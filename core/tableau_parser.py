import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Any


def _join_title_runs(runs) -> str:
    """Concatena los ``<run>`` de un título Tableau limpiando artefactos.

    Tableau inserta un carácter ``Æ`` (U+00C6) como separador visual entre
    líneas de título multilínea (un ``<run>`` propio con ``'Æ '``). Ese carácter
    no es válido en un título Power BI y se reemplaza por un salto de línea.
    También se normalizan espacios sobrantes.
    """
    text = "".join((run.text or "") for run in runs).strip()
    # Separador visual multilínea de Tableau -> salto de línea real.
    text = text.replace("\u00c6", "\n")
    # Colapsar espacios/saltos redundantes que deja la concatenación de runs.
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return text.strip()


@dataclass(frozen=True)
class TableauWorksheet:
    """Hoja de Tableau tipada: campos, marca y encodings."""

    name: str
    mark_type: str
    visual_type: str
    datasource: str
    rows: list[str]
    columns: list[str]
    measures: list[str]
    field_labels: dict[str, str]
    encodings: dict[str, list[str]]
    filters: list[dict[str, Any]]
    row_measures: list[str] = field(default_factory=list)
    column_measures: list[str] = field(default_factory=list)
    panel_measures: list[str] = field(default_factory=list)
    sort_contract: dict[str, str] = field(default_factory=dict)
    title_text: str = ""

    @classmethod
    def from_details(cls, details: dict[str, Any]) -> "TableauWorksheet":
        """Construye la hoja tipada desde el dict que produce _extract_worksheet_info."""
        return cls(
            name=details["Nombre_Hoja"],
            mark_type=details["Tipo_Marca"],
            visual_type=details["Tipo_Visual"],
            datasource=details["Fuente_Datos"],
            rows=details["Campos_Filas"],
            columns=details["Campos_Columnas"],
            measures=details["Campos_Medidas"],
            field_labels=details.get("Etiquetas_Campos", {}),
            encodings=details["Encodings"],
            filters=details["Filtros"],
            row_measures=details.get("Campos_Medidas_Filas", []),
            column_measures=details.get("Campos_Medidas_Columnas", []),
            panel_measures=details.get("Paneles_Medidas", []),
            sort_contract=details.get("Orden", {}),
            title_text=details.get("Texto_Titulo", ""),
        )

    def to_details(self) -> dict[str, Any]:
        """Devuelve el dict de contrato que consume el generador PBIR."""
        return {
            "Nombre_Hoja": self.name,
            "Tipo_Marca": self.mark_type,
            "Tipo_Visual": self.visual_type,
            "Fuente_Datos": self.datasource,
            "Campos_Filas": self.rows,
            "Campos_Columnas": self.columns,
            "Campos_Medidas": self.measures,
            "Campos_Medidas_Filas": self.row_measures,
            "Campos_Medidas_Columnas": self.column_measures,
            "Paneles_Medidas": self.panel_measures,
            "Orden": self.sort_contract,
            "Etiquetas_Campos": self.field_labels,
            "Encodings": self.encodings,
            "Filtros": self.filters,
            "Texto_Titulo": self.title_text,
        }


@dataclass(frozen=True)
class TableauZone:
    """Zona de un dashboard Tableau vinculada a una worksheet."""

    worksheet_name: str
    order: int
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    z_order: int = 0
    floating: bool = False
    show_title: bool = True
    worksheet: TableauWorksheet | None = None

    def layout(self) -> dict[str, int] | None:
        """Devuelve sólo el rectángulo válido que entiende el visual PBIR."""
        x, y, width, height = self.x, self.y, self.width, self.height
        if x is None or y is None or width is None or height is None:
            return None
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "z_order": self.z_order,
        }

    def to_layout_dict(self) -> dict[str, Any]:
        """Serializa sólo los campos de layout (excluye el worksheet tipado)."""
        return {
            "worksheet_name": self.worksheet_name,
            "order": self.order,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "z_order": self.z_order,
            "floating": self.floating,
            "show_title": self.show_title,
        }


@dataclass
class TableauDashboard:
    """Dashboard Tableau con sus zonas en orden de documento."""

    name: str
    zones: list[TableauZone]
    controls: list[dict[str, Any]]
    objects: list[dict[str, Any]] = field(default_factory=list)
    title: dict[str, Any] | None = None


