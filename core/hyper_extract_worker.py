"""Worker aislado para exportar extractos Hyper grandes."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from core.hyper_extract import _export_hyper_table_in_process


def main() -> int:
    """Exporta un Hyper y persiste sólo el esquema necesario por el padre."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hyper_path", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("preferred_table")
    parser.add_argument("manifest_path", type=Path)
    args = parser.parse_args()

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output_csv.with_name(
        f"{args.output_csv.name}.{uuid.uuid4().hex}.tmp"
    )
    args.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = args.manifest_path.with_name(
        f"{args.manifest_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        result = _export_hyper_table_in_process(
            args.hyper_path,
            temporary_output,
            args.preferred_table or None,
        )
        temporary_manifest.write_text(
            json.dumps({"column_types": result.column_types}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_output, args.output_csv)
        os.replace(temporary_manifest, args.manifest_path)
    finally:
        for temporary in (temporary_output, temporary_manifest):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
