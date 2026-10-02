"""
IPv6-first SMTP connection optimization (notifications.service._PreferIPv6SMTP).

Root-cause fix for a real production incident: Railway's outbound network
could reach Gmail's SMTP frontend over IPv6 immediately, but IPv4 to the
exact same hostname timed out — and Python's synchronous socket API tries
every getaddrinfo() result in DNS's own order with no Happy-Eyeballs-style
racing, so the default smtplib.SMTP always burned the full connect timeout
on the failing IPv4 address before ever trying the working IPv6 one.

These tests use only real local loopback sockets (127.0.0.1 / ::1) with a
mocked socket.getaddrinfo() — no real network, no real Gmail, no email
sent, no SMTP authentication anywhere in this file. See conftest.py's
own _fail_fast_on_real_smtp_connections fixture for why every other test
in this suite gets a fast-failing _get_socket by default; the tests here
that need to observe REAL (loopback-only) connection behavior call a
reference to the original _get_socket captured at import time, before
that autouse fixture ever patches it — see _REAL_GET_SOCKET below.

Run with: pytest tests/test_smtp_ipv6_preference.py -v
"""
import inspect
import re
import socket
from unittest.mock import patch

import pytest

from app.modules.notifications.service import _PreferIPv6SMTP, send_email

# Captured at import time, before conftest.py's autouse
# _fail_fast_on_real_smtp_connections fixture ever runs and patches
# _PreferIPv6SMTP._get_socket for every test in this suite (by design —
# see that fixture's own docstring). Tests below that want to exercise
# the REAL connection-preference logic (against local loopback sockets
# only) call this captured reference directly instead of
# client._get_socket(...), which would otherwise hit the fixture's
# fast-fail stand-in like every other test correctly does.
_REAL_GET_SOCKET = _PreferIPv6SMTP._get_socket


def _listening_ipv4_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


def _listening_ipv6_socket():
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.bind(("::1", 0))
    s.listen(1)
    return s


def _closed_ipv6_port():
    """A loopback IPv6 address/port nothing is listening on — connecting
    to it fails fast (connection refused), simulating "IPv6 connection
    fails" without a real multi-second timeout in the test suite."""
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.bind(("::1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# A: IPv6 attempted first when DNS returns both families
# ---------------------------------------------------------------------------

def test_prefers_ipv6_when_both_families_resolve():
    ipv4_listener = _listening_ipv4_socket()
    ipv6_listener = _listening_ipv6_socket()
    try:
        ipv4_addr = ipv4_listener.getsockname()
        ipv6_addr = ipv6_listener.getsockname()

        # DNS's own order (IPv4 first) — exactly what smtp.gmail.com
        # actually returns in production, per the read-only diagnostic.
        fake_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ipv4_addr),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ipv6_addr),
        ]
        with patch("socket.getaddrinfo", return_value=fake_infos) as mock_getaddrinfo:
            client = _PreferIPv6SMTP.__new__(_PreferIPv6SMTP)
            result_sock = _REAL_GET_SOCKET(client, "smtp.gmail.com", 587, 5)
            try:
                # Both listeners are up — if IPv4 (listed first by DNS)
                # had been tried first, it would have succeeded and this
                # would be an AF_INET socket. Getting AF_INET6 back proves
                # IPv6 was attempted (and won) ahead of it.
                assert result_sock.family == socket.AF_INET6
            finally:
                result_sock.close()
            mock_getaddrinfo.assert_called_once_with("smtp.gmail.com", 587, 0, socket.SOCK_STREAM)
    finally:
        ipv4_listener.close()
        ipv6_listener.close()


# ---------------------------------------------------------------------------
# B: IPv4 fallback when IPv6 fails
# ---------------------------------------------------------------------------

def test_falls_back_to_ipv4_when_ipv6_connection_fails():
    ipv4_listener = _listening_ipv4_socket()
    try:
        ipv4_addr = ipv4_listener.getsockname()
        dead_ipv6_port = _closed_ipv6_port()

        fake_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ipv4_addr),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", dead_ipv6_port, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            client = _PreferIPv6SMTP.__new__(_PreferIPv6SMTP)
            result_sock = _REAL_GET_SOCKET(client, "smtp.gmail.com", 587, 5)
            try:
                assert result_sock.family == socket.AF_INET
                assert result_sock.getpeername()[:2] == ipv4_addr
            finally:
                result_sock.close()
    finally:
        ipv4_listener.close()


