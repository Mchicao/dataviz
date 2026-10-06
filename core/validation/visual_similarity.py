"""Comparación visual ligera sobre buffers RGB normalizados.

El módulo no decodifica PNG ni abre navegadores. Recibe buffers ya capturados
con el mismo viewport y calcula métricas por región, de modo que el harness E2E
pueda seguir siendo responsable de las capturas sin añadir dependencias al
runtime del migrador.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ComparisonStatus(StrEnum):
    """Estado derivado de una comparación visual."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    PENDING = "pending"


@dataclass(frozen=True)
class VisualRegion:
    """Región y tolerancias de comparación en coordenadas de píxel."""

    name: str
    x: int
    y: int
    width: int
    height: int
    max_mean_difference: float = 0.03
    max_changed_ratio: float = 0.08
    max_ink_loss: float = 0.20

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("region name is required")
        if min(self.x, self.y) < 0 or min(self.width, self.height) <= 0:
            raise ValueError("region coordinates must be non-negative and sized")
        for name, value in (
            ("max_mean_difference", self.max_mean_difference),
            ("max_changed_ratio", self.max_changed_ratio),
            ("max_ink_loss", self.max_ink_loss),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True)
class RegionComparison:
    """Métricas calculadas para una región."""

    name: str
    status: ComparisonStatus
    mean_difference: float
    changed_ratio: float
    ink_loss: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "mean_difference": self.mean_difference,
            "changed_ratio": self.changed_ratio,
            "ink_loss": self.ink_loss,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegionComparison:
        return cls(
            name=str(data["name"]),
            status=ComparisonStatus(str(data["status"])),
            mean_difference=float(data.get("mean_difference", 0.0)),
            changed_ratio=float(data.get("changed_ratio", 0.0)),
            ink_loss=float(data.get("ink_loss", 0.0)),
        )


@dataclass(frozen=True)
class VisualComparison:
    """Resultado agregado de una captura de referencia contra DataVIZ."""

    page_id: str
    status: ComparisonStatus
    regions: tuple[RegionComparison, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "status": self.status.value,
            "regions": [region.to_dict() for region in self.regions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualComparison:
        return cls(
            page_id=str(data.get("page_id", "")),
            status=ComparisonStatus(str(data["status"])),
            regions=tuple(RegionComparison.from_dict(item) for item in data.get("regions", [])),
        )


def compare_rgb_buffers(
    reference: bytes,
    actual: bytes,
    *,
    width: int,
    height: int,
    regions: tuple[VisualRegion, ...],
    channels: int = 3,
    changed_channel_delta: int = 12,
    background_floor: int = 245,
    page_id: str = "",
) -> VisualComparison:
    """Compara buffers RGB/RGBA del mismo tamaño sin dependencias externas."""

    expected = width * height * channels
    if width <= 0 or height <= 0 or channels not in {3, 4}:
        raise ValueError("width/height must be positive and channels must be 3 or 4")
    if len(reference) != expected or len(actual) != expected:
        raise ValueError(f"buffer length must be {expected} bytes")
    if not 0 <= changed_channel_delta <= 255:
        raise ValueError("changed_channel_delta must be in [0, 255]")
    if not 0 <= background_floor <= 255:
        raise ValueError("background_floor must be in [0, 255]")
    if not regions:
        raise ValueError("at least one visual region is required")

    results = tuple(
        _compare_region(
            reference,
            actual,
            width=width,
            height=height,
            channels=channels,
            region=region,
            changed_channel_delta=changed_channel_delta,
            background_floor=background_floor,
        )
        for region in regions
    )
    if any(result.status is ComparisonStatus.FAILED for result in results):
        status = ComparisonStatus.FAILED
    elif any(result.status is ComparisonStatus.WARNING for result in results):
        status = ComparisonStatus.WARNING
    else:
        status = ComparisonStatus.PASSED
    return VisualComparison(page_id=page_id, status=status, regions=results)


def _compare_region(
    reference: bytes,
    actual: bytes,
    *,
    width: int,
    height: int,
    channels: int,
    region: VisualRegion,
    changed_channel_delta: int,
    background_floor: int,
) -> RegionComparison:
    if region.x + region.width > width or region.y + region.height > height:
        raise ValueError(f"region {region.name!r} exceeds the image bounds")

    difference = 0
    changed = 0
    reference_ink = 0
    actual_ink = 0
    pixels = region.width * region.height
    for y in range(region.y, region.y + region.height):
        for x in range(region.x, region.x + region.width):
            offset = (y * width + x) * channels
            ref_pixel = reference[offset : offset + 3]
            actual_pixel = actual[offset : offset + 3]
            deltas = tuple(abs(left - right) for left, right in zip(ref_pixel, actual_pixel))
            difference += sum(deltas)
            changed += int(max(deltas) > changed_channel_delta)
            reference_ink += int(sum(ref_pixel) / 3 < background_floor)
            actual_ink += int(sum(actual_pixel) / 3 < background_floor)

    mean_difference = difference / (pixels * 3 * 255)
    changed_ratio = changed / pixels
    ink_loss = max(0.0, (reference_ink - actual_ink) / reference_ink) if reference_ink else 0.0
    failed = (
        mean_difference > region.max_mean_difference
        or changed_ratio > region.max_changed_ratio
        or ink_loss > region.max_ink_loss
    )
    warning = (
        mean_difference > region.max_mean_difference / 2
        or changed_ratio > region.max_changed_ratio / 2
        or ink_loss > region.max_ink_loss / 2
    )
    status = (
        ComparisonStatus.FAILED
        if failed
        else ComparisonStatus.WARNING
        if warning
        else ComparisonStatus.PASSED
    )
    return RegionComparison(
        name=region.name,
        status=status,
        mean_difference=mean_difference,
        changed_ratio=changed_ratio,
        ink_loss=ink_loss,
    )


__all__ = [
    "ComparisonStatus",
    "RegionComparison",
    "VisualComparison",
    "VisualRegion",
    "compare_rgb_buffers",
]
