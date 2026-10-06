"""
Estimador de memoria RAM para modelos tabulares en Power BI.
Permite proyectar el consumo de memoria basado en tipos de datos y cardinalidad
(cantidad de valores únicos), sin necesidad de crear el modelo real.
"""

import math
from dataclasses import dataclass
from enum import Enum


class DataType(Enum):
    INT64 = "int64"
    STRING = "string"
    DATETIME = "dateTime"
    DOUBLE = "double"
    BOOLEAN = "boolean"
    DECIMAL = "decimal"


@dataclass
class MemoryEstimate:
    """Resultado de estimación de memoria para una columna."""

    data_bytes: int
    dictionary_bytes: int
    hierarchy_bytes: int
    total_bytes: int

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)


@dataclass
class ColumnMemoryEstimate:
    """Estimación detallada para ser usada en reportes."""

    column_name: str
    data_type: str
    cardinality: int
    row_count: int
    estimated_mb: float
    details: MemoryEstimate


class MemoryEstimator:
    """
    Lógica basada en la compresión del motor VertiPaq.
    Referencias: Whitepapers de SQLBI sobre VertiPaq.
    """

    # Overhead promedio por columna (estructuras internas)
    COLUMN_OVERHEAD_BYTES = 2000

    @staticmethod
    def _estimate_hash_encoding_bits(cardinality: int) -> int:
        """Calcula bits necesarios para almacenar N valores únicos (Hash Encoding)."""
        if cardinality <= 0:
            return 0
        # VertiPaq usa el número de bits más cercano: 1, 8, 16, 32, 64
        # Pero simplificamos a log2 real redondeado para estimación general
        raw_bits = math.ceil(math.log2(cardinality + 1))  # +1 por valor nulo/default

        if raw_bits <= 8:
            return 8
        if raw_bits <= 16:
            return 16
        if raw_bits <= 32:
            return 32
        return 64

    @staticmethod
    def estimate_column_size(
        data_type: str, row_count: int, cardinality: int, avg_length: int = 0
    ) -> MemoryEstimate:
        """
        Estima el tamaño en RAM de una columna.

        Args:
            data_type: Tipo de dato ('string', 'int64', etc).
            row_count: Cantidad total de filas.
            cardinality: Cantidad de valores únicos.
            avg_length: Longitud promedio (solo para strings).
        """
        dtype = MemoryEstimator._normalize_type(data_type)

        # 1. Data Structure (lo que pesa el índice de punteros)
        # Hash Encoding es el default, pero si baja cardinalidad, Value Encoding puede ser usado.
        # Aquí asumimos Hash Encoding conservadoramente.
        bits_per_row = MemoryEstimator._estimate_hash_encoding_bits(cardinality)
        data_bytes = math.ceil((row_count * bits_per_row) / 8)

        # 2. Dictionary Structure (lo que pesan los valores únicos)
        dict_bytes = 0

        if dtype == DataType.INT64:
            dict_bytes = cardinality * 8
        elif dtype == DataType.DOUBLE or dtype == DataType.DECIMAL:
            dict_bytes = cardinality * 8
        elif dtype == DataType.BOOLEAN:
            dict_bytes = cardinality * 1  # En realidad es muy poco
        elif dtype == DataType.DATETIME:
            dict_bytes = cardinality * 8
        elif dtype == DataType.STRING:
            # String dictionary: overhead + caracteres (UTF-16 aprox 2 bytes/char)
            # 8 bytes puntero + string data
            len_used = avg_length if avg_length > 0 else 20  # Default 20 chars
            dict_bytes = cardinality * (8 + (len_used * 2))

        # 3. Hierarchy/Relationships (si aplica)
        hierarchy_bytes = 0

        total = data_bytes + dict_bytes + hierarchy_bytes + MemoryEstimator.COLUMN_OVERHEAD_BYTES

        return MemoryEstimate(
            data_bytes=int(data_bytes),
            dictionary_bytes=int(dict_bytes),
            hierarchy_bytes=int(hierarchy_bytes),
            total_bytes=int(total),
        )

    @staticmethod
    def _normalize_type(raw_type: str) -> DataType:
        """Normaliza strings de tipos de datos."""
        t = str(raw_type).lower()
        if "int" in t or "i4" in t or "i8" in t:
            return DataType.INT64
        if "string" in t or "str" in t or "char" in t:
            return DataType.STRING
        if "date" in t or "time" in t:
            return DataType.DATETIME
        if "bool" in t:
            return DataType.BOOLEAN
        if "double" in t or "float" in t or "r8" in t or "decimal" in t or "real" in t:
            return DataType.DOUBLE
        return DataType.STRING  # Default

    @staticmethod
    def format_bytes(size: float) -> str:
        """Formatea bytes a string legible (KB, MB, GB)."""
        power = 2**10
        n = size
        power_labels = {0: "", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
        count = 0
        while n > power and count < 4:
            n /= power
            count += 1
        return f"{n:.2f} {power_labels[count]}"


@dataclass
class TableMemoryEstimate:
    """Estimación de memoria para una tabla completa."""

    table_name: str
    row_count: int
    columns: list[ColumnMemoryEstimate]
    total_mb: float


@dataclass
class ModelMemoryEstimate:
    """Estimación de memoria para un modelo completo."""

    model_name: str
    tables: list[TableMemoryEstimate]

    @property
    def total_mb(self) -> float:
        return sum(t.total_mb for t in self.tables)


def estimate_table_memory(
    table_name: str, row_count: int, columns: list[dict]
) -> TableMemoryEstimate:
    """
    Estima memoria para una tabla completa.

    Args:
        table_name: Nombre de la tabla
        row_count: Filas estimadas
        columns: Lista de dicts con keys: name, datatype, cardinality (opcional)
    """
    col_estimates = []
    total_bytes = 0

    for col in columns:
        c_name = col.get("name", "Unknown")
        c_type = col.get("datatype", "string")
        # Si no viene cardinalidad, estimamos 10% de row_count o min(row_count, 1000)
        c_card = col.get("cardinality")
        if c_card is None:
            c_card = int(min(row_count, max(100, row_count * 0.1)))

        est = MemoryEstimator.estimate_column_size(
            data_type=c_type, row_count=row_count, cardinality=c_card
        )

        col_est = ColumnMemoryEstimate(
            column_name=c_name,
            data_type=c_type,
            cardinality=c_card,
            row_count=row_count,
            estimated_mb=est.total_mb,
            details=est,
        )
        col_estimates.append(col_est)
        total_bytes += est.total_bytes

    return TableMemoryEstimate(
        table_name=table_name,
        row_count=row_count,
        columns=col_estimates,
        total_mb=total_bytes / (1024 * 1024),
    )
