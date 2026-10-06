import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("TWBXExtractor")

# Límite por defecto de descompresión: 500 MB (500 * 1024 * 1024)
DEFAULT_MAX_UNCOMPRESSED_SIZE = 524288000
# Límite por defecto de ratio de expansión (ej. 100x)
DEFAULT_MAX_EXPANSION_RATIO = 100


class TWBXExtractor:
    """Clase encargada de desempaquetar archivos .twbx de Tableau de forma segura,

    aplicando mitigación ante vulnerabilidades Zip Slip, Zip Bomb y generando
    un hash estable de procedencia (SHA-256) libre de metadatos locales.
    """

    def __init__(
        self,
        max_size_bytes: int = DEFAULT_MAX_UNCOMPRESSED_SIZE,
        max_ratio: float = DEFAULT_MAX_EXPANSION_RATIO,
    ) -> None:
        self.max_size_bytes = max_size_bytes
        self.max_ratio = max_ratio

    def calculate_sha256(self, file_path: Path) -> str:
        """Calcula el hash SHA-256 de un archivo de manera eficiente en bloques."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def extract(self, twbx_path_or_file: Any, output_dir: Path) -> Path:
        """Extrae de forma segura el archivo .twb principal y otros recursos del .twbx.

        Retorna la ruta al archivo .twb principal extraído.
        """
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        twb_file_path: Path | None = None
        total_uncompressed_size = 0

        # Si recibimos una ruta (str o Path), resolvemos su tamaño para validación de ratio.
        # Si es un file-like object, medimos lo que podamos.
        archive_size = 0
        if isinstance(twbx_path_or_file, (str, Path)):
            archive_path = Path(twbx_path_or_file).resolve()
            if not zipfile.is_zipfile(archive_path):
                raise ValueError(
                    f"El archivo especificado no es un archivo ZIP válido: {archive_path}"
                )
            archive_size = archive_path.stat().st_size
            zip_ref = zipfile.ZipFile(archive_path, "r")
        else:
            zip_ref = zipfile.ZipFile(twbx_path_or_file, "r")

        try:
            with zip_ref as zf:
                # 1. Validación de seguridad preliminar (Zip Bomb / Expansion ratio)
                for info in zf.infolist():
                    total_uncompressed_size += info.file_size

                if total_uncompressed_size > self.max_size_bytes:
                    raise ValueError(
                        f"Límite de tamaño descomprimido superado. "
                        f"Límite: {self.max_size_bytes} bytes, Requerido: {total_uncompressed_size} bytes."
                    )

                if archive_size > 0:
                    ratio = total_uncompressed_size / archive_size
                    if ratio > self.max_ratio:
                        raise ValueError(
                            f"Ratio de expansión anormal detectado (posible Zip Bomb). "
                            f"Ratio: {ratio:.2f}, Límite: {self.max_ratio}"
                        )

                # 2. Extracción segura con validación de Zip Slip
                for member in zf.namelist():
                    # Resolver ruta de destino final
                    target_path = Path(output_dir / member).resolve()

                    # Validación de Zip Slip: debe estar estrictamente dentro de output_dir
                    try:
                        target_path.relative_to(output_dir)
                    except ValueError as e:
                        raise ValueError(
                            f"Ruta de extracción maliciosa detectada / Ruta insegura dentro del TWBX: {member} (Zip Slip)"
                        ) from e

                    # Realizar la extracción del miembro de forma segura
                    if member.endswith("/"):
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)

                        # Si es el .twb principal (el XML de Tableau), lo guardamos
                        if member.lower().endswith(".twb"):
                            twb_file_path = target_path

            if not twb_file_path:
                raise FileNotFoundError(
                    "No se encontró ningún archivo .twb principal en el paquete .twbx"
                )

            logger.info(f"Extracción segura de TWBX completada. TWB principal: {twb_file_path}")
            return twb_file_path

        except Exception as ex:
            logger.error("Error durante la extracción del TWBX. Limpiando residuos...")
            cleanup_temp_dir(str(output_dir))
            raise ex


def extract_twb_from_twbx(file_obj) -> tuple[str, str]:
    """Función de compatibilidad heredada.

    Recibe un objeto tipo archivo (file-like object) o ruta de un .twbx.
    Extrae el .twb contenido y retorna la ruta a un archivo temporal y el directorio.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        extractor = TWBXExtractor()
        twb_path = extractor.extract(file_obj, Path(temp_dir))
        return str(twb_path), temp_dir
    except Exception as e:
        cleanup_temp_dir(temp_dir)
        raise e


def cleanup_temp_dir(temp_dir: str) -> None:
    """Limpia el directorio temporal creado."""
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
