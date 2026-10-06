"""Contrato versionado de capacidades por conector (Connector SDK).

Declara, por origen de datos, la capacidad mínima que las primeras fuentes
(D009: CSV/Parquet local, PostgreSQL, SQL Server y API HTTP) necesitan:

* descubrimiento de esquema y tipos neutrales soportados;
* autenticación por esquema, referida mediante identificador opaco
  (:class:`CredentialRef`); ningún campo transporta un valor de secreto
  (D008 / ADR-0001);
* capacidad de consulta SQL o API, separada del mecanismo de pushdown;
* pushdown (proyección, filtro, agregación, límite) con dialecto obligatorio;
* streaming por lotes, cancelación cooperativa y límites aplicables
  (filas / tiempo / costo) (D005);
* disponibilidad de metadata de costo y semántica normalizada de errores;
* RLS nativo del origen y modos de ejecución live/extract con política de
  frescura para extract (D010).

Reglas de coherencia validadas en construcción:

* declarar pushdown sin dialecto es inválido, y un dialecto sin ninguna
  capacidad de pushdown también lo es;
* el modo ``extract`` exige una política de frescura con ``ttl_seconds >= 1``;
  una política de frescura sin modo ``extract`` también es inválida;
* ``auth`` con ``NONE`` no puede combinarse con esquemas referidos;
* ``connector_id`` es ``snake_case`` en minúsculas.

El contrato es pasivo (sólo datos, biblioteca estándar) y no importa
conectores concretos ni drivers de red.

Política de versionado (SemVer):

* **MAJOR**: campo eliminado/renombrado, tipo rupturista, semántica alterada
  o expansión de un enum cerrado que lectores anteriores no pueden entender.
* **MINOR**: campo nuevo opcional con valor por defecto. Lectores actuales
  aceptan payloads de minors anteriores del mismo major y los normalizan a la
  versión actual; minors futuros se rechazan de forma fail-closed para no
  descartar capacidades desconocidas silenciosamente.
* **PATCH**: corrección que no cambia la forma serializada.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION: str = "1.2.0"
"""Versión actual del contrato de capacidades de conector (SemVer)."""

#: Conjunto cerrado de tipos neutrales que un conector puede declarar. Es la
#: capacidad mínima común de las primeras fuentes; ampliarlo es un cambio MINOR.
NEUTRAL_TYPES: frozenset[str] = frozenset(
    {
        "string",
        "boolean",
        "integer",
        "float",
        "decimal",
        "date",
        "datetime",
        "binary",
    }
)

_CONNECTOR_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
"""Identificador canónico de conector: snake_case en minúsculas (1-64)."""

_CREDENTIAL_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
"""Identificador opaco de credencial.

