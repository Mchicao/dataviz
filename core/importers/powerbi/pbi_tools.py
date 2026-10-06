"""Optional bridge from native PBIX files to DataVIZ's SourceAST.

The native PBIX container is proprietary. This module deliberately delegates
extraction to the user-installed ``pbi-tools`` executable and only consumes its
source-control ``PbixProj`` output. It never embeds credentials or parses
arbitrary SQL from user input.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_EXTRACTED_PATH_RE = re.compile(r"^Extracting PBIX file to:\s*(.+?)\s*$", re.MULTILINE)


class PbiToolsError(RuntimeError):
    """Raised when pbi-tools cannot extract a PBIX source."""


def build_extract_command(
    executable: str | Path,
    pbix_path: str | Path,
    desktop_pid: int | None = None,
    *,
    extraction_dir: str | Path | None = None,
    model_serialization: str = "Raw",
) -> list[str]:
    """Build an offline or Desktop-backed ``pbi-tools extract`` command.

    Native PBIX files can be extracted offline by omitting ``desktop_pid``.
    Raw serialization preserves the complete TOM payload and avoids depending
    on a running graphical Power BI Desktop instance.
    """
    if desktop_pid is not None and desktop_pid <= 0:
        raise ValueError("desktop_pid must be a positive process id")
    command = [str(Path(executable)), "extract", str(Path(pbix_path))]
    if desktop_pid is not None:
        command.append(str(desktop_pid))
        return command
    if extraction_dir is not None:
        command.extend(("-extractFolder", str(Path(extraction_dir))))
    command.extend(("-modelSerialization", model_serialization))
    return command


def _parse_extraction_path(output: str, pbix_path: Path) -> Path:
    match = _EXTRACTED_PATH_RE.search(output)
    candidate = Path(match.group(1).strip()) if match else pbix_path.with_suffix("")
    if not candidate.is_dir():
        raise PbiToolsError(
            f"pbi-tools terminó sin error, pero no se encontró la carpeta PbixProj: {candidate}"
        )
    return candidate


def extract_pbix(
    pbix_path: str | Path,
    *,
    executable: str | Path,
    desktop_pid: int | None = None,
    extraction_dir: str | Path | None = None,
    timeout_seconds: int = 900,
) -> Path:
    """Extract ``pbix_path`` with pbi-tools and return a PbixProj directory.

    The default path extracts offline with Raw model serialization. Supplying
    ``desktop_pid`` retains the explicit Desktop-backed compatibility path.
    """
    pbix = Path(pbix_path).resolve()
    tool = Path(executable).resolve()
    if not pbix.is_file() or pbix.suffix.lower() != ".pbix":
        raise FileNotFoundError(f"PBIX source not found: {pbix_path}")
    if not tool.is_file():
        raise FileNotFoundError(f"pbi-tools executable not found: {executable}")

    destination = Path(extraction_dir).resolve() if extraction_dir is not None else None
    if destination is not None and destination.exists():
        raise FileExistsError(f"Extraction destination already exists: {destination}")
    command = build_extract_command(
        tool,
        pbix,
        desktop_pid,
        extraction_dir=destination if desktop_pid is None else None,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise PbiToolsError(f"pbi-tools excedió {timeout_seconds}s al extraer {pbix.name}") from exc
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise PbiToolsError(
            f"pbi-tools falló con código {result.returncode} al extraer {pbix.name}. "
            "Revisa el formato del archivo y la salida de pbi-tools."
        )

    extracted = _parse_extraction_path(output, pbix)
    if destination is None or extracted.resolve() == destination:
        return extracted
    shutil.copytree(extracted, destination)
    return destination


__all__ = ["PbiToolsError", "build_extract_command", "extract_pbix"]
