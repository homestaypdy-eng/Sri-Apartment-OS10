"""
Shared file-upload validation, used by every upload endpoint (booking
attachments, expense bill attachments, housekeeping photos, maintenance
photos, guest ID documents). Centralized so the limit is enforced
consistently rather than copy-pasted (or, worse, forgotten) at each call
site.
"""
from fastapi import HTTPException, UploadFile

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif")

# OS10 Phase 3 Step 3A: real file-signature (magic-byte) checks, not just
# the client-declared Content-Type header (trivially spoofable — the exact
# gap the storage/security audit found for guest ID document uploads).
# Hand-rolled rather than a new dependency (e.g. python-magic): these
# signatures are simple, stable, and cover exactly what a scanned ID
# document legitimately is — a photo or a PDF. A content type with no
# entry here is not signature-checked (verify_signature only rejects a
# CONFIRMED mismatch; it never blocks a type this table doesn't know).
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/pdf": (b"%PDF-",),
}

# Stored file extension is derived from the VALIDATED content type, never
# from the client-supplied filename — closes the "attacker chooses the
# extension" gap (e.g. uploading real image bytes but naming the file
# "id.html" would previously still be stored with a .html extension).
EXTENSION_BY_CONTENT_TYPE: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


def _signature_matches(contents: bytes, content_type: str) -> bool:
    if content_type == "image/webp":
        # RIFF....WEBP — the four-byte format tag sits at a fixed offset
        # after the 4-byte RIFF chunk size field, not at the very start.
        return contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
    signatures = _SIGNATURES.get(content_type)
    if signatures is None:
        return True
    return any(contents.startswith(sig) for sig in signatures)


async def read_and_validate_upload(
    file: UploadFile,
    allowed_content_types: tuple[str, ...] | None = None,
    verify_signature: bool = False,
) -> bytes:
    """
    Reads the full upload into memory and enforces the size cap. Raises
    413 (Payload Too Large) rather than silently truncating.

    allowed_content_types, when given, rejects anything outside that list
    with 415 (Unsupported Media Type) — used on the housekeeping/
    maintenance photo endpoints (which are explicitly "photos", not
    general attachments) and on guest ID document uploads, to stop
    someone uploading an arbitrary file (e.g. an HTML or SVG file) through
    a field the UI presents as an image/document upload. Left unset for
    booking/expense attachments, since those are genuinely meant to accept
    a wider range of documents.

    verify_signature, when True (used for guest ID documents, the most
    sensitive upload in the system), additionally checks the actual file
    bytes against a real signature for the CLIENT-DECLARED content type —
    rejecting with 415 if they don't match, rather than trusting the
    header alone. This does not widen what's accepted; it only catches a
    declared type whose bytes don't actually match it (e.g. an HTML file
    relabeled as image/jpeg).
    """
    if allowed_content_types is not None and file.content_type not in allowed_content_types:
        raise HTTPException(415, f"Unsupported file type '{file.content_type}'. Allowed: {', '.join(allowed_content_types)}")
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(413, f"File too large - maximum size is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB")
    if len(contents) == 0:
        raise HTTPException(400, "Uploaded file is empty")
    if verify_signature and file.content_type is not None and not _signature_matches(contents, file.content_type):
        raise HTTPException(415, f"File content does not match its declared type '{file.content_type}'")
    return contents