Excluye deliberadamente espacios, ``=``, ``:`` y ``@``: con ese alfabeto no es
posible formar un DSN, URL ni par clave-valor que incruste un secreto.
"""


class SchemaDiscoveryLevel(StrEnum):
    """Nivel de descubrimiento de esquema que el conector garantiza.

    * ``NONE``: no expone esquema previo a la lectura.
    * ``PARTIAL``: esquema best-effort (p. ej. inferido de una respuesta).
    * ``FULL``: esquema confiable obtenido del origen antes de leer datos.
    """

    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class AuthKind(StrEnum):
    """Esquemas de autenticación soportables, siempre por referencia.

    Cada valor describe el *esquema*, no el valor: el secreto vive en el
    gestor de secretos y se referencia mediante :class:`CredentialRef`
    (D008 / ADR-0001).
    """

    NONE = "none"
    PASSWORD_REF = "password_ref"
    TOKEN_REF = "token_ref"
    API_KEY_REF = "api_key_ref"


class ExecutionMode(StrEnum):
    """Modos de ejecución de consulta (D010: híbrido live/extract)."""

    LIVE = "live"
    EXTRACT = "extract"


class CapabilitySupport(StrEnum):
    """Estado explícito para capacidades que pueden ser parciales."""

    UNSUPPORTED = "unsupported"
    PARTIAL = "partial"
    SUPPORTED = "supported"


class QueryKind(StrEnum):
    """Interfaz de consulta que expone el origen al conector."""

    NONE = "none"
    SQL = "sql"
    API = "api"


class PaginationKind(StrEnum):
    """Mechanism a connector uses to continue a bounded source read."""

    OFFSET = "offset"
    CURSOR = "cursor"
    PAGE = "page"
    TOKEN = "token"


class ErrorCategory(StrEnum):
    """Categorías estables de error que un conector puede normalizar."""

    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    SOURCE_ERROR = "source_error"


def _parse_semver(value: str) -> tuple[int, int, int]:
    """Parsea una versión SemVer simple ``MAJOR.MINOR.PATCH``."""
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value
    ):
        raise ValueError(f"schema_version must be SemVer MAJOR.MINOR.PATCH, got {value!r}")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _coerce_enum(enum_type: type, value: Any, label: str) -> Any:
    """Normaliza ``value`` al enum, aceptando la instancia o su string."""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            allowed = [member.value for member in enum_type]
            raise ValueError(f"invalid {label}: {value!r} (expected one of {allowed})") from exc
    raise TypeError(f"{label} must be str or {enum_type.__name__}, not {type(value).__name__}")


@dataclass(frozen=True)
class CredentialRef:
    """Identificador opaco de una credencial en el gestor de secretos.

    Nunca transporta el valor del secreto: sólo la referencia que el plano de
    secretos (P3-SECRETS) resuelve a un token efímero en tiempo de conexión.

    Attributes:
        value: identificador opaco (alfabeto ``[A-Za-z0-9._/-]``; se rechazan
            espacios, ``=``, ``:`` y ``@`` para que no pueda incrustarse un
            secreto, DSN o URL con credenciales).
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("CredentialRef.value is required")
        if not _CREDENTIAL_REF_RE.match(self.value):
            raise ValueError(
                f"CredentialRef.value must be an opaque identifier matching "
                f"{_CREDENTIAL_REF_RE.pattern!r} (no whitespace, '=', ':' or '@'), "
                f"got {self.value!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict compatible con JSON."""
        return {"value": self.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CredentialRef:
        """Reconstruye desde un dict validando el patrón."""
        return cls(value=str(data["value"]))


@dataclass(frozen=True)
class AuthSupport:
    """Esquemas de autenticación que un conector soporta.

    Attributes:
        kinds: tupla no vacía de :class:`AuthKind`. ``NONE`` no puede
            combinarse con esquemas referidos.
    """

    kinds: tuple[AuthKind, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kinds, tuple):
            object.__setattr__(self, "kinds", tuple(self.kinds))
        coerced = tuple(_coerce_enum(AuthKind, kind, "auth kind") for kind in self.kinds)
        object.__setattr__(self, "kinds", coerced)
        if not coerced:
            raise ValueError("AuthSupport.kinds must declare at least one AuthKind")
        if AuthKind.NONE in coerced and len(coerced) > 1:
            raise ValueError("AuthSupport.kinds must not combine NONE with referenced schemes")

    def allows(self, kind: AuthKind) -> bool:
        """Indica si ``kind`` está entre los esquemas soportados."""
        return kind in self.kinds

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict compatible con JSON."""
        return {"kinds": [kind.value for kind in self.kinds]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuthSupport:
        """Reconstruye desde un dict."""
        return cls(kinds=tuple(AuthKind(k) for k in data["kinds"]))


@dataclass(frozen=True)
class PushdownSupport:
    """Capacidades de pushdown del conector y su dialecto.

    El dialecto describe el lenguaje de expresión que el origen acepta en el
    pushdown (p. ej. ``"postgresql"``, ``"tsql"``) o el mecanismo del lector
    (p. ej. ``"arrow"`` para proyección por columnas en pyarrow).

    Regla de coherencia: hay dialecto si y sólo si hay al menos una capacidad.
    """

    projection: bool = False
    filter: bool = False
    aggregation: bool = False
    limit: bool = False
    dialect: str = ""

    def __post_init__(self) -> None:
        if self.dialect and not self.supported:
            raise ValueError("pushdown dialect declared without any pushdown capability")
        if self.supported and not self.dialect.strip():
            raise ValueError(
                "pushdown capabilities require a non-empty dialect "
                "(declaring pushdown without a dialect is invalid)"
            )

    @property
    def supported(self) -> bool:
        """``True`` si el conector empuja al menos una operación al origen."""
        return bool(self.projection or self.filter or self.aggregation or self.limit)

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict compatible con JSON."""
        return {
            "projection": self.projection,
            "filter": self.filter,
            "aggregation": self.aggregation,
            "limit": self.limit,
            "dialect": self.dialect,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PushdownSupport:
        """Reconstruye desde un dict."""
        return cls(
            projection=bool(data.get("projection", False)),
            filter=bool(data.get("filter", False)),
            aggregation=bool(data.get("aggregation", False)),
            limit=bool(data.get("limit", False)),
            dialect=str(data.get("dialect", "")),
        )


@dataclass(frozen=True)
class QuerySupport:
    """Capacidad SQL/API declarada sin confundirla con pushdown."""

    kind: QueryKind = QueryKind.NONE
    support: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    dialect: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _coerce_enum(QueryKind, self.kind, "query kind"))
        object.__setattr__(
            self,
            "support",
            _coerce_enum(CapabilitySupport, self.support, "query support"),
        )
        if self.support is not CapabilitySupport.UNSUPPORTED and self.kind is QueryKind.NONE:
            raise ValueError("query support requires query kind 'sql' or 'api'")
        if self.kind is QueryKind.SQL and self.support is not CapabilitySupport.UNSUPPORTED:
            if not self.dialect.strip():
                raise ValueError("supported SQL query capability requires a dialect")
        elif self.dialect:
            raise ValueError("query dialect is only valid for a supported/partial SQL capability")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "support": self.support.value,
            "dialect": self.dialect,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QuerySupport:
        return cls(
            kind=data.get("kind", QueryKind.NONE.value),
            support=data.get("support", CapabilitySupport.UNSUPPORTED.value),
            dialect=str(data.get("dialect", "")),
        )


@dataclass(frozen=True)
class PaginationSupport:
    """Explicit pagination capability; chunked local streaming is not pagination."""

    support: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    kinds: tuple[PaginationKind, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "support",
            _coerce_enum(CapabilitySupport, self.support, "pagination support"),
        )
        if not isinstance(self.kinds, tuple):
            object.__setattr__(self, "kinds", tuple(self.kinds))
        kinds = tuple(
            _coerce_enum(PaginationKind, kind, "pagination kind") for kind in self.kinds
        )
        object.__setattr__(self, "kinds", kinds)
        if len(set(kinds)) != len(kinds):
            raise ValueError(f"duplicated pagination kinds: {kinds!r}")
        if self.support is CapabilitySupport.UNSUPPORTED and kinds:
            raise ValueError("unsupported pagination cannot declare pagination kinds")
        if self.support is not CapabilitySupport.UNSUPPORTED and not kinds:
            raise ValueError("partial/supported pagination must declare at least one kind")

    def to_dict(self) -> dict[str, Any]:
        return {"support": self.support.value, "kinds": [kind.value for kind in self.kinds]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaginationSupport:
        return cls(
            support=data.get("support", CapabilitySupport.UNSUPPORTED.value),
            kinds=tuple(data.get("kinds", ())),
        )


_COST_METADATA_FIELDS = frozenset(
    {"estimated_rows", "estimated_bytes", "source_cost_units", "currency"}
)


@dataclass(frozen=True)
class CostMetadataSupport:
    """Metadata de costo que el conector puede producir, nunca valores de costo."""

    support: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "support",
            _coerce_enum(CapabilitySupport, self.support, "cost metadata support"),
        )
        if not isinstance(self.fields, tuple):
            object.__setattr__(self, "fields", tuple(self.fields))
        unknown = [
            field_name for field_name in self.fields if field_name not in _COST_METADATA_FIELDS
        ]
        if unknown:
            raise ValueError(f"unsupported cost metadata fields: {unknown}")
        if len(set(self.fields)) != len(self.fields):
            raise ValueError(f"duplicated cost metadata fields: {self.fields!r}")
        if self.support is CapabilitySupport.UNSUPPORTED and self.fields:
            raise ValueError("unsupported cost metadata cannot declare fields")
        if self.support is not CapabilitySupport.UNSUPPORTED and not self.fields:
            raise ValueError("partial/supported cost metadata must declare at least one field")

    def to_dict(self) -> dict[str, Any]:
        return {"support": self.support.value, "fields": list(self.fields)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CostMetadataSupport:
        return cls(
            support=data.get("support", CapabilitySupport.UNSUPPORTED.value),
            fields=tuple(str(field_name) for field_name in data.get("fields", ())),
        )


@dataclass(frozen=True)
class ErrorSemantics:
    """Semántica de errores normalizados que el runtime puede depender de."""

    support: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    categories: tuple[ErrorCategory, ...] = ()
    retryable: tuple[ErrorCategory, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "support",
            _coerce_enum(CapabilitySupport, self.support, "error semantics support"),
        )
        if not isinstance(self.categories, tuple):
            object.__setattr__(self, "categories", tuple(self.categories))
        if not isinstance(self.retryable, tuple):
            object.__setattr__(self, "retryable", tuple(self.retryable))
        categories = tuple(
            _coerce_enum(ErrorCategory, category, "error category") for category in self.categories
        )
        retryable = tuple(
            _coerce_enum(ErrorCategory, category, "retryable error category")
            for category in self.retryable
        )
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "retryable", retryable)
        if len(set(categories)) != len(categories):
            raise ValueError(f"duplicated error categories: {categories!r}")
        if len(set(retryable)) != len(retryable):
            raise ValueError(f"duplicated retryable error categories: {retryable!r}")
        if self.support is CapabilitySupport.UNSUPPORTED and (categories or retryable):
            raise ValueError("unsupported error semantics cannot declare categories")
        if self.support is not CapabilitySupport.UNSUPPORTED and not categories:
            raise ValueError("partial/supported error semantics must declare categories")
        if not set(retryable).issubset(categories):
            raise ValueError("retryable error categories must be a subset of declared categories")

    def to_dict(self) -> dict[str, Any]:
        return {
            "support": self.support.value,
            "categories": [category.value for category in self.categories],
            "retryable": [category.value for category in self.retryable],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ErrorSemantics:
        return cls(
            support=data.get("support", CapabilitySupport.UNSUPPORTED.value),
            categories=tuple(data.get("categories", ())),
            retryable=tuple(data.get("retryable", ())),
        )


@dataclass(frozen=True)
class LimitsSupport:
    """Límites que el conector puede aplicar a una consulta (D005/D010).

    Attributes:
        row_limit: puede cortar por número máximo de filas.
        time_limit: puede cortar por presupuesto de tiempo.
        cost_limit: puede cortar por costo estimado de la consulta.
    """

    row_limit: bool = False
    time_limit: bool = False
    cost_limit: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict compatible con JSON."""
        return {
            "row_limit": self.row_limit,
            "time_limit": self.time_limit,
            "cost_limit": self.cost_limit,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LimitsSupport:
        """Reconstruye desde un dict."""
        return cls(
            row_limit=bool(data.get("row_limit", False)),
            time_limit=bool(data.get("time_limit", False)),
            cost_limit=bool(data.get("cost_limit", False)),
        )


@dataclass(frozen=True)
class FreshnessPolicy:
    """Política de frescura exigida por el modo ``extract`` (D010).

    Attributes:
        ttl_seconds: antigüedad máxima aceptada del extracto (>= 1 segundo).
    """

    ttl_seconds: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.ttl_seconds, bool) or not isinstance(self.ttl_seconds, int):
            raise TypeError(f"ttl_seconds must be an int, got {self.ttl_seconds!r}")
        if self.ttl_seconds < 1:
            raise ValueError(f"ttl_seconds must be >= 1, got {self.ttl_seconds!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict compatible con JSON."""
        return {"ttl_seconds": self.ttl_seconds}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FreshnessPolicy:
        """Reconstruye desde un dict."""
        return cls(ttl_seconds=int(data["ttl_seconds"]))


@dataclass(frozen=True)
class ConnectorCapabilityContract:
    """Declaración versionada de capacidades de un conector.

    Invariantes (validadas en ``__post_init__``):

    * ``schema_version`` debe coincidir exactamente con :data:`SCHEMA_VERSION`.
    * ``connector_id`` coincide con ``^[a-z][a-z0-9_]{0,63}$``.
    * ``supported_types`` es sin duplicados y subset de :data:`NEUTRAL_TYPES`;
      puede estar vacío sólo si ``implementation`` es ``unsupported``.
    * pushdown y dialecto son coherentes (ambos o ninguno).
    * ``execution_modes`` es no vacío para implementaciones reales; un roadmap
      ``unsupported`` no declara modos operativos. ``EXTRACT`` exige ``freshness``.
    * ningún campo transporta un valor de credencial.

    Attributes:
        schema_version: versión SemVer del contrato.
        connector_id: identificador canónico del origen (p. ej. ``"csv"``).
        implementation: estado del implementation runtime real. ``unsupported``
            significa roadmap/declaración, no capability operativa.
        schema_discovery: nivel de descubrimiento de esquema garantizado.
        supported_types: tipos neutrales que el origen expone.
        auth: esquemas de autenticación soportados.
        query: interfaz de consulta SQL/API y nivel de soporte.
        pagination: mecanismos de paginación soportados por el runtime.
        pushdown: capacidades de pushdown y dialecto.
        streaming_row_chunkable: emite lotes de filas (streaming).
        cancelable: soporta cancelación cooperativa entre lotes.
        limits: límites aplicables (filas/tiempo/costo).
        cost_metadata: metadata de costo disponible y campos declarados.
        error_semantics: categorías de error normalizadas y reintentables.
        native_rls: el origen aplica RLS de forma nativa.
        execution_modes: modos soportados (live/extract).
        freshness: política de frescura, obligatoria si hay ``extract``.
        requires_network: ``True`` sólo si el conector abre un socket.
        description: nota legible (sin secretos ni rutas absolutas).
    """

    schema_version: str
    connector_id: str
    implementation: CapabilitySupport = CapabilitySupport.SUPPORTED
    schema_discovery: SchemaDiscoveryLevel = SchemaDiscoveryLevel.NONE
    supported_types: tuple[str, ...] = ()
    auth: AuthSupport = field(default_factory=lambda: AuthSupport(kinds=(AuthKind.NONE,)))
    query: QuerySupport = field(default_factory=QuerySupport)
    pagination: PaginationSupport = field(default_factory=PaginationSupport)
    pushdown: PushdownSupport = field(default_factory=PushdownSupport)
    streaming_row_chunkable: bool = False
    cancelable: bool = False
    limits: LimitsSupport = field(default_factory=LimitsSupport)
    cost_metadata: CostMetadataSupport = field(default_factory=CostMetadataSupport)
    error_semantics: ErrorSemantics = field(default_factory=ErrorSemantics)
    native_rls: bool = False
    execution_modes: tuple[ExecutionMode, ...] = ()
    freshness: FreshnessPolicy | None = None
    requires_network: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        current_major, current_minor, _ = _parse_semver(SCHEMA_VERSION)
        major, minor, _ = _parse_semver(self.schema_version)
        if major != current_major or minor > current_minor:
            raise ValueError(
                f"schema_version incompatible: reader={SCHEMA_VERSION!r}, "
                f"received={self.schema_version!r}"
            )
        if self.schema_version != SCHEMA_VERSION:
            object.__setattr__(self, "schema_version", SCHEMA_VERSION)
        if not isinstance(self.connector_id, str) or not _CONNECTOR_ID_RE.match(self.connector_id):
            raise ValueError(
                f"connector_id must match {_CONNECTOR_ID_RE.pattern!r}, got {self.connector_id!r}"
            )
        object.__setattr__(
            self,
            "implementation",
            _coerce_enum(CapabilitySupport, self.implementation, "implementation support"),
        )
        object.__setattr__(
            self,
            "schema_discovery",
            _coerce_enum(SchemaDiscoveryLevel, self.schema_discovery, "schema_discovery"),
        )
        if not isinstance(self.supported_types, tuple):
            object.__setattr__(self, "supported_types", tuple(self.supported_types))
        types = self.supported_types
        if not types and self.implementation is not CapabilitySupport.UNSUPPORTED:
            raise ValueError("implemented connectors must declare at least one neutral type")
        unknown = [t for t in types if t not in NEUTRAL_TYPES]
        if unknown:
            raise ValueError(
                f"unsupported neutral types: {unknown} (allowlist={sorted(NEUTRAL_TYPES)})"
            )
        if len(set(types)) != len(types):
            raise ValueError(f"duplicated supported types: {types!r}")
        if not isinstance(self.auth, AuthSupport):
            object.__setattr__(self, "auth", AuthSupport.from_dict(self.auth))
        if not isinstance(self.query, QuerySupport):
            object.__setattr__(self, "query", QuerySupport.from_dict(self.query))
        if not isinstance(self.pagination, PaginationSupport):
            object.__setattr__(self, "pagination", PaginationSupport.from_dict(self.pagination))
        if not isinstance(self.pushdown, PushdownSupport):
            object.__setattr__(self, "pushdown", PushdownSupport.from_dict(self.pushdown))
        if not isinstance(self.limits, LimitsSupport):
            object.__setattr__(self, "limits", LimitsSupport.from_dict(self.limits))
        if not isinstance(self.cost_metadata, CostMetadataSupport):
            object.__setattr__(
                self, "cost_metadata", CostMetadataSupport.from_dict(self.cost_metadata)
            )
        if not isinstance(self.error_semantics, ErrorSemantics):
            object.__setattr__(
                self, "error_semantics", ErrorSemantics.from_dict(self.error_semantics)
            )
        if not isinstance(self.execution_modes, tuple):
            object.__setattr__(self, "execution_modes", tuple(self.execution_modes))
        modes = tuple(
            _coerce_enum(ExecutionMode, mode, "execution mode") for mode in self.execution_modes
        )
        object.__setattr__(self, "execution_modes", modes)
        if not modes and self.implementation is not CapabilitySupport.UNSUPPORTED:
            raise ValueError("implemented connectors must declare at least one execution_modes value")
        if ExecutionMode.EXTRACT in modes and self.freshness is None:
            raise ValueError(
                "execution mode 'extract' requires a FreshnessPolicy "
                "(extract requires a freshness policy)"
            )
        if self.freshness is not None and ExecutionMode.EXTRACT not in modes:
            raise ValueError("FreshnessPolicy declared without the 'extract' execution mode")
        if self.implementation is CapabilitySupport.UNSUPPORTED:
            if self.query.support is not CapabilitySupport.UNSUPPORTED:
                raise ValueError("an unavailable connector cannot claim query support")
            if self.pagination.support is not CapabilitySupport.UNSUPPORTED:
                raise ValueError("an unavailable connector cannot claim pagination support")
            if self.pushdown.supported:
                raise ValueError("an unavailable connector cannot claim pushdown support")
            if self.streaming_row_chunkable or self.cancelable:
                raise ValueError("an unavailable connector cannot claim runtime streaming/cancellation")
            if self.limits.row_limit or self.limits.time_limit or self.limits.cost_limit:
                raise ValueError("an unavailable connector cannot claim enforceable query limits")
            if self.native_rls:
                raise ValueError("an unavailable connector cannot claim runtime native RLS enforcement")
            if modes:
                raise ValueError("an unavailable connector cannot declare operational execution modes")
        if not isinstance(self.description, str) or not self.description.strip():
            # Default estable y no vacío, igual que el descriptor heredado.
            object.__setattr__(self, "description", f"{self.connector_id} connector")

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict compatible con JSON."""
        return {
            "schema_version": self.schema_version,
            "connector_id": self.connector_id,
            "implementation": self.implementation.value,
            "schema_discovery": self.schema_discovery.value,
            "supported_types": list(self.supported_types),
            "auth": self.auth.to_dict(),
            "query": self.query.to_dict(),
            "pagination": self.pagination.to_dict(),
            "pushdown": self.pushdown.to_dict(),
            "streaming_row_chunkable": self.streaming_row_chunkable,
            "cancelable": self.cancelable,
            "limits": self.limits.to_dict(),
            "cost_metadata": self.cost_metadata.to_dict(),
            "error_semantics": self.error_semantics.to_dict(),
            "native_rls": self.native_rls,
            "execution_modes": [mode.value for mode in self.execution_modes],
            "freshness": self.freshness.to_dict() if self.freshness else None,
            "requires_network": self.requires_network,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConnectorCapabilityContract:
        """Reconstruye desde un dict, validando todas las reglas de coherencia."""
        freshness = data.get("freshness")
        return cls(
            schema_version=str(data["schema_version"]),
            connector_id=str(data["connector_id"]),
            implementation=data.get("implementation", CapabilitySupport.SUPPORTED.value),
            schema_discovery=_coerce_enum(
                SchemaDiscoveryLevel, data.get("schema_discovery", "none"), "schema_discovery"
            ),
            supported_types=tuple(str(t) for t in data.get("supported_types", ())),
            auth=(
                AuthSupport.from_dict(data["auth"])
                if "auth" in data
                else AuthSupport(kinds=(AuthKind.NONE,))
            ),
            query=QuerySupport.from_dict(data.get("query", {})),
            pagination=PaginationSupport.from_dict(data.get("pagination", {})),
            pushdown=PushdownSupport.from_dict(data.get("pushdown", {})),
            streaming_row_chunkable=bool(data.get("streaming_row_chunkable", False)),
            cancelable=bool(data.get("cancelable", False)),
            limits=LimitsSupport.from_dict(data.get("limits", {})),
            cost_metadata=CostMetadataSupport.from_dict(data.get("cost_metadata", {})),
            error_semantics=ErrorSemantics.from_dict(data.get("error_semantics", {})),
            native_rls=bool(data.get("native_rls", False)),
            execution_modes=tuple(ExecutionMode(m) for m in data.get("execution_modes", ())),
            freshness=FreshnessPolicy.from_dict(freshness) if freshness else None,
            requires_network=bool(data.get("requires_network", False)),
            description=str(data.get("description", "")),
        )

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serializa a JSON canónico (claves ordenadas, UTF-8 puro)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> ConnectorCapabilityContract:
        """Reconstruye desde JSON canónico (``from_dict(json.loads(text))``)."""
        return cls.from_dict(json.loads(text))


__all__ = [
    "AuthKind",
    "AuthSupport",
    "CapabilitySupport",
    "ConnectorCapabilityContract",
    "CostMetadataSupport",
    "CredentialRef",
    "ErrorCategory",
    "ErrorSemantics",
    "ExecutionMode",
    "FreshnessPolicy",
    "LimitsSupport",
    "NEUTRAL_TYPES",
    "PaginationKind",
    "PaginationSupport",
    "PushdownSupport",
    "QueryKind",
    "QuerySupport",
    "SCHEMA_VERSION",
    "SchemaDiscoveryLevel",
]