@dataclass
class TableauWorkbookIR:
    """Representación intermedia mínima para no perder la estructura del dashboard."""

    dashboards: list[TableauDashboard]
    standalone_worksheets: list[dict[str, Any]]

    def to_migration_items(
        self, worksheet_details: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Materializa el contrato del generador preservando zones, orden y layout."""
        items: list[dict[str, Any]] = []
        for dashboard in self.dashboards:
            worksheets: list[dict[str, Any]] = []
            for zone in dashboard.zones:
                # Zona tipada: usa el worksheet adjunto; fallback al dict externo.
                if zone.worksheet is not None:
                    worksheet = zone.worksheet.to_details()
                else:
                    worksheet = worksheet_details.get(zone.worksheet_name)
                if worksheet is None:
                    continue
                positioned = {
                    **worksheet,
                    "Layout": zone.layout(),
                    "Zona": zone.to_layout_dict(),
                    "Mostrar_Titulo": zone.show_title,
                }
                worksheets.append(positioned)
            items.append(
                {
                    "Tipo": "Dashboard",
                    "Nombre": dashboard.name,
                    "Hojas": worksheets,
                    "Zonas": [zone.to_layout_dict() for zone in dashboard.zones],
                    "Controles": dashboard.controls,
                    "Objetos": dashboard.objects,
                    "Titulo_Dashboard": dashboard.title,
                }
            )
        items.extend(
            {"Tipo": "Hoja", "Nombre": worksheet["Nombre_Hoja"], "Hojas": [worksheet]}
            for worksheet in self.standalone_worksheets
        )
        return items


class TableauReader:
    def __init__(self, file_path):
        self.file_path = file_path
        self.tree = ET.parse(file_path)
        self.root = self.tree.getroot()

    def get_datasources(self):
        """
        Lista las fuentes de datos únicas (Legacy simpler version).
        """
        datasources = set()
        for ds in self.root.findall(".//datasource"):
            name = ds.get("caption", ds.get("name"))
            if name and "Parameters" not in name:
                clean_name = name.replace("[", "").replace("]", "").replace(".", "").strip()
                datasources.add(clean_name)
        return list(datasources)

    def get_detailed_datasources(self):
        """
        Obtiene información detallada de los datasources (Ported from V2).
        Returns list of dicts: {Fuente_Datos_Tableau, DataSource_Name_Internal, Nombre_Tabla_Redshift, ...}
        """
        conexiones = []
        for ds in self.root.findall(".//datasource"):
            # Ignorar parámetros
            ds_name = ds.get("caption") or ds.get("name")
            if not ds_name or "Parameters" in ds_name:
                continue

            nombre_interno = ds.get("name", "")

            # Info base
            info_conn = {
                "Fuente_Datos_Tableau": ds_name,
                "DataSource_Name_Internal": nombre_interno,
                "Tipo": "Unknown",
                "Nombre_Tabla_Redshift": None,
                "Query_SQL_Completa": "",
                "Server": "",
                "Database": "",
                "Schema": "",
            }

            # 1. Connection info
            conn = ds.find(".//connection")
            if conn is not None:
                info_conn["Server"] = conn.get("server") or conn.get("host") or ""
                info_conn["Database"] = conn.get("dbname") or conn.get("database") or ""
                info_conn["Schema"] = conn.get("schema") or ""

                # 2. Relation info
                # A federated datasource wraps its real tables in a collection
                # relation.  The wrapper has no table/name, so selecting it
                # produces the old ``Unknown`` entity in the semantic model.
                relation = next(
                    (
                        item
                        for item in conn.findall(".//relation")
                        if item.get("type") in {"table", "text"}
                        and "[Extract]." not in (item.get("table") or "")
                    ),
                    conn.find(".//relation"),
                )
                if relation is not None:
                    rel_type = relation.get("type") or "Unknown"
                    info_conn["Tipo"] = rel_type

                    if rel_type == "text":
                        info_conn["Nombre_Tabla_Redshift"] = "CUSTOM SQL"
                        info_conn["Query_SQL_Completa"] = (
                            relation.text.strip() if relation.text else ""
                        )
                    else:
                        tabla_raw = relation.get("table") or relation.get("name")
                        if tabla_raw:
                            tabla_limpia = tabla_raw.replace("[", "").replace("]", "").rstrip("$")
                            info_conn["Nombre_Tabla_Redshift"] = tabla_limpia
                            if "." in tabla_limpia:
                                parts = tabla_limpia.split(".")
                                if len(parts) >= 2:
                                    info_conn["Schema"] = parts[0]

            # Fallback
            if not info_conn["Nombre_Tabla_Redshift"] and ds_name:
                if "_" in ds_name and " " not in ds_name:
                    info_conn["Nombre_Tabla_Redshift"] = ds_name

            conexiones.append(info_conn)
        return conexiones

    def get_worksheets(self):
        return self.root.findall(".//worksheet")

    def get_dashboards_structure(self):
        # ... (El código de dashboards se mantiene igual) ...
        dashboards = []
        all_sheets_names = {ws.get("name") for ws in self.root.findall(".//worksheet")}
        for dash in self.root.findall(".//dashboard"):
            dash_name = dash.get("name")
            contained_sheets = []
            for zone in dash.findall(".//zone"):
                zone_name = zone.get("name")
                if zone_name in all_sheets_names:
                    contained_sheets.append(zone_name)
            contained_sheets = list(set(contained_sheets))
            dashboards.append({"name": dash_name, "sheets": contained_sheets})
        return dashboards

    def get_workbook_ir(self) -> TableauWorkbookIR:
        """Construye el IR de dashboards antes de generar PBIR o degradar layout."""
        worksheet_names = {
            name
            for worksheet in self.root.findall(".//worksheet")
            if (name := worksheet.get("name")) is not None
        }
        dashboards: list[TableauDashboard] = []
        # Tableau conserva el orden visible de las pestañas en ``windows``.
        # Los nodos ``dashboard`` pueden estar serializados alfabéticamente,
        # por lo que su orden XML no representa el recorrido del usuario.
        dashboard_order = {
            window.get("name"): index
            for index, window in enumerate(self.root.findall(".//windows/window"))
            if window.get("class") == "dashboard" and window.get("name")
        }
        used_worksheets: set[str] = set()

        for dashboard in self.root.findall(".//dashboard"):
            zones: list[TableauZone] = []
            zones_root = dashboard.find("./zones")
            if zones_root is None:
                continue
            for zone in zones_root.findall(".//zone"):
                worksheet_name = zone.get("name")
                if not isinstance(worksheet_name, str) or worksheet_name not in worksheet_names:
                    continue
                if not self._is_worksheet_zone(zone):
                    continue
                # ZONE-ORDER-01: el orden de worksheets no debe desplazarse
                # por títulos, contenedores, leyendas u objetos web intermedios.
                zones.append(self._extract_zone(zone, worksheet_name, len(zones)))
                used_worksheets.add(worksheet_name)
            controls = self._extract_dashboard_controls(dashboard, zones_root)
            title = self._extract_dashboard_title(dashboard, zones_root)
            objects = self._extract_dashboard_objects(zones_root)
            dashboards.append(
                TableauDashboard(
                    name=dashboard.get("name") or "Dashboard Sin Nombre",
                    zones=zones,
                    controls=controls,
                    objects=objects,
                    title=title,
                )
            )

        dashboards.sort(
            key=lambda dashboard: dashboard_order.get(dashboard.name, len(dashboard_order))
        )

        standalone = []
        for worksheet in self.root.findall(".//worksheet"):
            name = worksheet.get("name")
            if not name or name in used_worksheets or worksheet.get("hidden") == "true":
                continue
            if name.startswith(("tooltip", "Tooltip", "__", "TOOLTIP")):
                continue
            standalone.append({"Nombre_Hoja": name})
        return TableauWorkbookIR(dashboards=dashboards, standalone_worksheets=standalone)

    def _extract_dashboard_objects(self, zones_root) -> list[dict[str, Any]]:
        """Conserva objetos Tableau no vinculados a una worksheet."""
        objects: list[dict[str, Any]] = []
        for zone in zones_root.findall(".//zone"):
            kind = zone.get("type-v2") or zone.get("type")
            if kind != "web":
                continue
            layout = self._extract_zone(zone, "Dashboard web object", len(objects)).layout()
            url = zone.get("param", "").strip()
            if layout is None or not url:
                continue
            objects.append({"Tipo": "web", "Url": url, "Layout": layout})
        return objects

    def _extract_dashboard_controls(self, dashboard, zones_root) -> list[dict[str, Any]]:
        """Extrae filtros y parámetros visibles con estado inicial Tableau."""
        controls: list[dict[str, Any]] = []
        for zone in zones_root.findall(".//zone"):
            kind = zone.get("type-v2")
            if kind not in {"filter", "paramctrl"}:
                continue
            param = zone.get("param", "")
            layout = self._extract_zone(zone, zone.get("name", ""), len(controls)).layout()
            if not param or layout is None:
                continue

            if kind == "filter" and ":" in param:
                datasource_name = param.split("].[", maxsplit=1)[0].lstrip("[")
                instance = param.rsplit(".[", maxsplit=1)[-1].strip("[]")
                parts = instance.split(":")
                if len(parts) < 3:
                    continue
                base_field = parts[-2]
                worksheet = self.root.find(f".//worksheet[@name='{zone.get('name', '')}']")
                label = base_field.replace("_", " ").title()
                if worksheet is not None:
                    title_format = worksheet.find(
                        f".//style-rule[@element='quick-filter']/format[@field='{param}']"
                    )
                    if title_format is not None:
                        label = title_format.get("value", label).rstrip(":")

                if parts[0].lower() == "yr":
                    shared_filter = self.root.find(
                        f".//shared-view/filter[@column='{param}']/groupfilter"
                    )
                    default = shared_filter.get("member", "") if shared_filter is not None else ""
                    controls.append(
                        {
                            "Tipo": "year_filter",
                            "Fuente_Datos": datasource_name,
                            "Campo_Base": base_field,
                            "Campo": f"{base_field} Year",
                            "Valor": int(default) if default.isdigit() else default,
                            "Titulo": label or "Select Year",
                            "Layout": layout,
                        }
                    )
                else:
                    controls.append(
                        {
                            "Tipo": "categorical_filter",
                            "Fuente_Datos": datasource_name,
                            "Campo": base_field,
                            "Valor": "",
                            "Titulo": label,
                            "Layout": layout,
                        }
                    )
                continue

            if kind == "paramctrl":
                parameter_name = param.rsplit(".[", maxsplit=1)[-1].strip("[]")
                parameter = self.root.find(
                    f".//datasource[@name='Parameters']/column[@name='[{parameter_name}]']"
                )
                if parameter is None:
                    continue
                options = [
                    {"value": alias.get("key", ""), "label": alias.get("value", "")}
                    for alias in parameter.findall("./aliases/alias")
                ]
                controls.append(
                    {
                        "Tipo": "parameter",
                        "Parametro": parameter_name,
                        "Valor": parameter.get("value", ""),
                        "Opciones": options,
                        "Titulo": "".join(zone.findall("./formatted-text/run")[0].itertext())
                        if zone.findall("./formatted-text/run")
                        else parameter.get("caption", parameter_name),
                        "Layout": layout,
                    }
                )
        return controls

    def _extract_dashboard_title(self, dashboard, zones_root) -> dict[str, Any] | None:
        """Extrae el título del dashboard y la zona que define su posición."""
        runs = dashboard.findall("./layout-options/title/formatted-text/run")
        text = _join_title_runs(runs)
        text = text.replace("<Sheet Name>", dashboard.get("name", ""))
        zone = zones_root.find(".//zone[@type-v2='title']")
        if zone is None:
            # DASH-TITLE-02: workbooks heredados serializan la misma zona con
            # ``type='title'``; ambos contratos deben producir el mismo IR.
            zone = zones_root.find(".//zone[@type='title']")
        if zone is None:
            return None
        if not text:
            # DASH-TITLE-03: si Tableau omite layout-options usa el nombre de
            # la hoja, que es el texto que Desktop muestra por defecto.
            text = dashboard.get("name", "")
        if not text:
            return None
        layout = self._extract_zone(zone, "Dashboard title", 0).layout()
        return {"Texto": text, "Layout": layout} if layout is not None else None

    @staticmethod
    def _is_worksheet_zone(zone) -> bool:
        """Distingue una hoja de leyendas, controles y layouts con el mismo nombre."""
        zone_type = zone.get("type")
        zone_type_v2 = zone.get("type-v2")
        if zone_type_v2 in {"filter", "paramctrl", "legend", "title"}:
            return False
        return zone_type in (None, "worksheet")

    def get_typed_workbook_ir(self) -> TableauWorkbookIR:
        """IR tipado Workbook→Dashboard→Zone→Worksheet con layout y orden preservados."""
        root = self.root

        # 0. Mapear Internal Name -> Caption
        mapa_datasources = {}
        for ds in root.findall(".//datasource"):
            internal_name = ds.get("name")
            caption = ds.get("caption") or internal_name
            if internal_name:
                mapa_datasources[internal_name] = caption

        # 1. Detalles por worksheet (campos, marca, encodings, filtros)
        worksheet_details: dict[str, dict[str, Any]] = {}
        for worksheet in root.findall(".//worksheet"):
            name = worksheet.get("name")
            if not name:
                continue
            info = self._extract_worksheet_info(worksheet, mapa_datasources)
            if info is not None:
                worksheet_details[name] = info

        # 2. Estructura de dashboards/zonas (sin detalles aún)
        workbook_ir = self.get_workbook_ir()

        # 3. Adjuntar el worksheet tipado a cada zona (vínculo Zone→Worksheet)
        for dashboard in workbook_ir.dashboards:
            for index, zone in enumerate(dashboard.zones):
                details = worksheet_details.get(zone.worksheet_name)
                if details is not None:
                    dashboard.zones[index] = replace(
                        zone, worksheet=TableauWorksheet.from_details(details)
                    )

        # 4. Standalone worksheets conservan su dict de detalles
        standalone_worksheets: list[dict[str, Any]] = []
        for worksheet in workbook_ir.standalone_worksheets:
            name = worksheet["Nombre_Hoja"]
            if isinstance(name, str) and name in worksheet_details:
                standalone_worksheets.append(worksheet_details[name])
        workbook_ir.standalone_worksheets = standalone_worksheets
        return workbook_ir

    def get_detailed_worksheets(self) -> list[dict]:
        """
        Extrae todas las hojas/vistas con información detallada y parsing robusto de campos.
        Portado de analisis_logic.py (V2).
        """
        return self.get_typed_workbook_ir().to_migration_items({})

    @staticmethod
    def _extract_zone(zone, worksheet_name: str, order: int) -> TableauZone:
        """Extrae posición y orden de una zona sin inferir valores inexistentes."""
        try:
            x = int(float(zone.get("x", "")))
            y = int(float(zone.get("y", "")))
            width = int(float(zone.get("w", "")))
            height = int(float(zone.get("h", "")))
        except ValueError:
            x = y = width = height = None
        if width is not None and (width <= 0 or height is None or height <= 0):
            x = y = width = height = None
        try:
            z_order = int(zone.get("z-order", zone.get("z", order)))
        except ValueError:
            z_order = order
        return TableauZone(
            worksheet_name=worksheet_name,
            order=order,
            x=x,
            y=y,
            width=width,
            height=height,
            z_order=z_order,
            floating=zone.get("floating", "false").lower() == "true",
            show_title=zone.get("show-title", "true").lower() != "false",
        )

    def _extract_worksheet_info(self, worksheet, mapa_datasources):
        """Helper para extraer info detallada de un worksheet."""
        nombre = worksheet.get("name") or worksheet.get("caption") or "Sin nombre"

        # Check contenido
        if worksheet.find(".//mark") is None and worksheet.find(".//visualization") is None:
            return None

        # Detectar tipo visual
        mark_type = "unknown"
        graph_style = worksheet.find(".//style/graph")
        if graph_style is not None:
            mark_type = graph_style.get("type")

        if mark_type == "unknown":
            style_rules = worksheet.findall(
                './/style/style-rule[@element="mark"]/format[@attr="mark-type"]'
            )
            if style_rules:
                mark_type = style_rules[0].get("value", "unknown")

        if mark_type in ["unknown", "automatic"]:
            pane_marks = worksheet.findall(".//panes/pane/mark")
            if pane_marks:
                pane_types = [
                    mark.get("class", "").lower() for mark in pane_marks if mark.get("class")
                ]
                val = "area" if "area" in pane_types else pane_types[0]
                if val:
                    mark_type = val.lower()

        # Extraccion de campos
        campos_filas = []
        campos_columnas = []
        campos_medidas = []
        campos_medidas_filas = []
        campos_medidas_columnas = []

        datasource_name = "Desconocido"
        ds_dep = worksheet.find(".//datasource-dependencies")
        if ds_dep is not None:
            internal = ds_dep.get("datasource", "")
            datasource_name = mapa_datasources.get(internal, internal)

        # 1. Metadatos de dependencias. No son campos visibles por sí solos:
        # sólo sirven para clasificar las referencias usadas en shelves/marks.
        import re

        ds_names_lower = {v.lower() for v in mapa_datasources.values()}
        field_meta: dict[str, tuple[str, str]] = {}
        field_labels: dict[str, str] = {}

        for dep in worksheet.findall(".//datasource-dependencies"):
            for col in dep.findall(".//column"):
                c_name = col.get("name", "")
                c_role = col.get("role", "")
                c_type = col.get("datatype", "")

                if not c_name:
                    continue
                clean = c_name.replace("[", "").replace("]", "")

                # Saltar artefactos que son el nombre del datasource.
                if clean.lower() in ds_names_lower:
                    continue

                field_meta[clean] = (c_role, c_type)
                if col.get("caption"):
                    field_labels[clean] = col.get("caption", clean)
                if clean.lower().strip(":") == "measure names":
                    for alias in col.findall("./aliases/alias"):
                        alias_field = self._normalize_tableau_field(alias.get("key", ""))
                        if alias_field and alias.get("value"):
                            field_labels[alias_field] = alias.get("value", alias_field)

        # Los aliases de Measure Names suelen vivir en el datasource global,
        # no dentro de datasource-dependencies del worksheet.
        datasource_internal = ds_dep.get("datasource", "") if ds_dep is not None else ""
        workbook_root = getattr(self, "root", None)
        if workbook_root is not None:
            for datasource in workbook_root.findall(
                f".//datasource[@name='{datasource_internal}']"
            ):
                for column in datasource.findall("./column"):
                    if column.get("name", "").strip("[]").lower().strip(":") != "measure names":
                        continue
                    for alias in column.findall("./aliases/alias"):
                        alias_field = self._normalize_tableau_field(alias.get("key", ""))
                        if alias_field and alias.get("value"):
                            field_labels[alias_field] = alias.get("value", alias_field)

        # 2. Shelf Analysis (Override)
        # Tableau puede incluir nodos rows/cols auxiliares vacíos antes del shelf
        # real. Se usa el último nodo no vacío, que corresponde a table/rows.
        rows_elem = next(
            (
                elem
                for elem in reversed(worksheet.findall(".//rows"))
                if elem.text and elem.text.strip()
            ),
            None,
        )
        cols_elem = next(
            (
                elem
                for elem in reversed(worksheet.findall(".//cols"))
                if elem.text and elem.text.strip()
            ),
            None,
        )

        has_rows = rows_elem is not None and rows_elem.text and rows_elem.text.strip()
        has_cols = cols_elem is not None and cols_elem.text and cols_elem.text.strip()

        pseudo_fields = {"multiple values", "measure names", ":measure names"}

        def extract_shelf_refs(text):
            if not text:
                return []
            tokens = re.findall(r"\[[^\]]*\]\.\[([^\]]+)\]", text)
            if not tokens:
                tokens = re.findall(r"\[([^\]]+)\]", text)
            refs: list[tuple[str, bool]] = []
            for token in tokens:
                parts = token.split(":")
                prefix = parts[0].lower() if len(parts) > 1 else ""
                field = parts[-2] if len(parts) > 2 and parts[-2] else token
                if field.lower() in pseudo_fields or field.lower() in ds_names_lower:
                    continue
                role, datatype = field_meta.get(field, ("", ""))
                derivations = {part.lower() for part in parts[:-2]}
                is_measure = bool(
                    derivations & {"sum", "avg", "min", "max", "cnt", "count", "median", "fval"}
                )
                is_measure = is_measure or role == "measure"
                if prefix == "usr" and datatype == "string":
                    is_measure = False
                projected_field = field
                if not is_measure and "mn" in derivations:
                    projected_field = f"{field} Month"
                elif not is_measure and "yr" in derivations:
                    projected_field = f"{field} Year"
                if (projected_field, is_measure) not in refs:
                    refs.append((projected_field, is_measure))

            # DATE-GRAIN-01: Year/Month en un mismo shelf representa una
            # fecha mensual continua; el nivel Year sólo es su contenedor.
            monthly_bases = {
                field_name.removesuffix(" Month")
                for field_name, is_measure in refs
                if not is_measure and field_name.endswith(" Month")
            }
            return [
                (field_name, is_measure)
                for field_name, is_measure in refs
                if not (
                    not is_measure
                    and field_name.endswith(" Year")
                    and field_name.removesuffix(" Year") in monthly_bases
                )
            ]

        def add_shelf(
            text: str | None,
            dimensions: list[str],
            shelf_measures: list[str],
        ) -> None:
            """Separa dimensiones y medidas sin perder el shelf Tableau."""
            for field_name, is_measure in extract_shelf_refs(text):
                target = campos_medidas if is_measure else dimensions
                if field_name not in target:
                    target.append(field_name)
                if is_measure and field_name not in shelf_measures:
                    shelf_measures.append(field_name)

        if has_rows:
            add_shelf(rows_elem.text, campos_filas, campos_medidas_filas)

        if has_cols:
            add_shelf(cols_elem.text, campos_columnas, campos_medidas_columnas)

        # PANE-01: los panes con ejes X independientes representan vistas
        # separadas, aunque Tableau las guarde dentro de un solo worksheet.
        paneles_medidas: list[str] = []
        for pane in worksheet.findall(".//panes/pane[@x-axis-name]"):
            for field_name, is_measure in extract_shelf_refs(pane.get("x-axis-name", "")):
                if is_measure and field_name not in paneles_medidas:
                    paneles_medidas.append(field_name)

        orden: dict[str, str] = {}
        shelf_sort = worksheet.find(".//shelf-sort[@measure-to-sort-by]")
        if shelf_sort is not None:
            sort_refs = extract_shelf_refs(shelf_sort.get("measure-to-sort-by", ""))
            if sort_refs:
                direction = shelf_sort.get("direction", "ASC").upper()
                orden = {
                    "Campo": sort_refs[0][0],
                    "Direccion": "Descending" if direction == "DESC" else "Ascending",
                }

        encodings = worksheet.findall(".//encodings/*")
        for encoding in encodings:
            raw_field = encoding.get("column") or encoding.get("columns")
            for field_name, is_measure in extract_shelf_refs(raw_field):
                if is_measure and field_name not in campos_medidas:
                    campos_medidas.append(field_name)

        # Tableau representa tablas con varias métricas mediante los pseudo-
        # campos Measure Names + Multiple Values. Las métricas reales están en
        # column-instance; descartarlas deja Values vacío en PBIR.
        shelf_text = " ".join(
            str(value)
            for value in (
                rows_elem.text if rows_elem is not None else "",
                cols_elem.text if cols_elem is not None else "",
                *(encoding.get("column", "") for encoding in encodings),
            )
        ).lower()
        uses_measure_values = "measure names" in shelf_text or "multiple values" in shelf_text
        if uses_measure_values:
            instances = list(worksheet.findall(".//column-instance"))
            ordered_fields = []
            for manual_sort in worksheet.findall(".//manual-sort"):
                if "measure names" not in manual_sort.get("column", "").lower():
                    continue
                for bucket in manual_sort.findall("./dictionary/bucket"):
                    field = self._normalize_tableau_field(bucket.text or "")
                    if field and field not in ordered_fields:
                        ordered_fields.append(field)

            instance_by_field = {
                instance.get("column", "").strip("[]"): instance for instance in instances
            }
            ordered_instances = [
                instance_by_field[field] for field in ordered_fields if field in instance_by_field
            ]
            ordered_instances.extend(
                instance for instance in instances if instance not in ordered_instances
            )
            for instance in ordered_instances:
                column_name = instance.get("column", "").strip("[]")
                derivation = instance.get("derivation", "").lower()
                role, datatype = field_meta.get(column_name, ("", ""))
                is_quantitative = role == "measure" or datatype in {
                    "real",
                    "integer",
                    "decimal",
                }
                if (
                    column_name
                    and derivation not in {"", "none"}
                    and is_quantitative
                    and column_name not in campos_medidas
                ):
                    campos_medidas.append(column_name)

        # Detectar Tipo Visual Normalizado
        mark_norm = mark_type.lower()
        has_text_encoding = any(
            encoding.tag.rsplit("}", maxsplit=1)[-1].lower() == "text" for encoding in encodings
        )
        measure_values_text_table = (
            uses_measure_values and mark_norm in {"text", "automatic", "auto"} and has_text_encoding
        )
        if measure_values_text_table or (
            mark_norm in {"automatic", "auto"}
            and has_text_encoding
            and bool(campos_filas or campos_columnas)
            and bool(campos_medidas)
        ):
            # Measure Values y las marcas Automatic con medida en Text son
            # tablas/matrices Tableau, no gráficos inferidos por ejes.
            mark_norm = "text"
        elif mark_norm == "unknown":
            has_dimension = bool(campos_filas or campos_columnas)
            if has_dimension and campos_medidas:
                mark_norm = "bar"
            elif has_dimension:
                mark_norm = "text"
            elif campos_medidas:
                mark_norm = "text"  # Card
            else:
                mark_norm = "text"

        title_text = self._extract_worksheet_title(worksheet)
        if not (campos_filas or campos_columnas or campos_medidas) and title_text:
            # TITLE-HOST-01: una worksheet sin shelves pero con título explícito
            # es un objeto de texto del dashboard, no un visual de datos vacío.
            mark_norm = "textbox"

        return {
            "Nombre_Hoja": nombre,
            "Tipo_Marca": mark_type,
            "Tipo_Visual": mark_norm,
            "Fuente_Datos": datasource_name,
            "Campos_Filas": campos_filas,
            "Campos_Columnas": campos_columnas,
            "Campos_Medidas": campos_medidas,
            "Campos_Medidas_Filas": campos_medidas_filas,
            "Campos_Medidas_Columnas": campos_medidas_columnas,
            "Paneles_Medidas": paneles_medidas,
            "Orden": orden,
            "Etiquetas_Campos": field_labels,
            "Encodings": self._extract_mark_encodings(worksheet),
            "Filtros": self._extract_worksheet_filters(worksheet),
            "Texto_Titulo": title_text,
        }

    @staticmethod
    def _extract_worksheet_title(worksheet) -> str:
        """Materializa el título Tableau, incluidos parámetros con alias."""
        runs = worksheet.findall("./layout-options/title/formatted-text/run")
        text = _join_title_runs(runs)
        if not text:
            return ""

        # <Sheet Name> es una variable dinámica de Tableau: se reemplaza por el
        # nombre de la hoja.
        sheet_name = worksheet.get("name", "")
        text = text.replace("<Sheet Name>", sheet_name)

        for dependency in worksheet.findall(".//datasource-dependencies/column"):
            name = dependency.get("name", "").strip("[]")
            if not name or dependency.get("param-domain-type") is None:
                continue
            value = dependency.get("value", "")
            alias_elem = dependency.find(f"./aliases/alias[@key='{value}']")
            replacement = alias_elem.get("value", value) if alias_elem is not None else value
            patterns = (
                f"<[Parameters].[{name}]>",
                f"<[{name}]>",
                f"[Parameters].[{name}]",
                f"[{name}]",
            )
            for pattern in patterns:
                text = text.replace(pattern, replacement)
        for filter_elem in worksheet.findall(".//filter"):
            raw_column = filter_elem.get("column", "")
            members = [
                member.get("member", "").strip('"')
                for member in filter_elem.findall(".//groupfilter")
                if member.get("member") and not member.findall("./groupfilter")
            ]
            if raw_column and len(members) == 1:
                text = text.replace(f"<{raw_column}>", members[0])
        return text

    @staticmethod
    def _normalize_tableau_field(raw_field: str) -> str:
        """Extrae el nombre lógico desde una referencia de campo de Tableau."""
        bracketed = re.findall(r"\[([^\]]+)\]", raw_field)
        candidate = bracketed[-1] if bracketed else raw_field
        if ":" in candidate:
            parts = candidate.split(":")
            if len(parts) > 1 and parts[1]:
                candidate = parts[1]
        return candidate.replace("[", "").replace("]", "").strip()

    @classmethod
    def _extract_mark_encodings(cls, worksheet) -> dict[str, list[str]]:
        """Conserva los estantes de marcas para migrar semántica, no sólo ejes.

        Tableau puede repetir el mismo tipo de codificación en varios panes. El
        IR mantiene el orden de aparición y elimina duplicados para que la capa
        PBIR pueda decidir el rol compatible con cada visual de destino.
        """
        encodings: dict[str, list[str]] = {}
        supported_roles = {
            "color",
            "detail",
            "geometry",
            "label",
            "lod",
            "path",
            "shape",
            "size",
            "text",
            "tooltip",
        }
        for encoding in worksheet.findall(".//encodings/*"):
            role = encoding.tag.rsplit("}", maxsplit=1)[-1].lower()
            raw_field = encoding.get("column") or encoding.get("columns")
            if role not in supported_roles or not raw_field:
                continue
            field = cls._normalize_tableau_field(raw_field)
            if field and field not in encodings.setdefault(role, []):
                encodings[role].append(field)
        return encodings

    @staticmethod
    def _extract_worksheet_filters(worksheet) -> list[dict[str, Any]]:
        """Conserva filtros Tableau en el IR, sin inventar una semántica PBIR."""
        filters: list[dict[str, Any]] = []
        for filter_elem in worksheet.findall(".//filter"):
            raw_field = filter_elem.get("column", "")
            if not raw_field:
                continue
            field = TableauReader._normalize_tableau_field(raw_field)

            values = [
                member.get("member", "").strip('"')
                for member in filter_elem.findall(".//groupfilter")
                if member.get("member")
            ]
            range_elem = filter_elem.find(".//range")
            root_group = filter_elem.find("./groupfilter")
            group_function = root_group.get("function", "") if root_group is not None else ""
            operator = "exclude" if group_function == "except" else "include"
            kind = "range" if range_elem is not None else "categorical"
            if "date" in field.lower() or "fecha" in field.lower():
                kind = "date"
            filters.append(
                {
                    "field": field,
                    "kind": kind,
                    "values": values,
                    "operator": operator,
                    "minimum": range_elem.get("from", "") if range_elem is not None else "",
                    "maximum": range_elem.get("to", "") if range_elem is not None else "",
                    "context": filter_elem.get("context", "false").lower() == "true",
                    "tableau_class": filter_elem.get("class", ""),
                }
            )
        return filters
