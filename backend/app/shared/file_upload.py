"""
Shared file-upload validation, used by every upload endpoint (booking
attachments, expense bill attachments, housekeeping photos, maintenance
photos). Centralized so the limit is enforced consistently rather than
copy-pasted (or, worse, forgotten) at each of the four call sites.
"""
from fastapi import HTTPException, UploadFile

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif")


async def read_and_validate_upload(file: UploadFile, allowed_content_types: tuple[str, ...] | None = None) -> bytes:
    """
    Reads the full upload into memory and enforces the size cap. Raises
    413 (Payload Too Large) rather than silently truncating.

    allowed_content_types, when given, rejects anything outside that list
    with 415 (Unsupported Media Type) — used on the housekeeping/
    maintenance photo endpoints (which are explicitly "photos", not
    general attachments) to stop someone uploading an arbitrary file
    (e.g. an HTML or executable file) through a field the UI presents as
    an image upload. Left unset for booking/expense attachments, since
    those are genuinely meant to accept documents (PDFs, scanned IDs,
    invoices), not just images.
    """
    if allowed_content_types is not None and file.content_type not in allowed_content_types:
        raise HTTPException(415, f"Unsupported file type '{file.content_type}'. Allowed: {', '.join(allowed_content_types)}")
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(413, f"File too large - maximum size is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB")
    if len(contents) == 0:
        raise HTTPException(400, "Uploaded file is empty")
    return contents
