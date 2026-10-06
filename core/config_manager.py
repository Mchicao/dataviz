import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Centralized configuration management.
    Ensures critical credentials are loaded from environment variables.
    """

    @staticmethod
    def get_env_var(key: str, default: str = None, required: bool = False) -> str:
        """
        Get an environment variable.

        Args:
            key: The name of the environment variable.
            default: Default value if not found.
            required: If True, raises ValueError if not found.

        Returns:
            The value of the environment variable.
        """
        value = os.getenv(key, default)

        if required and not value:
            raise ValueError(f"Missing required environment variable: {key}")

        return value

    @property
    def azure_client_id(self) -> str:
        return self.get_env_var("AZURE_CLIENT_ID", required=False)

    @property
    def azure_tenant_id(self) -> str:
        return self.get_env_var("AZURE_TENANT_ID", required=False)

    @property
    def azure_client_secret(self) -> str:
        return self.get_env_var("AZURE_CLIENT_SECRET", required=False)

    @property
    def pbi_workspace_id(self) -> str:
        return self.get_env_var("PBI_WORKSPACE_ID", required=False)


# Singleton instance
# Singleton instance
config = ConfigManager()

# --- LEGACY CONSTANTS (Migration Logic) ---

# --- RUTAS BASE ---
BASE_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = BASE_DIR.parent.absolute()

# --- RUTAS DE ARCHIVOS ---
RUTA_EXCEL_AUDITORIA = PROJECT_ROOT / "Auditoria_Tableau_Export.xlsx" # Adjusted path to root
RUTA_EXCEL_SMART_MAPPING = PROJECT_ROOT / "Smart_Mapping_Suggestions.xlsx"

# --- CONFIGURACIÓN DE IA ---
MODELO_IA_DEFAULT = "gemini-1.5-flash"
DELAY_API_IA = 1.0

# --- CONFIGURACIÓN DE POWER BI / TOM ---
PREFIJO_MEDIDA_MIGRADA = "(T)"
NOMBRE_TABLA_MEDIDAS = "_Medidas_Tableau"
USE_TOM = os.getenv("USE_TOM", "0").strip().lower() in ("1", "true", "yes")

_tom_dirs_raw = os.getenv("TOM_DLL_DIRS", "")
TOM_DLL_DIRECTORIES = [p.strip() for p in _tom_dirs_raw.split(os.pathsep) if p.strip()]

DEFAULT_REDSHIFT_SERVER = os.getenv("REDSHIFT_SERVER", "10.190.65.25")
DEFAULT_REDSHIFT_DATABASE = os.getenv("REDSHIFT_DATABASE", "rds_dwh_qa")
DEFAULT_REDSHIFT_SCHEMA = os.getenv("REDSHIFT_SCHEMA", "public")
DEFAULT_M_PROVIDER_FUNCTION = os.getenv("PBI_M_PROVIDER", "AmazonRedshift.Database")

DEV_MODE = os.getenv("DEV_MODE", "1").strip().lower() in ("1", "true", "yes")

# --- FILES ---
FORMATO_TIMESTAMP_BACKUP = "%Y%m%d_%H%M%S"
