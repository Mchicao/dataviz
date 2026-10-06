"""Automatización prudente de Power BI Desktop normal.

Nunca crea una segunda sesión de Desktop: una instancia existente puede tener
cambios sin guardar y no es seguro abrir otro PBIP encima de ella.
"""

import csv
import io
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from pywinauto import Application

    PYWINAUTO_AVAILABLE = True
except ImportError:
    PYWINAUTO_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesktopInstance:
    """Proceso detectado de Power BI Desktop normal."""

    process_id: int
    image_name: str


def find_desktop_instances() -> list[DesktopInstance]:
    """Lista procesos PBIDesktop sin terminar ni modificar procesos existentes."""
    if not hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = 0
    else:
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq PBIDesktop.exe", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=creationflags,
        )
    except OSError as error:
        logger.warning("No se pudo inspeccionar Power BI Desktop: %s", error)
        return []

    instances: list[DesktopInstance] = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 2 or row[0].lower() != "pbidesktop.exe":
            continue
        try:
            instances.append(DesktopInstance(process_id=int(row[1]), image_name=row[0]))
        except ValueError:
            logger.debug("Fila tasklist no interpretable: %s", row)
    return instances


class DesktopAutomator:
    """Abre un PBIP sólo cuando no hay otra sesión de Power BI Desktop."""

    def __init__(self, pbi_desktop_path: str | None = None):
        self.pbi_desktop_path = pbi_desktop_path

    def _can_start_new_instance(self) -> bool:
        instances = find_desktop_instances()
        if not instances:
            return True
        ids = ", ".join(str(instance.process_id) for instance in instances)
        logger.error(
            "Hay una instancia activa de Power BI Desktop (PID %s). "
            "Reutilízala manualmente o ciérrala tras guardar; no se abrirá otra instancia.",
            ids,
        )
        return False

    def convert_pbip_to_pbix(self, pbip_file_path: Path, timeout_seconds: int = 60) -> bool:
        """Abre un PBIP en Desktop normal y solicita Guardar como sólo si es seguro."""
        if not PYWINAUTO_AVAILABLE:
            logger.error("pywinauto no está instalado; no se puede automatizar Desktop.")
            return False

        pbip_file_path = Path(pbip_file_path)
        if not pbip_file_path.exists():
            logger.error("Archivo PBIP no encontrado: %s", pbip_file_path)
            return False
        if not self._can_start_new_instance():
            return False

        try:
            command = (
                f'"{self.pbi_desktop_path}" "{pbip_file_path}"'
                if self.pbi_desktop_path
                else str(pbip_file_path)
            )
            app = Application(backend="uia").start(command)
            main_window = app.window(title_re=".*Power BI Desktop")
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if main_window.exists(timeout=1):
                    break
                time.sleep(1)
            if not main_window.exists():
                logger.error("Power BI Desktop normal no cargó antes del timeout.")
                return False

            main_window.type_keys("%f")
            time.sleep(1)
            main_window.type_keys("a")
            time.sleep(2)
            save_as_dialog = app.window(title="Guardar como")
            if not save_as_dialog.exists(timeout=10):
                main_window.type_keys("{F12}")
            if not save_as_dialog.exists(timeout=5):
                logger.error("No se pudo abrir el diálogo Guardar como.")
                return False
            save_as_dialog.type_keys("{ENTER}")
            logger.info("Comando Guardar como enviado; Desktop queda abierto para revisión humana.")
            return True
        except Exception as error:
            logger.error("Error en automatización de Desktop: %s", error)
            return False
