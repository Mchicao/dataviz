"""Dataset tabular neutral compartido por runtimes y adaptadores."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Dataset:
    """Tabla inmutable con columnas ordenadas y filas alineadas."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Dataset.name es obligatorio")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f"columnas duplicadas en dataset {self.name!r}")
        expected = set(self.columns)
        for index, row in enumerate(self.rows):
            if set(row) != expected:
                raise ValueError(f"fila {index} no coincide con las columnas de {self.name!r}")

    @classmethod
    def from_records(
        cls,
        name: str,
        records: Sequence[Mapping[str, Any]],
        columns: Sequence[str] | None = None,
    ) -> Dataset:
        """Construye un dataset e infiere el orden por primera aparición."""
        if columns is None:
            ordered = tuple(dict.fromkeys(key for record in records for key in record))
        else:
            ordered = tuple(columns)
        rows = tuple({column: record.get(column) for column in ordered} for record in records)
        return cls(name=name, columns=ordered, rows=rows)

    def column_index(self, name: str) -> int:
        """Devuelve la posición de una columna; falla si no existe."""
        try:
            return self.columns.index(name)
        except ValueError as exc:
            raise KeyError(f"campo {name!r} no existe en dataset {self.name!r}") from exc

    def has_column(self, name: str) -> bool:
        """Indica si la columna pertenece al esquema físico."""
        return name in self.columns


__all__ = ["Dataset"]
