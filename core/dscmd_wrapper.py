"""
Wrapper para interactuar con DAX Studio Command Line (dscmd).
Permite automatizar la extracción de métricas VPAX y ejecutar consultas DAX contra modelos tabulares.

Requisitos:
- DAX Studio instalado y 'dscmd.exe' accesible en el PATH o en una ruta configurada.
"""

import logging
import os
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VpaxResult:
    """Resultado de la generación de VPAX."""

    success: bool
    output_path: str
    message: str
    size_bytes: int = 0


class DaxStudioWrapper:
    def __init__(self, dscmd_path: str | None = None):
        """
        Inicializa el wrapper.

        Args:
            dscmd_path: Ruta al ejecutable dscmd.exe. Si es None, busca en rutas default.
        """
        self.dscmd_path = dscmd_path or self._find_dscmd()

    def _find_dscmd(self) -> str:
        """Intenta localizar dscmd.exe en rutas comunes."""
        common_paths = [
            r"C:\Program Files\DAX Studio\dscmd.exe",
            r"C:\Program Files (x86)\DAX Studio\dscmd.exe",
            os.path.join(os.getenv("LOCALAPPDATA", ""), r"Programs\DAX Studio\dscmd.exe"),
        ]

        for path in common_paths:
            if os.path.exists(path):
                return path

        # Fallback: asumir que está en PATH
        return "dscmd"

    def is_installed(self) -> bool:
        """Verifica si DAX Studio CLI está disponible."""
        try:
            cmd = [self.dscmd_path, "--help"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.returncode == 0
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.error(f"Error verificando dscmd: {e}")
            return False

    def export_vpax(self, connection_string: str, output_path: str) -> VpaxResult:
        """
        Exporta métricas VPAX desde un modelo conectado.

        Args:
            connection_string: Connection string del modelo (ej: "Provider=MSOLAP;Data Source=localhost:port;...")
            output_path: Ruta destino del archivo .vpax

        Returns:
            VpaxResult con el estado de la operación.
        """
        if not self.is_installed():
            return VpaxResult(False, output_path, "DAX Studio (dscmd) no encontrado.")

        # Comando para DAX Studio 3.x: dscmd vpax <output_path> -c <connection_string>
        cmd = [self.dscmd_path, "vpax", output_path, "-c", connection_string]

        try:
            logger.info(f"Ejecutando exportación VPAX a {output_path}")
            # print(f"DEBUG: running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                if os.path.exists(output_path):
                    size = os.path.getsize(output_path)
                    return VpaxResult(True, output_path, "Exportación exitosa", size)
                else:
                    return VpaxResult(False, output_path, "Comando exitoso pero archivo no creado")
            else:
                err_msg = result.stderr or result.stdout
                logger.error(f"Error dscmd: {err_msg}")
                return VpaxResult(False, output_path, f"Error dscmd: {err_msg}")

        except Exception as e:
            logger.error(f"Excepción ejecutando dscmd: {e}")
            return VpaxResult(False, output_path, f"Excepción: {e}")

    def execute_dax(self, connection_string: str, dax_query: str, format: str = "csv") -> str:
        """
        Ejecuta una query DAX y retorna el resultado en crudo.

        Args:
            connection_string: Connection string del modelo.
            dax_query: Consulta DAX a ejecutar.
            format: Formato de salida (csv por defecto).

        Returns:
            String con el resultado (ej. CSV content).
        """
        if not self.is_installed():
            raise RuntimeError("DAX Studio (dscmd) no encontrado.")

        # Crear archivo temporal para la query si es muy larga
        # O pasarlo como argumento si es corta. Recomendado archivo.
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".dax", delete=False) as tmp_query:
            tmp_query.write(dax_query)
            query_path = tmp_query.name

        try:
            cmd = [
                self.dscmd_path,
                "csv",  # Comando para salida CSV
                f"/c:{connection_string}",
                f"/i:{query_path}",
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"Error ejecutando DAX: {result.stderr or result.stdout}")

            return result.stdout

        finally:
            if os.path.exists(query_path):
                os.remove(query_path)


# ============ CLI Quick Test ============
if __name__ == "__main__":
    wrapper = DaxStudioWrapper()
    if wrapper.is_installed():
        print(f"✅ dscmd encontrado en: {wrapper.dscmd_path}")
    else:
        print("❌ dscmd no encontrado. Asegúrate de instalar DAX Studio.")
