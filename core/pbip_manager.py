import json
import os
import shutil
import stat
import time
import xml.etree.ElementTree as ET
from typing import Any

from . import tom_engine
from .report_json_generator import generate_legacy_report, remove_pbir_structure
from .visual_mapper import VisualMapper


class PBIPManager:
    def __init__(self, config):
        """
        Args:
            config: Objeto MigrationRequest (Pydantic) de core.interfaces.migration_types
        """
        self.config = config
        from core.interfaces.migration_types import MigrationMode

        self.project_name = config.project_name
        self.output_dir = str(config.output_dir)

        # Determine strict PBIR mode based on enum
        # Note: PBIR requires PBI Desktop with "Enhanced report format" preview feature enabled
        self.use_pbir = config.mode == MigrationMode.PBIR

        self.pbip_folder = os.path.join(self.output_dir, f"{self.project_name}.pbip")
        self.report_folder = os.path.join(self.output_dir, f"{self.project_name}.Report")
        self.semantic_model_folder = os.path.join(
            self.output_dir, f"{self.project_name}.SemanticModel"
        )
        self.mapper = VisualMapper()

    def _remove_readonly(self, func, path, excinfo):
        """Función auxiliar para forzar borrado en Windows/OneDrive"""
        os.chmod(path, stat.S_IWRITE)
        func(path)

    def _force_delete(self, path):
        """Intenta borrar una carpeta con reintentos para OneDrive"""
        if os.path.exists(path):
            try:
                shutil.rmtree(path, onexc=self._remove_readonly)
            except Exception:
                # Si falla, esperamos 1 segundo (sync de OneDrive) y reintentamos
                time.sleep(1)
                try:
                    shutil.rmtree(path, onexc=self._remove_readonly)
                except Exception as e:
                    print(f"Advertencia: No se pudo borrar {path}. {e}")

    def create_structure(self, audit_results=None):
        """Crea carpetas base y el archivo model.bim con estructuras de tablas si están disponibles."""
        try:
            self._execute_creation(audit_results)
        except Exception as e:
            if self.config.cleanup_on_failure:
                print(f"❌ Error durante creación. Limpiando {self.output_dir}...")
                self._force_delete(self.output_dir)
            raise e

    def _execute_creation(self, audit_results):
        """Lógica interna de creación."""
        self._force_delete(self.report_folder)
        self._force_delete(self.semantic_model_folder)

        # Eliminar archivo .pbip existente con reintentos (OneDrive puede bloquearlo)
        if os.path.exists(self.pbip_folder):
            for attempt in range(3):
                try:
                    os.chmod(self.pbip_folder, stat.S_IWRITE)
                    os.remove(self.pbip_folder)
                    break
                except Exception as e:
                    if attempt == 2:
                        raise RuntimeError(f"No se pudo eliminar {self.pbip_folder}: {e}")
                    time.sleep(0.5)

        os.makedirs(self.report_folder, exist_ok=True)
        os.makedirs(self.semantic_model_folder, exist_ok=True)

        # 1. .pbip - Ahora con $schema requerido
        pbip_content = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{self.project_name}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        }
        with open(self.pbip_folder, "w", encoding="utf-8") as f:
            json.dump(pbip_content, f, indent=4)

        # Validar integridad del archivo .pbip
        with open(self.pbip_folder, encoding="utf-8") as f:
            content = f.read()
            if not content or content[0] != "{" or "\x00" in content:
                raise RuntimeError(
                    f"Archivo .pbip corrupto después de escritura: {self.pbip_folder}"
                )

        # 2. definition.pbir
        with open(os.path.join(self.report_folder, "definition.pbir"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
                    "version": "4.0",
                    "datasetReference": {
                        "byPath": {"path": f"../{self.project_name}.SemanticModel"}
                    },
                },
                f,
                indent=4,
            )

        # 3. definition.pbism
        with open(
            os.path.join(self.semantic_model_folder, "definition.pbism"), "w", encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
                    "version": "4.2",
                    "settings": {},
                },
                f,
                indent=4,
            )

        # 4. .platform files (requerido por pbi-tools)
        import uuid

        logical_id = str(uuid.uuid4())

        # .platform para Report
        with open(os.path.join(self.report_folder, ".platform"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                    "metadata": {"type": "Report", "displayName": self.project_name},
                    "config": {"version": "2.0", "logicalId": logical_id},
                },
                f,
                indent=2,
            )

        # .platform para SemanticModel
        with open(
            os.path.join(self.semantic_model_folder, ".platform"), "w", encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                    "metadata": {"type": "SemanticModel", "displayName": self.project_name},
                    "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
                },
                f,
                indent=2,
            )

        # 5. .pbixproj.json (manifest para pbi-tools)
        pbixproj_path = os.path.join(self.output_dir, ".pbixproj.json")
        with open(pbixproj_path, "w", encoding="utf-8") as f:
            json.dump(
                {"version": "1.0.0", "settings": {"model": {"serializationMode": "Default"}}},
                f,
                indent=2,
            )

        # 4. model.bim (Poblado con tablas de auditoría)
        tables = []
        if audit_results:
            for ds in audit_results.datasources:
                # Sanitizar nombre de tabla
                t_name = ds.caption.replace("[", "").replace("]", "").replace(".", " ").strip()
                if "Parameters" in t_name:
                    continue

                columns = []
                for col in ds.columns:
                    # Excluir cálculos de Tableau (no existen en la BBDD origen, causarían error de refresh)
                    # Excluir columnas no usadas (optimización básica)
                    if col.is_used and not col.is_calculated:
                        c_name = col.local_name.replace("[", "").replace("]", "").strip()
                        # Mapear tipos básicos
                        tp = "string"
                        if col.data_type in ["integer", "real", "i4", "i2", "r8"]:
                            tp = "int64"
                        elif col.data_type in ["date", "datetime"]:
                            tp = "dateTime"
                        elif col.data_type in ["boolean", "bool"]:
                            tp = "boolean"

                        columns.append({"name": c_name, "dataType": tp, "sourceColumn": col.name})

                if columns:
                    # Generar Expresión M real
                    from core.tom_engine import generate_m_expression

                    schema = ds.schema or "public"  # Fallback schema
                    table_nm = ds.table or t_name

                    m_code = generate_m_expression(
                        connection_class=ds.connection_class or "genérico",
                        server=getattr(ds, "server", "SERVER_PLACEHOLDER"),
                        db=getattr(ds, "database", "DB_PLACEHOLDER"),
                        schema=schema,
                        table=table_nm,
                        custom_sql=ds.custom_sql,
                    )

                    tables.append(
                        {
                            "name": t_name,
                            "columns": columns,
                            "partitions": [
                                {
                                    "name": "Partición Importada",
                                    "mode": "import",
                                    "source": {"type": "m", "expression": m_code},
                                }
                            ],
                        }
                    )

        # 4. model.bim (ELIMINADO: Se genera vía TOM para evitar conflictos con TMDL)
        # Si se requiere model.bim estático para auditoría sin TOM, debe manejarse aparte.
        pass

    def generate_semantic_model_with_tom(self, tableau_xml_path, dll_path):
        from core.interfaces.migration_types import MigrationMode

        # Determine Legacy Mode (TMSL for PBIRS) vs TMDL (Cloud)
        is_legacy = self.config.mode == MigrationMode.PBIRS

        return tom_engine.ejecutar_migracion_tom(
            tableau_xml_path,
            self.semantic_model_folder,
            dll_path,
            legacy_mode=is_legacy,
        )

    @staticmethod
    def filter_migration_items(
        items: list[dict[str, Any]], page_names: list[str] | None
    ) -> list[dict[str, Any]]:
        """Filtra dashboards u hojas por nombre sin alterar su contenido."""
        if not page_names:
            return items
        requested = {name.casefold().strip() for name in page_names}
        return [
            item
            for item in items
            if str(item.get("Nombre") or item.get("Nombre_Hoja") or "").casefold().strip()
            in requested
        ]

    def generate_report_visuals(
        self,
        tableau_xml_path: str,
        worksheets_data: list[dict[str, Any]] | None = None,
        page_names: list[str] | None = None,
    ) -> None:
        """
        Genera visuales del reporte.
        Si use_pbir=False, genera report.json legacy (compatible PBIRS).
        Si use_pbir=True, genera estructura PBIR con carpetas usando el nuevo módulo.
        """
        if not self.use_pbir:
            # Modo Legacy: Generar report.json único
            generate_legacy_report(
                report_folder=self.report_folder,
                tableau_xml_path=tableau_xml_path,
                semantic_model_name=f"{self.project_name}.SemanticModel",
                project_name=self.project_name,
            )
            # Asegurar que no haya estructura PBIR
            remove_pbir_structure(self.report_folder)
            return

        # Modo PBIR: Usar nuevo módulo robusto
        from core import pbir_logic

        # Extraer datos de worksheets si no se proporcionaron
        if worksheets_data is None:
            worksheets_data = self._extract_worksheets_data(tableau_xml_path)
        worksheets_data = self.filter_migration_items(worksheets_data, page_names)

        if worksheets_data:
            pbir_logic.crear_paginas_desde_tableau(
                carpeta_report=self.report_folder,
                hojas_tableau=worksheets_data,
                carpeta_semantic_model=self.semantic_model_folder,
                ruta_tableau=tableau_xml_path,
            )
        else:
            # Fallback: Crear estructura básica
            self._create_basic_pbir_structure(tableau_xml_path)

    def _extract_worksheets_data(self, tableau_xml_path):
        """Extrae datos de worksheets usando el parser robusto."""
        try:
            from core.tableau_parser import TableauReader

            reader = TableauReader(tableau_xml_path)
            return reader.get_detailed_worksheets()
        except Exception:
            # Fallback legacy (should not happen usually)
            return self._extract_worksheets_data_legacy(tableau_xml_path)

    def _extract_worksheets_data_legacy(self, tableau_xml_path):
        """Legacy extraction logic (renamed)."""
        try:
            tree = ET.parse(tableau_xml_path)
            root = tree.getroot()
        except Exception:
            return []

        worksheets_data = []
        worksheets = root.findall(".//worksheet")

        for ws in worksheets:
            ws_name = ws.get("name", "Sin nombre")

            # Detectar tipo de marca
            mark_elem = ws.find(".//mark")
            tipo_visual = mark_elem.get("class", "text") if mark_elem is not None else "text"

            # Extraer campos (simplificado)
            campos_filas = []
            campos_columnas = []
            campos_medidas = []

            # 1. Parsing mejorado de filas/columnas desde <rows> y <cols>
            rows_elem = ws.find(".//rows")
            cols_elem = ws.find(".//cols")

            if rows_elem is not None and rows_elem.text:
                for field_ref in rows_elem.text.split("]"):
                    if "[" in field_ref:
                        clean = field_ref.split("[")[-1].strip()
                        if clean:
                            campos_filas.append(clean)

            if cols_elem is not None and cols_elem.text:
                for field_ref in cols_elem.text.split("]"):
                    if "[" in field_ref:
                        clean = field_ref.split("[")[-1].strip()
                        if clean:
                            campos_columnas.append(clean)

            # 2. Parsing de encodings (color, size, text) para medidas
            for pane in ws.findall(".//pane"):
                for enc in pane.findall(".//encodings/*"):
                    field_name = enc.get("column")
                    if field_name:
                        clean = field_name.replace("[", "").replace("]", "")
                        if enc.tag in ("size", "color", "text"):
                            campos_medidas.append(clean)
                        # También añadimos a filas/columnas si es row/column encoding
                        elif enc.tag == "row-header":
                            campos_filas.append(clean)

            # 3. Fallback original: datasource-dependencies (si todo lo anterior falla)
            if not campos_filas and not campos_columnas and not campos_medidas:
                for col in ws.findall(".//datasource-dependencies//column"):
                    role = col.get("role", "")
                    raw_name = col.get("name", "")
                    clean_name = raw_name.replace("[", "").replace("]", "")
                    if role == "measure":
                        campos_medidas.append(clean_name)
                    else:
                        campos_filas.append(clean_name)

            # Limpiar duplicados manteniendo orden
            campos_filas = list(dict.fromkeys(campos_filas))
            campos_columnas = list(dict.fromkeys(campos_columnas))
            campos_medidas = list(dict.fromkeys(campos_medidas))

            # Detectar fuente de datos
            ds_dep = ws.find(".//datasource-dependencies")
            fuente_datos = ds_dep.get("datasource", "") if ds_dep is not None else ""

            worksheets_data.append(
                {
                    "Nombre": ws_name,
                    "Nombre_Hoja": ws_name,
                    "Tipo_Visual": tipo_visual,
                    "Tipo_Marca": tipo_visual,
                    "Campos_Filas": campos_filas,
                    "Campos_Columnas": campos_columnas,
                    "Campos_Medidas": campos_medidas,
                    "Fuente_Datos": fuente_datos,
                }
            )

        return worksheets_data

    def _create_basic_pbir_structure(self, tableau_xml_path):
        """Fallback: Crea estructura PBIR básica sin data binding complejo."""
        try:
            tree = ET.parse(tableau_xml_path)
            reader = tree.getroot()
        except Exception:
            return

        first_ds = reader.find(".//datasource")
        ds_name = "Data"
        if first_ds is not None:
            raw = first_ds.get("caption", first_ds.get("name"))
            if raw:
                ds_name = raw.replace("[", "").replace("]", "").replace(".", "").strip()

        worksheets = reader.findall(".//worksheet")

        pages_dir = os.path.join(self.report_folder, "definition", "pages")
        os.makedirs(pages_dir, exist_ok=True)

        for i, ws in enumerate(worksheets):
            ws_name = ws.get("name", f"Page {i}")
            safe_name = "".join([c for c in ws_name if c.isalnum()]) or f"Page{i}"

            page_dir = os.path.join(pages_dir, safe_name)
            os.makedirs(page_dir, exist_ok=True)

            with open(os.path.join(page_dir, "page.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
                        "name": safe_name,
                        "displayName": ws_name,
                        "displayOption": "ActualSize",
                        "height": 1000,
                        "width": 1300,
                    },
                    f,
                    indent=4,
                )

            visuals_dir = os.path.join(page_dir, "visuals")
            os.makedirs(visuals_dir, exist_ok=True)

            vis_config = self.mapper.map_worksheet(ws, ds_name)
            vis_folder = os.path.join(visuals_dir, vis_config["name"])
            os.makedirs(vis_folder, exist_ok=True)

            with open(os.path.join(vis_folder, "visual.json"), "w", encoding="utf-8") as f:
                json.dump(vis_config, f, indent=4)
