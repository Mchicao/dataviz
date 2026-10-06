"""Upload safety boundary for the DataVIZ SaaS service.

Fail-closed validation of multipart upload metadata and streamed byte payloads.
It enforces per-file and aggregate size caps, file-count caps, a configurable
content-type allowlist, flat (single-segment) filenames, and filename length,
delegating traversal/null-byte/ADS/device-name defenses to
:class:`~apps.dataviz_service.security.path_traversal.PathTraversalBoundary`.

The boundary is intentionally metadata-first: a request whose declared size or
content-type is illegal is rejected *before* any byte of the body is buffered,
which is the primary defense against memory-exhaustion DoS via oversized or
spoofed uploads. :func:`UploadBoundary.capped_reader` provides the streaming
backstop for when no trustworthy Content-Length is available.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import BinaryIO

from apps.dataviz_service.contracts import SanitizedServiceError
from apps.dataviz_service.security.path_traversal import PathTraversalBoundary, PathTraversalError

logger = logging.getLogger("dataviz_service.security.upload")


class UploadError(SanitizedServiceError):
    """Raised when an upload violates size, count, content-type, or filename rules."""

    def __init__(self, message: str = "Upload rejected by safety boundary.") -> None:
        super().__init__(message, status_code=413, error_code="UPLOAD_REJECTED")


#: Conservative default content-type allowlist for DataVIZ ingestion artifacts.
#: Tableau workbooks, Power BI / PBIP, JSON manifests, and ZIP containers (twbx/pbit).
DEFAULT_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/zip",
        "application/octet-stream",
        "application/x-tableau-workbook",
        "application/x-pbip",
        "text/xml",
        "text/plain",
    }
)

#: Default per-file size cap: 50 MB. Mirrors the ZIP cap order of magnitude in
#: :func:`PathTraversalBoundary.validate_zip_archive` for consistency.
DEFAULT_MAX_INDIVIDUAL_SIZE_BYTES: int = 50 * 1024 * 1024

#: Default aggregate size cap across a whole upload batch: 200 MB.
DEFAULT_MAX_TOTAL_SIZE_BYTES: int = 200 * 1024 * 1024

#: Default maximum number of files in a single upload request.
DEFAULT_MAX_FILE_COUNT: int = 50

#: Default maximum filename length (bytes in the UTF-8 basename).
DEFAULT_MAX_FILENAME_LENGTH: int = 255


class _FileMeta:
    """Internal normalized metadata for a single uploaded file."""

    __slots__ = ("filename", "content_type", "size")

    def __init__(self, filename: str, content_type: str, size: int) -> None:
        self.filename = filename
        self.content_type = content_type
        self.size = size


class UploadBoundary:
    """Stateless, fail-closed validator for uploaded file metadata and streams."""

    @staticmethod
    def validate_filename(filename: str, *, max_length: int = DEFAULT_MAX_FILENAME_LENGTH) -> str:
        """Validate a single upload filename and return the cleaned basename.

        Upload filenames MUST be flat (a single path segment): any path
        separator, traversal, null byte, ADS, or reserved device name is rejected
        via :class:`PathTraversalBoundary`. Empty names are rejected. The check
        is defense-in-depth on top of traversal sanitization, because uploaded
        filenames are attacker-controlled and frequently written to disk verbatim.
        """
        if not isinstance(filename, str) or not filename.strip():
            raise UploadError("Upload filename must not be empty.")

        cleaned = filename.strip()

        # Delegate traversal / null-byte / ADS / UNC / device-name hardening.
        # Wrap the delegated error so the upload boundary exposes a single
        # UploadError contract to its callers (defense-in-depth, fail-closed).
        try:
            safe = PathTraversalBoundary.sanitize_relative_path(cleaned)
        except PathTraversalError as exc:
            logger.warning("Upload boundary: unsafe filename '%s'", cleaned)
            raise UploadError("Upload filename failed traversal hardening.") from exc

        # Upload filenames must be a single flat segment: the sanitizer already
        # rejected ".." and absolute paths, but a benign-looking "a/b" is still
        # a path. Reject any separator so the name can be used as a basename.
        if "/" in safe:
            logger.warning("Upload boundary: path separator in filename '%s'", cleaned)
            raise UploadError("Upload filename must not contain path separators.")

        if len(safe.encode("utf-8")) > max_length:
            logger.warning("Upload boundary: filename length exceeds cap (%d)", max_length)
            raise UploadError(f"Upload filename exceeds maximum length of {max_length} bytes.")

        return safe

    @staticmethod
    def _normalize_content_type(content_type: str) -> str:
        """Return the content type without parameters, lowercased.

        e.g. ``text/plain; charset=utf-8`` -> ``text/plain``.
        """
        if not isinstance(content_type, str) or not content_type.strip():
            raise UploadError("Content-Type must not be empty.")

        if "\x00" in content_type or "\r" in content_type or "\n" in content_type:
            logger.warning("Upload boundary: control character in Content-Type")
            raise UploadError("Control characters in Content-Type are forbidden.")

        # Strip RFC 7231 parameters (everything after ';') and lowercase.
        return content_type.split(";", 1)[0].strip().lower()

    @classmethod
    def validate_file_metadata(
        cls,
        filename: str,
        content_type: str,
        declared_size: int,
        *,
        allowed_content_types: Iterable[str] = DEFAULT_ALLOWED_CONTENT_TYPES,
        max_individual_size: int = DEFAULT_MAX_INDIVIDUAL_SIZE_BYTES,
        max_filename_length: int = DEFAULT_MAX_FILENAME_LENGTH,
    ) -> _FileMeta:
        """Validate one file's metadata before any body byte is buffered.

        Fail-closed: every dimension (filename, content-type, declared size) must
        pass. ``declared_size`` may be ``-1`` only as a sentinel meaning
        "unknown"; in that case the caller MUST additionally wrap the stream in
        :func:`capped_reader` to enforce the size cap at ingest time.
        """
        safe_name = cls.validate_filename(filename, max_length=max_filename_length)
        normalized_ct = cls._normalize_content_type(content_type)

        allowed = {c.strip().lower() for c in allowed_content_types}
        if normalized_ct not in allowed:
            logger.warning(
                "Upload boundary: Content-Type '%s' not in allowlist %s",
                normalized_ct,
                sorted(allowed),
            )
            raise UploadError("Content-Type is not permitted by upload policy.")

        if not isinstance(declared_size, int) or isinstance(declared_size, bool):
            raise UploadError("Declared upload size must be an integer.")

        # Negative is allowed ONLY as an explicit "unknown size" sentinel; the
        # caller is then required to enforce the cap via capped_reader.
        if declared_size < -1:
            raise UploadError("Declared upload size is invalid.")

        if declared_size > max_individual_size:
            logger.warning(
                "Upload boundary: declared size %d exceeds per-file cap %d",
                declared_size,
                max_individual_size,
            )
            raise UploadError(f"Upload exceeds per-file size limit of {max_individual_size} bytes.")

        return _FileMeta(filename=safe_name, content_type=normalized_ct, size=declared_size)

    @classmethod
    def validate_batch(
        cls,
        files: Iterable[tuple[str, str, int]],
        *,
        allowed_content_types: Iterable[str] = DEFAULT_ALLOWED_CONTENT_TYPES,
        max_individual_size: int = DEFAULT_MAX_INDIVIDUAL_SIZE_BYTES,
        max_total_size: int = DEFAULT_MAX_TOTAL_SIZE_BYTES,
        max_file_count: int = DEFAULT_MAX_FILE_COUNT,
        max_filename_length: int = DEFAULT_MAX_FILENAME_LENGTH,
    ) -> list[_FileMeta]:
        """Validate an upload batch for per-file, aggregate-size and count limits.

        ``files`` is an iterable of ``(filename, content_type, declared_size)``
        tuples. Raises :class:`UploadError` on the first violation (fail-closed,
        fail-fast) so no partial oversized batch is accepted.
        """
        accepted: list[_FileMeta] = []
        total = 0

        for index, entry in enumerate(files):
            if not isinstance(entry, tuple) or len(entry) != 3:
                raise UploadError(f"Upload entry {index} is malformed.")
            filename, content_type, declared_size = entry
            meta = cls.validate_file_metadata(
                filename,
                content_type,
                declared_size,
                allowed_content_types=allowed_content_types,
                max_individual_size=max_individual_size,
                max_filename_length=max_filename_length,
            )

            # Only fold known sizes into the running total; an unknown (-1) size
            # must be enforced per-stream by the caller via capped_reader.
            if meta.size >= 0:
                total += meta.size
                if total > max_total_size:
                    logger.warning(
                        "Upload boundary: aggregate size %d exceeds batch cap %d",
                        total,
                        max_total_size,
                    )
                    raise UploadError(
                        f"Upload batch exceeds total size limit of {max_total_size} bytes."
                    )

            accepted.append(meta)
            if len(accepted) > max_file_count:
                logger.warning(
                    "Upload boundary: file count %d exceeds cap %d",
                    len(accepted),
                    max_file_count,
                )
                raise UploadError(f"Upload batch exceeds file count limit of {max_file_count}.")

        return accepted

    @staticmethod
    def capped_reader(
        stream: BinaryIO, max_bytes: int, *, chunk_size: int = 64 * 1024
    ) -> Iterator[bytes]:
        """Yield at most ``max_bytes`` bytes from ``stream``; raise on overflow.

        Streaming backstop used when Content-Length is absent, untrusted, or when
        the declared size is the "unknown" sentinel (-1). Reads one extra chunk
        past the cap to detect a spoofed/undersized declaration and fails closed.
        """
        if max_bytes < 0:
            raise UploadError("max_bytes must not be negative.")
        if chunk_size <= 0:
            raise UploadError("chunk_size must be positive.")

        consumed = 0
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > max_bytes:
                logger.warning("Upload boundary: stream overflow %d > cap %d", consumed, max_bytes)
                raise UploadError(f"Upload stream exceeded maximum size of {max_bytes} bytes.")
            yield chunk
