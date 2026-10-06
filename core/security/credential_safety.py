"""Detect and redact credentials embedded in BI artifacts.

This module deliberately distinguishes useful connection metadata from secrets.
It never returns the matched value. Callers can persist the sanitized text/tree and
store only the finding metadata for a user-facing warning.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile

REDACTED = "[REDACTED]"
_MAX_SCAN_FILE_BYTES = 4 * 1024 * 1024
_TEXT_SUFFIXES = {
    ".json",
    ".xml",
    ".tmdl",
    ".m",
    ".pq",
    ".txt",
    ".yaml",
    ".yml",
    ".query",
    ".config",
    ".js",
    ".ts",
    ".csv",
}
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|authorization|sig)",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(
    r"^(?:\*+|x+|<[^>]+>|your[_ -].*|change[_ -].*|none|null|false|true|"
    r"credential\.current\(\)|environment\.[a-z_]+)$",
    re.IGNORECASE,
)

# Literal values in assignments, JSON-ish text, connection strings and URLs.
_QUOTED_ASSIGNMENT = re.compile(
    r"(?P<key>password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|authorization|sig)"
    r'["\']?\s*(?P<sep>[:=]\s*)(?P<quote>["\'])(?P<value>.*?)(?P=quote)',
    re.IGNORECASE,
)
_UNQUOTED_ASSIGNMENT = re.compile(
    r"(?P<key>password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|authorization|sig)"
    r"(?P<sep>\s*=\s*)(?P<value>[^;,&\s\]\}\)]+)",
    re.IGNORECASE,
)
_CONNECTION_STRING = re.compile(
    r"(?P<key>password|pwd|user\s+id|uid|secret|access[_ -]?token)"
    r"(?P<sep>\s*=\s*)(?P<value>[^;\r\n]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?P<prefix>\bBearer\s+)(?P<value>[A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE)


@dataclass(frozen=True)
class CredentialFinding:
    """Safe description of one possible embedded credential."""

    kind: str
    severity: str
    source: str
    line: int | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "source": self.source,
            "line": self.line,
            "message": self.message,
        }


@dataclass(frozen=True)
class CredentialScanReport:
    """Sanitized inspection result suitable for persistence and API responses."""

    findings: tuple[CredentialFinding, ...] = ()
    scanned_files: int = 0
    redactions: int = 0

    @property
    def has_credential_risk(self) -> bool:
        return bool(self.findings)

    @property
    def status(self) -> str:
        return "warning" if self.has_credential_risk else "clean"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "has_credential_risk": self.has_credential_risk,
            "finding_count": len(self.findings),
            "redaction_count": self.redactions,
            "scanned_file_count": self.scanned_files,
            "sanitized": True,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _is_literal(value: str) -> bool:
    cleaned = value.strip().strip("'\"")
    return bool(cleaned) and not _PLACEHOLDER.fullmatch(cleaned)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _finding(kind: str, source: str, text: str, offset: int, key: str) -> CredentialFinding:
    return CredentialFinding(
        kind=kind,
        severity="high" if kind in {"embedded_secret", "bearer_token"} else "warning",
        source=source,
        line=_line_number(text, offset),
        message=f"Possible embedded credential in field '{key}'. The value was redacted.",
    )


def sanitize_text(text: str, *, source: str = "artifact") -> tuple[str, CredentialScanReport]:
    """Redact literal secrets from text and return safe finding metadata."""
    if not isinstance(text, str) or not text:
        return text, CredentialScanReport()

    findings: list[CredentialFinding] = []
    spans: list[tuple[int, int, str]] = []

    for pattern, kind in (
        (_QUOTED_ASSIGNMENT, "embedded_secret"),
        (_CONNECTION_STRING, "embedded_secret"),
        (_UNQUOTED_ASSIGNMENT, "embedded_secret"),
    ):
        for match in pattern.finditer(text):
            value = match.group("value")
            if not _is_literal(value):
                continue
            start, end = match.span("value")
            spans.append((start, end, REDACTED))
            findings.append(_finding(kind, source, text, match.start(), match.group("key")))

    for match in _BEARER.finditer(text):
        value = match.group("value")
        if _is_literal(value):
            start, end = match.span("value")
            spans.append((start, end, REDACTED))
            findings.append(_finding("bearer_token", source, text, match.start(), "Bearer"))

    # Deduplicate overlapping pattern matches while keeping deterministic output.
    accepted: list[tuple[int, int, str]] = []
    for span in sorted(spans, key=lambda item: (item[0], -(item[1] - item[0]))):
        if accepted and span[0] < accepted[-1][1]:
            continue
        accepted.append(span)

    sanitized = text
    for start, end, replacement in reversed(accepted):
        sanitized = sanitized[:start] + replacement + sanitized[end:]

    unique_findings: list[CredentialFinding] = []
    seen: set[tuple[str, str, int | None]] = set()
    for finding in findings:
        key = (finding.kind, finding.source, finding.line)
        if key not in seen:
            unique_findings.append(finding)
            seen.add(key)
    report = CredentialScanReport(tuple(unique_findings), redactions=len(accepted))
    return sanitized, report


def sanitize_json_value(
    value: Any, *, source: str = "artifact"
) -> tuple[Any, CredentialScanReport]:
    """Recursively sanitize JSON-compatible values without retaining secret values."""
    findings: list[CredentialFinding] = []
    redactions = 0

    def visit(item: Any, path: str) -> Any:
        nonlocal redactions
        if isinstance(item, str):
            safe, report = sanitize_text(item, source=source)
            findings.extend(report.findings)
            redactions += report.redactions
            return safe
        if isinstance(item, dict):
            output: dict[Any, Any] = {}
            for key, child in item.items():
                key_text = str(key)
                if _SENSITIVE_KEY.search(key_text) and child not in (None, "", False):
                    output[key] = REDACTED
                    redactions += 1
                    findings.append(
                        CredentialFinding(
                            kind="sensitive_field",
                            severity="high",
                            source=source,
                            line=None,
                            message=f"Possible embedded credential in field '{key_text}'. The value was redacted.",
                        )
                    )
                else:
                    output[key] = visit(child, f"{path}.{key_text}")
            return output
        if isinstance(item, list):
            return [visit(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if isinstance(item, tuple):
            return tuple(visit(child, f"{path}[{index}]") for index, child in enumerate(item))
        return item

    safe_value = visit(value, "root")
    unique: list[CredentialFinding] = []
    seen: set[tuple[str, str, int | None, str]] = set()
    for finding in findings:
        key = (finding.kind, finding.source, finding.line, finding.message)
        if key not in seen:
            unique.append(finding)
            seen.add(key)
    return safe_value, CredentialScanReport(tuple(unique), redactions=redactions)


def _merge_reports(
    reports: list[CredentialScanReport], *, scanned_files: int
) -> CredentialScanReport:
    findings: list[CredentialFinding] = []
    seen: set[tuple[str, str, int | None, str]] = set()
    redactions = 0
    for report in reports:
        redactions += report.redactions
        for finding in report.findings:
            key = (finding.kind, finding.source, finding.line, finding.message)
            if key not in seen:
                findings.append(finding)
                seen.add(key)
    return CredentialScanReport(tuple(findings), scanned_files=scanned_files, redactions=redactions)


def scan_artifact(path: str | Path) -> CredentialScanReport:
    """Scan PBIP/PbixProj directories or PBIT/ZIP packages without exposing values."""
    root = Path(path)
    reports: list[CredentialScanReport] = []
    scanned = 0

    if root.is_file() and root.suffix.lower() in {".pbit", ".zip"}:
        with ZipFile(root) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > _MAX_SCAN_FILE_BYTES:
                    continue
                if Path(info.filename).suffix.lower() not in _TEXT_SUFFIXES:
                    continue
                raw = archive.read(info)
                if b"\x00" in raw[:4096]:
                    continue
                text = raw.decode("utf-8-sig", errors="replace")
                _, report = sanitize_text(text, source=info.filename)
                reports.append(report)
                scanned += 1
        return _merge_reports(reports, scanned_files=scanned)

    if root.is_file():
        candidates = [root]
    elif root.is_dir():
        candidates = [p for p in root.rglob("*") if p.is_file()]
    else:
        return CredentialScanReport()

    for file_path in sorted(candidates):
        if file_path.stat().st_size > _MAX_SCAN_FILE_BYTES:
            continue
        if file_path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        raw = file_path.read_bytes()
        if b"\x00" in raw[:4096]:
            continue
        text = raw.decode("utf-8-sig", errors="replace")
        _, report = sanitize_text(
            text, source=file_path.relative_to(root).as_posix() if root.is_dir() else file_path.name
        )
        reports.append(report)
        scanned += 1
    return _merge_reports(reports, scanned_files=scanned)


def hash_safe_text(text: str) -> str:
    """Return a stable hash for a sanitized text value without storing it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def report_from_json(value: Any, *, source: str = "artifact") -> CredentialScanReport:
    """Convenience helper for callers that already have a parsed JSON payload."""
    _, report = sanitize_json_value(value, source=source)
    return report


__all__ = [
    "CredentialFinding",
    "CredentialScanReport",
    "REDACTED",
    "hash_safe_text",
    "report_from_json",
    "sanitize_json_value",
    "sanitize_text",
    "scan_artifact",
]
