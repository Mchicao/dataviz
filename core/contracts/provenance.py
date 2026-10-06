"""Contrato de proveniencia para artefactos generados por la migración.

Captura el hash criptográfico y la ruta relativa sanitizada de cada artefacto.
Nunca persiste rutas absolutas, letras de unidad, rutas UNC ni credenciales.

Diseño (Ponytail): se reutiliza ``hashlib`` y ``re`` de la stdlib y ``pydantic``
ya presente en el proyecto; el contrato se mantiene mínimo y versionable.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

#: Versión actual del esquema de proveniencia.
SCHEMA_VERSION = "1.0"

#: Único algoritmo de hash soportado por el contrato.
HASH_ALGORITHM = "sha256"


class OriginKind(StrEnum):
    """Neutral classification of the platform an artifact originates from.

    Values are deliberately target-agnostic: no PBIR visual types, no DAX
    concept names, and no React component names. Shared by the provenance
    record and the Source AST so both can record where a node/artifact came
    from without coupling to any destination runtime.
    """

    TABLEAU = "tableau"
    POWER_BI = "power_bi"
    UNKNOWN = "unknown"


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")
# Sintaxis ``user:pass@`` (RFC 3986 userInfo) para detectar credenciales embebidas.
_CRED_RE = re.compile(r"[a-zA-Z0-9_.%+-]+:[^/@]+@")


def compute_hash(data: bytes | bytearray) -> str:
    """Calcula el hash SHA-256 en hexadecimal de ``data``.

    Args:
        data: Contenido del artefacto en bytes.

    Returns:
        Hexdigest SHA-256 de 64 caracteres.

    Raises:
        TypeError: Si ``data`` no es ``bytes``/``bytearray``.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data debe ser bytes o bytearray")
    return hashlib.sha256(data).hexdigest()


def sanitize_relative_path(path: str) -> str:
    """Normaliza ``path`` a una ruta POSIX relativa segura.

    - Rechaza rutas absolutas POSIX (``/`` inicial), letras de unidad Windows
      (``C:``) y rutas UNC (``//host/share``).
    - Rechaza segmentos ``..`` que escapen de la raíz del proyecto.
    - Normaliza separadores a ``/`` y colapsa ``.`` y dobles barras.
    - Bloquea credenciales con sintaxis ``user:pass@``.

    Args:
        path: Ruta bruta a sanitizar.

    Returns:
        Ruta relativa normalizada con separadores ``/``.

    Raises:
        ValueError: Si la ruta es inválida, absoluta, escapa de la raíz o
            contiene credenciales.
    """
    if path is None:
        raise ValueError("La ruta no puede ser None")
    raw = str(path)
    if not raw.strip():
        raise ValueError("La ruta no puede estar vacía")
    normalized = raw.replace("\\", "/")
    if _DRIVE_RE.match(normalized):
        raise ValueError(f"Letra de unidad no permitida en la ruta: {path!r}")
    if normalized.startswith("//"):
        raise ValueError(f"Ruta UNC no permitida: {path!r}")
    if normalized.startswith("/"):
        raise ValueError(f"Ruta absoluta no permitida: {path!r}")

    parts: list[str] = []
    for segment in normalized.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                raise ValueError(f"La ruta escapa de la raíz del proyecto: {path!r}")
            parts.pop()
            continue
        parts.append(segment)
    if not parts:
        raise ValueError("La ruta relativa no puede quedar vacía")

    sanitized = "/".join(parts)
    if _CRED_RE.search(sanitized):
        raise ValueError("La ruta contiene credenciales embebidas")
    return sanitized


class ProvenanceRecord(BaseModel):
    """Registro inmutable de proveniencia de un único artefacto.

    El contrato es deliberadamente reducido (versión, ruta, hash, algoritmo)
    para evitar filtrar metadatos sensibles como rutas absolutas o credenciales.
    Los campos adicionales se rechazan (``extra="forbid"``) como defensa frente
    a registros que pudieran incluir credenciales por error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    artifact_path: str
    artifact_hash: str
    hash_algorithm: str = HASH_ALGORITHM
    origin: str = OriginKind.UNKNOWN.value

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: str) -> str:
        # La versión del esquema es obligatoria; nunca cadena vacía.
        if not value or not value.strip():
            raise ValueError("schema_version no puede estar vacío")
        return value

    @field_validator("artifact_path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        # Sanitiza y rechaza rutas absolutas/UNC/con credenciales.
        return sanitize_relative_path(value)

    @field_validator("artifact_hash")
    @classmethod
    def _check_hash(cls, value: str) -> str:
        # SHA-256 hex de 64 caracteres; se normaliza a minúsculas.
        candidate = value.lower()
        if not _HASH_RE.match(candidate):
            raise ValueError("artifact_hash debe ser hex SHA-256 de 64 caracteres")
        return candidate

    @field_validator("hash_algorithm")
    @classmethod
    def _check_algorithm(cls, value: str) -> str:
        # Sólo se soporta sha256 para evitar algoritmos débiles (md5, etc.).
        if value.lower() != HASH_ALGORITHM:
            raise ValueError(f"Sólo se soporta el algoritmo {HASH_ALGORITHM!r}")
        return value.lower()

    @field_validator("origin")
    @classmethod
    def _check_origin(cls, value: str) -> str:
        # Origin must be a known OriginKind value; free-form platform names are
        # rejected to keep provenance origin-aware but closed.
        allowed = {kind.value for kind in OriginKind}
        if value not in allowed:
            raise ValueError(f"origin must be one of {sorted(allowed)}, got {value!r}")
        return value

    @classmethod
    def from_bytes(
        cls,
        *,
        artifact_path: str,
        data: bytes | bytearray,
        schema_version: str = SCHEMA_VERSION,
        origin: str = OriginKind.UNKNOWN.value,
    ) -> ProvenanceRecord:
        """Construye un registro desde los bytes del artefacto.

        Args:
            artifact_path: Ruta relativa del artefacto (se sanitiza).
            data: Contenido del artefacto; se calcula el hash SHA-256.
            schema_version: Versión del esquema (por defecto la actual).
            origin: Plataforma de origen (:class:`OriginKind` value). Por
                defecto ``"unknown"`` para mantener compatibilidad con llamadas
                que aún no clasifican el origen.

        Returns:
            ``ProvenanceRecord`` inmutable con hash, ruta sanitizada y origen.
        """
        return cls(
            schema_version=schema_version,
            artifact_path=artifact_path,
            artifact_hash=compute_hash(data),
            hash_algorithm=HASH_ALGORITHM,
            origin=origin,
        )
