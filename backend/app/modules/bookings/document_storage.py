"""Private storage adapters for sensitive GuestDocument files.

Local and Railway-volume records use a private mounted filesystem. New
production deployments should use a private S3-compatible bucket: object
storage is shared across backend replicas and is not tied to an ephemeral
container filesystem. No adapter returns a public or presigned URL.

Legacy rows without storage metadata continue to resolve through file_path;
this module never migrates or removes those files implicitly.
"""
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException

STORAGE_ROOT = Path(os.environ.get("GUEST_DOCUMENT_STORAGE_DIR", "/tmp/sri_apartment_guest_documents"))
STORAGE_BACKEND = os.environ.get("GUEST_DOCUMENT_STORAGE_BACKEND", "local")
SUPPORTED_BACKENDS = {"local", "railway_volume", "s3"}
S3_BUCKET = os.environ.get("GUEST_DOCUMENT_S3_BUCKET", "")
S3_REGION = os.environ.get("GUEST_DOCUMENT_S3_REGION", "")
S3_ENDPOINT_URL = os.environ.get("GUEST_DOCUMENT_S3_ENDPOINT_URL", "") or None
S3_ADDRESSING_STYLE = os.environ.get("GUEST_DOCUMENT_S3_ADDRESSING_STYLE", "auto")
S3_ACCESS_KEY_ID = os.environ.get("GUEST_DOCUMENT_S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.environ.get("GUEST_DOCUMENT_S3_SECRET_ACCESS_KEY", "")
S3_SERVER_SIDE_ENCRYPTION = os.environ.get("GUEST_DOCUMENT_S3_SERVER_SIDE_ENCRYPTION", "AES256")


def validate_production_storage_config(
    *, environment: str | None = None, backend: str | None = None,
    root: str | Path | None = None, bucket: str | None = None,
    region: str | None = None, access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> None:
    """Fail closed in production unless private ID storage is configured durably."""
    if (environment if environment is not None else os.environ.get("ENVIRONMENT", "development")) != "production":
        return
    selected_backend = backend if backend is not None else STORAGE_BACKEND
    configured_root = Path(root if root is not None else STORAGE_ROOT)
    selected_root = configured_root.resolve()
    if selected_backend == "local":
        raise RuntimeError("STARTUP BLOCKED: production guest ID documents require durable storage; local storage is not allowed.")
    if selected_backend == "railway_volume":
        temporary_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve(), Path("/var/tmp").resolve()}
        if any(selected_root == temp_root or temp_root in selected_root.parents for temp_root in temporary_roots):
            raise RuntimeError("STARTUP BLOCKED: production guest ID documents cannot use an ephemeral temporary directory.")
        if not configured_root.is_absolute():
            raise RuntimeError("STARTUP BLOCKED: configure an absolute durable guest document volume path.")
        return
    if selected_backend == "s3":
        configured_bucket = bucket if bucket is not None else S3_BUCKET
        configured_region = region if region is not None else S3_REGION
        configured_access = access_key_id if access_key_id is not None else S3_ACCESS_KEY_ID
        configured_secret = secret_access_key if secret_access_key is not None else S3_SECRET_ACCESS_KEY
        if (
            not configured_bucket or not configured_region
            or bool(configured_access) != bool(configured_secret)
            or S3_ADDRESSING_STYLE not in {"auto", "path", "virtual"}
        ):
            raise RuntimeError("STARTUP BLOCKED: configure a durable S3-compatible guest document bucket and region.")
        return
    raise RuntimeError("STARTUP BLOCKED: production guest ID document storage backend is unsupported.")


class StorageUnavailable(Exception):
    """The configured storage backend could not complete an operation."""


class StorageNotFound(Exception):
    """The metadata exists but its stored object is missing."""


def _root_for_backend(backend: str) -> Path:
    if backend not in {"local", "railway_volume"}:
        raise StorageUnavailable("Filesystem storage backend is not configured")
    return STORAGE_ROOT


def new_storage_key(extension: str) -> str:
    """Create an opaque server-side key; callers never pass client names."""
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        raise ValueError("Invalid storage extension")
    return f"{uuid.uuid4().hex}{extension}"


def _validate_key(key: str) -> None:
    # Backend-boundary validation also prevents a malformed database value
    # from escaping a mounted private directory or addressing arbitrary keys.
    if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,8}", key):
        raise StorageNotFound("Guest document file not found")


def _path_for(backend: str, key: str) -> Path:
    _validate_key(key)
    return _root_for_backend(backend) / key


def storage_path(backend: str, key: str) -> Path:
    """Resolve a key only for filesystem backends (legacy callers/tests)."""
    return _path_for(backend, key)


