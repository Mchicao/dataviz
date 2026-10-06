"""Path and ZIP traversal security boundary.

Prevents path traversal attacks (directory traversal, absolute path escape,
Windows device names, null bytes) and ZIP archive expansion vulnerabilities
(zip bombs, directory traversal entries, symlink creation).
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from typing import BinaryIO

from apps.dataviz_service.contracts import SanitizedServiceError

logger = logging.getLogger("dataviz_service.security.path_traversal")


class PathTraversalError(SanitizedServiceError):
    """Raised when a file path or zip entry violates safety limits."""

    def __init__(self, message: str = "Path traversal or zip safety violation detected.") -> None:
        super().__init__(message, status_code=400, error_code="PATH_TRAVERSAL_DETECTED")


# Reserved Windows device names that must never be created or opened
WINDOWS_RESERVED_NAMES: set[str] = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

_PATH_SEPARATORS_RE = re.compile(r"[/\\]+")


class PathTraversalBoundary:
    """Stateless boundary for validating relative paths and ZIP archives."""

    @staticmethod
    def sanitize_relative_path(path: str) -> str:
        """Validate and return a normalized relative path string.

        Raises :class:`PathTraversalError` if null bytes, directory traversal
        (``..``), absolute paths, UNC paths, alternate data streams, or reserved
        device names are detected.
        """
        if not isinstance(path, str) or not path.strip():
            raise PathTraversalError("Path must not be empty.")

        # Reject null bytes
        if "\x00" in path:
            logger.warning("Path traversal boundary: null byte detected in path")
            raise PathTraversalError("Null bytes in path are forbidden.")

        raw_path = path.strip()

        # Reject Windows UNC paths or device paths
        if (
            raw_path.startswith("\\\\")
            or raw_path.startswith("//")
            or raw_path.startswith("\\\\?\\")
            or raw_path.startswith("\\\\.\\")
        ):
            logger.warning("Path traversal boundary: UNC or device path detected '%s'", path)
            raise PathTraversalError("UNC and device paths are forbidden.")

        cleaned = raw_path.replace("\\", "/")

        # Check absolute paths
        if cleaned.startswith("/") or re.match(r"^[a-zA-Z]:", cleaned):
            logger.warning("Path traversal boundary: absolute path detected '%s'", path)
            raise PathTraversalError("Absolute paths are forbidden.")

        parts = [p for p in cleaned.split("/") if p and p != "."]

        for part in parts:
            if part == "..":
                logger.warning("Path traversal boundary: directory traversal '..' detected")
                raise PathTraversalError("Directory traversal ('..') is forbidden.")

            # Reject Alternate Data Streams (ADS) on Windows (e.g. file.txt:$DATA)
            if ":" in part:
                logger.warning(
                    "Path traversal boundary: alternate data stream ':' detected in '%s'", part
                )
                raise PathTraversalError("Alternate Data Streams (':') in path are forbidden.")

            stem = part.split(".")[0].upper()
            if stem in WINDOWS_RESERVED_NAMES:
                logger.warning("Path traversal boundary: reserved Windows name '%s'", part)
                raise PathTraversalError(f"Reserved device name '{part}' is forbidden.")

        return "/".join(parts)

    @classmethod
    def safe_join(cls, base_dir: str | Path, target_path: str | Path) -> Path:
        """Safely join ``base_dir`` and ``target_path``.

        Ensures the resolved path stays strictly within ``base_dir``.
        """
        base = Path(base_dir).resolve()
        cleaned_rel = cls.sanitize_relative_path(str(target_path))
        target = (base / cleaned_rel).resolve()

        try:
            target.relative_to(base)
        except ValueError as exc:
            logger.warning(
                "Path traversal boundary: resolved path '%s' escaped base '%s'", target, base
            )
            raise PathTraversalError("Path resolves outside base directory.") from exc

        return target

    @classmethod
    def validate_zip_archive(
        cls,
        archive_source: str | Path | BinaryIO,
        *,
        max_uncompressed_bytes: int = 100_000_000,  # 100 MB default cap
        max_files: int = 1000,
        max_ratio: float = 100.0,
    ) -> list[str]:
        """Validate a ZIP archive for zip bombs and traversal entries.

        Returns a list of safe entry relative path names.
        Raises :class:`PathTraversalError` on any violation.
        """
        try:
            zf = zipfile.ZipFile(archive_source, "r")
        except zipfile.BadZipFile as exc:
            raise PathTraversalError("Invalid or corrupted ZIP archive.") from exc

        with zf:
            infos = zf.infolist()

            if len(infos) > max_files:
                logger.warning("Zip boundary: entry count %d exceeds cap %d", len(infos), max_files)
                raise PathTraversalError(f"ZIP contains too many files (max {max_files}).")

            total_uncompressed = 0
            total_compressed = 0
            safe_entries: list[str] = []

            for info in infos:
                filename = info.filename
                # Reject symlink entries (UNIX file mode 0o120000)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    logger.warning("Zip boundary: symlink entry detected '%s'", filename)
                    raise PathTraversalError("Symbolic links in ZIP archives are forbidden.")
                # Check for traversal in zip entry name
                cleaned_entry = cls.sanitize_relative_path(filename)
                safe_entries.append(cleaned_entry)

                # Track sizes
                total_uncompressed += info.file_size
                total_compressed += info.compress_size

                if total_uncompressed > max_uncompressed_bytes:
                    logger.warning(
                        "Zip boundary: uncompressed size %d exceeds max %d",
                        total_uncompressed,
                        max_uncompressed_bytes,
                    )
                    raise PathTraversalError("ZIP uncompressed payload size limit exceeded.")

            if total_compressed > 0:
                ratio = total_uncompressed / total_compressed
                if ratio > max_ratio and total_uncompressed > 1_000_000:
                    logger.warning(
                        "Zip boundary: compression ratio %.1f exceeds cap %.1f", ratio, max_ratio
                    )
                    raise PathTraversalError("Zip bomb detected (excessive compression ratio).")
            elif total_uncompressed > 1_000_000:
                logger.warning(
                    "Zip boundary: uncompressed size %d with zero compressed size",
                    total_uncompressed,
                )
                raise PathTraversalError(
                    "Zip bomb detected (zero compressed size for non-empty payload)."
                )

            return safe_entries