# ---------------------------------------------------------------------------
# C: unchanged behavior when only IPv4 is returned (e.g. no IPv6 egress —
# every local/dev machine, most CI, and production before IPv6 was enabled)
# ---------------------------------------------------------------------------

def test_works_normally_when_only_ipv4_is_returned():
    ipv4_listener = _listening_ipv4_socket()
    try:
        ipv4_addr = ipv4_listener.getsockname()
        fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ipv4_addr)]
        with patch("socket.getaddrinfo", return_value=fake_infos):
            client = _PreferIPv6SMTP.__new__(_PreferIPv6SMTP)
            result_sock = _REAL_GET_SOCKET(client, "smtp.gmail.com", 587, 5)
            try:
                assert result_sock.family == socket.AF_INET
                assert result_sock.getpeername()[:2] == ipv4_addr
            finally:
                result_sock.close()
    finally:
        ipv4_listener.close()


def test_raises_the_real_underlying_error_when_every_address_fails():
    """Same failure surfaced to callers as smtplib's own default
    behavior — send_email()'s existing except-clause callers
    (send_customer_login_otp_email etc.) are unaffected."""
    dead_port = _closed_ipv6_port()
    fake_infos = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", dead_port, 0, 0))]
    with patch("socket.getaddrinfo", return_value=fake_infos):
        client = _PreferIPv6SMTP.__new__(_PreferIPv6SMTP)
        with pytest.raises(OSError):
            _REAL_GET_SOCKET(client, "smtp.gmail.com", 587, 5)


# ---------------------------------------------------------------------------
# D: DNS stays dynamic — never a hardcoded Gmail/Google IP address
# ---------------------------------------------------------------------------

def test_resolution_is_dynamic_not_hardcoded():
    """Proves _get_socket calls socket.getaddrinfo with the ORIGINAL
    hostname argument every time — no cached/hardcoded IP short-circuits
    it — and that no IP-literal appears anywhere in its own source."""
    ipv4_listener = _listening_ipv4_socket()
    try:
        ipv4_addr = ipv4_listener.getsockname()
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ipv4_addr)]) as mock_getaddrinfo:
            client = _PreferIPv6SMTP.__new__(_PreferIPv6SMTP)
            _REAL_GET_SOCKET(client, "smtp.gmail.com", 587, 5).close()
            # The literal hostname string, unresolved, is what reaches
            # getaddrinfo — proving resolution happens fresh, in this
            # call, rather than against some pre-resolved constant.
            args, _ = mock_getaddrinfo.call_args
            assert args[0] == "smtp.gmail.com"
    finally:
        ipv4_listener.close()

    source = inspect.getsource(_PreferIPv6SMTP)
    ipv4_literal = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    ipv6_literal = re.compile(r"\b[0-9a-fA-F]{1,4}(:[0-9a-fA-F]{0,4}){2,7}\b")
    assert not ipv4_literal.search(source), "found a hardcoded IPv4 address in _PreferIPv6SMTP's source"
    assert not ipv6_literal.search(source), "found a hardcoded IPv6 address in _PreferIPv6SMTP's source"


# ---------------------------------------------------------------------------
# E: existing test-safety net still prevents any REAL network connection —
# for both the plain smtplib.SMTP path and the new _PreferIPv6SMTP path,
# via conftest.py's (updated) autouse _fail_fast_on_real_smtp_connections.
# ---------------------------------------------------------------------------

def test_fail_fast_fixture_still_intercepts_the_new_smtp_class(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.settings.smtp_host", "smtp.gmail.com")
    monkeypatch.setattr("app.modules.notifications.service.settings.smtp_port", 587)

    with pytest.raises(OSError, match="test-environment"):
        send_email("someone@example.com", "subject", "body")


def test_fail_fast_fixture_intercepts_direct_instantiation_too():
    """Belt-and-suspenders: even a caller that reaches for
    _PreferIPv6SMTP directly (bypassing send_email) still can't make a
    real connection during tests."""
    with pytest.raises(OSError, match="test-environment"):
        _PreferIPv6SMTP("smtp.gmail.com", 587, timeout=5)