def _s3_client():
    if (
        not S3_BUCKET or not S3_REGION
        or bool(S3_ACCESS_KEY_ID) != bool(S3_SECRET_ACCESS_KEY)
        or S3_ADDRESSING_STYLE not in {"auto", "path", "virtual"}
    ):
        raise StorageUnavailable("Object storage is not configured")
    try:
        import boto3
        from botocore.config import Config

        options = {
            "region_name": S3_REGION,
            "config": Config(s3={"addressing_style": S3_ADDRESSING_STYLE}),
        }
        if S3_ENDPOINT_URL:
            options["endpoint_url"] = S3_ENDPOINT_URL
        # When both are unset, use the SDK's standard workload/role
        # credential chain. Never require credentials in local config.
        if S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY:
            options["aws_access_key_id"] = S3_ACCESS_KEY_ID
            options["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY
        return boto3.client("s3", **options)
    except Exception:
        raise StorageUnavailable("Object storage is not available") from None


def _is_missing_object(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return isinstance(error, FileNotFoundError)
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"NoSuchKey", "NotFound", "404"} or status == 404


def _s3_operation(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Exception as error:
        if _is_missing_object(error):
            raise StorageNotFound("Guest document file not found") from None
        raise StorageUnavailable("Object storage operation failed") from None


def store_new_document(contents: bytes, extension: str, content_type: str | None = None) -> tuple[str, str]:
    backend = STORAGE_BACKEND
    if backend not in SUPPORTED_BACKENDS:
        raise StorageUnavailable("Guest document storage is not configured")
    key = new_storage_key(extension)

    if backend == "s3":
        client = _s3_client()
        parameters = {"Bucket": S3_BUCKET, "Key": key, "Body": contents}
        if content_type:
            parameters["ContentType"] = content_type
        if S3_SERVER_SIDE_ENCRYPTION:
            parameters["ServerSideEncryption"] = S3_SERVER_SIDE_ENCRYPTION
        try:
            _s3_operation(client.put_object, **parameters)
        except StorageUnavailable:
            # A timeout can be ambiguous: the provider may have accepted the
            # full object before the client lost its response. Best-effort
            # removal prevents an unreferenced identity document lingering.
            try:
                client.delete_object(Bucket=S3_BUCKET, Key=key)
            except Exception:
                pass
            raise
        return backend, key

    root = _root_for_backend(backend)
    path = _path_for(backend, key)
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stored_file:
            stored_file.write(contents)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageUnavailable("Guest document storage is not available") from None
    return backend, key


def document_backend_and_key(document) -> tuple[str, str] | None:
    if document.storage_backend and document.storage_key:
        _validate_key(document.storage_key)
        return document.storage_backend, document.storage_key
    return None


def resolve_document_path(document) -> Path:
    """Filesystem-only resolver retained for legacy code and local tests."""
    metadata = document_backend_and_key(document)
    if metadata:
        backend, key = metadata
        if backend not in {"local", "railway_volume"}:
            raise StorageUnavailable("Document is not stored on the filesystem")
        return _path_for(backend, key)
    if document.file_path:
        root = STORAGE_ROOT.resolve()
        legacy_path = Path(document.file_path).resolve()
        if not legacy_path.is_relative_to(root) or legacy_path == root:
            raise StorageNotFound("Guest document file not found")
        return legacy_path
    raise StorageNotFound("Guest document file not found")


def exists(document) -> bool:
    metadata = document_backend_and_key(document)
    if metadata:
        backend, key = metadata
        if backend == "s3":
            client = _s3_client()
            try:
                client.head_object(Bucket=S3_BUCKET, Key=key)
                return True
            except Exception as error:
                if _is_missing_object(error):
                    return False
                raise StorageUnavailable("Object storage operation failed") from None
    try:
        return resolve_document_path(document).is_file()
    except StorageNotFound:
        return False


def open_document(document) -> BinaryIO:
    metadata = document_backend_and_key(document)
    if metadata:
        backend, key = metadata
        if backend == "s3":
            client = _s3_client()
            response = _s3_operation(client.get_object, Bucket=S3_BUCKET, Key=key)
            return response["Body"]
    path = resolve_document_path(document)
    try:
        return path.open("rb")
    except FileNotFoundError:
        raise StorageNotFound("Guest document file not found") from None
    except OSError:
        raise StorageUnavailable("Guest document storage is not available") from None


def delete_stored_object(backend: str, key: str) -> None:
    _validate_key(key)
    if backend == "s3":
        client = _s3_client()
        _s3_operation(client.delete_object, Bucket=S3_BUCKET, Key=key)
        return
    path = _path_for(backend, key)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise StorageUnavailable("Could not delete guest document file") from None


def delete_document_file(document) -> None:
    metadata = document_backend_and_key(document)
    if metadata:
        delete_stored_object(*metadata)
        return
    if document.file_path:
        try:
            Path(document.file_path).unlink(missing_ok=True)
        except OSError:
            raise StorageUnavailable("Could not delete guest document file") from None
        return
    raise StorageNotFound("Guest document file not found")
