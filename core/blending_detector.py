import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class BlendedWorksheet:
    worksheet_name: str
    primary_datasource: str
    secondary_datasources: list[str]

    @property
    def all_datasources(self) -> list[str]:
        return [self.primary_datasource] + self.secondary_datasources


def detect_blending(twb_path: str) -> list[BlendedWorksheet]:
    """
    Detecta hojas de trabajo que utilizan Data Blending (múltiples fuentes de datos).
    """
    if not os.path.exists(twb_path):
        return []

    try:
        tree = ET.parse(twb_path)
        root = tree.getroot()
    except Exception:
        return []

    blended_sheets = []

    for sheet in root.findall(".//worksheets/worksheet"):
        sheet_name = sheet.get("name")

        # Encontrar todas las dependencias de datasource
        datasources = set()

        # Estructura típica: <table><view><datasource-dependencies datasource='...'>
        for dep in sheet.findall(".//datasource-dependencies"):
            ds_name = dep.get("datasource")
            if ds_name and "Parameters" not in ds_name:
                datasources.add(ds_name)

        if len(datasources) > 1:
            # Identificar primaria (usualmente la primera en lista o analisis mas profundo)
            # Simplificamos asumiendo orden arbitrario por ahora
            ds_list = list(datasources)
            if sheet_name:
                blended_sheets.append(
                    BlendedWorksheet(
                        worksheet_name=sheet_name,
                        primary_datasource=ds_list[0],
                        secondary_datasources=ds_list[1:],
                    )
                )

    return blended_sheets
