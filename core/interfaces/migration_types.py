from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class MigrationMode(StrEnum):
    PBIRS = "PBIRS"  # Legacy Report Server (report.json)
    PBIR = "PBIR"  # Modern Cloud (folder structure)


class PBIXGenerationMode(StrEnum):
    MANUAL = "MANUAL"  # Opción D: Usuario genera el PBIX manualmente
    AUTOMATED_DESKTOP = "AUTOMATED_DESKTOP"  # Opción B.1: Automatización via UI/Desktop


class DataSourceConfig(BaseModel):
    """Configuration for a specific data source in the migration."""

    original_caption: str
    new_caption: str | None = None
    connection_type: str
    server: str | None = None
    database: str | None = None

    # Custom SQL optimization
    optimized_sql: str | None = None

    # Credentials (should be handled securely, this is just config carrier)
    username: str | None = None


class MigrationRequest(BaseModel):
    """
    Contract defining a migration job.
    This replaces loose dictionary arguments in the engine.
    """

    project_name: str = Field(..., min_length=1)
    source_file: Path
    output_dir: Path
    mode: MigrationMode = MigrationMode.PBIR
    pbix_mode: PBIXGenerationMode = PBIXGenerationMode.MANUAL

    # Feature Flags
    convert_calculations: bool = True
    enable_bpa: bool = False
    cleanup_on_failure: bool = True  # Nueva flag solicitada

    # Context
    datasources: list[DataSourceConfig] = Field(default_factory=list)

    @field_validator("source_file")
    def validate_source(cls, v):
        if not v.exists():
            raise ValueError(f"Source file {v} does not exist")
        if v.suffix.lower() not in [".twb", ".twbx"]:
            raise ValueError("File must be .twb or .twbx")
        return v

    class Config:
        frozen = True
