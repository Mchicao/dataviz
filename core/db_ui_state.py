"""Estado y helpers puros para la UI de conexión a bases de datos.

Módulo sin dependencias de Streamlit para poder probarlo de forma aislada.
"""

from dataclasses import dataclass

DRIVER_LABELS: dict[str, str] = {
    "redshift": "Redshift",
    "postgresql": "PostgreSQL",
    "sqlserver": "SQL Server",
    "oracle": "Oracle",
}

DEFAULT_DRIVER = "redshift"

_DRIVER_DEFAULT_PORTS: dict[str, int] = {
    "redshift": 5439,
    "postgresql": 5432,
    "sqlserver": 1433,
    "oracle": 1521,
}


def _is_valid_driver(driver: object) -> bool:
    """Indica si ``driver`` es una clave válida de :data:`DRIVER_LABELS`."""
    return isinstance(driver, str) and driver in DRIVER_LABELS


def resolve_driver_preference(prev: str | None, selected: str) -> str:
    """Resuelve la preferencia de driver persistida en la sesión.

    Valida ``selected`` contra las claves de :data:`DRIVER_LABELS`; si es
    inválida retorna ``prev`` cuando éste también sea válido, o
    ``'redshift'`` como último recurso.
    """
    if _is_valid_driver(selected):
        return selected
    if _is_valid_driver(prev):
        return prev  # type: ignore[return-value]
    return DEFAULT_DRIVER


@dataclass
class FormField:
    """Definición declarativa de un campo del formulario de conexión.

    ``default_port`` sólo es significativo para el campo ``port``.
    """

    name: str
    label: str
    default_port: int = 0
    secret: bool = False


def driver_form_fields(driver: str) -> list[FormField]:
    """Retorna los campos del formulario de conexión para ``driver``.

    Si ``driver`` no es una clave válida se resuelve con
    :func:`resolve_driver_preference` hacia el driver por defecto.
    """
    resolved = resolve_driver_preference(None, driver)
    return [
        FormField(name="server", label="Servidor"),
        FormField(
            name="port",
            label="Puerto",
            default_port=_DRIVER_DEFAULT_PORTS[resolved],
        ),
        FormField(name="database", label="Base de Datos"),
        FormField(name="username", label="Usuario"),
        FormField(name="password", label="Contraseña", secret=True),
    ]
