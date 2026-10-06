"""
Script de validación automática para archivos PBIP migrados.
Verifica que la estructura sea correcta y contenga los elementos mínimos requeridos.
"""

import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# PBIP-CLI-01: permite ejecutar este archivo directamente además de usarlo como módulo.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.dax_converter import contains_tableau_residual_syntax  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PBIP_Validator")


def _console_safe(value: object, encoding: str | None = None) -> str:
    """Convierte texto a una representación imprimible por la consola activa."""
    # PBIP-CLI-02: reemplaza sólo caracteres no representables, sin perder acentos válidos.
    output_encoding = encoding or sys.stdout.encoding or "utf-8"
    return str(value).encode(output_encoding, errors="replace").decode(output_encoding)


def _report_folder_from_pbip(ruta_proyecto: str, pbip_files: Sequence[str]) -> str | None:
    """Resuelve la carpeta Report declarada por el proyecto PBIP activo.

    Un directorio de ejecución puede conservar varias revisiones ``.Report``.
    Validar la primera que devuelve el sistema de archivos produce resultados
    falsamente verdes para un ``.pbip`` que apunta a otra revisión.
    """
    for filename in pbip_files:
        try:
            with open(os.path.join(ruta_proyecto, filename), encoding="utf-8") as source:
                pbip = json.load(source)
        except (OSError, json.JSONDecodeError):
            continue
        for artifact in pbip.get("artifacts", []):
            report_path = artifact.get("report", {}).get("path")
            if isinstance(report_path, str) and report_path:
                candidate = os.path.join(ruta_proyecto, report_path)
                if os.path.isdir(candidate):
                    return candidate
    return None


def check_tmdl_residual_syntax(tables_folder: str) -> list[str]:
    """Ubica líneas DAX activas que todavía contienen sintaxis Tableau."""
    residuals: list[str] = []
    if not os.path.isdir(tables_folder):
        return residuals

    for filename in sorted(os.listdir(tables_folder)):
        if not filename.endswith(".tmdl"):
            continue
        path = os.path.join(tables_folder, filename)
        try:
            with open(path, encoding="utf-8") as source:
                lines = source.readlines()
        except OSError:
            continue

        in_block_comment = False
        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if in_block_comment:
                if "*/" not in line:
                    continue
                line = line.split("*/", 1)[1].strip()
                in_block_comment = False
            if not line or line.startswith(("///", "//")):
                continue
            if "/*" in line:
                before_comment, after_comment = line.split("/*", 1)
                line = before_comment.strip()
                if "*/" not in after_comment:
                    in_block_comment = True
            line = line.split("//", 1)[0].strip()
            if line and contains_tableau_residual_syntax(line):
                residuals.append(f"{filename}:{line_number}")

    return residuals


def validar_proyecto(
    ruta_proyecto: str,
    expected_pages: Sequence[str] | None = None,
    expected_empty_pages: Sequence[str] | None = None,
) -> dict[str, Any]:
    """
    Realiza una auditoría flash del proyecto PBIP generado.
    Valida estructura PBIR/TMDL. Si se entrega ``expected_pages``, además
    comprueba el contrato de páginas del workbook fuente.

    ``expected_empty_pages`` es una lista explícita de ``displayName`` de páginas
    declaradas sin proyecciones (p.ej. instrucciones o setup). Cuando una de
    esas páginas no tenga proyecciones, deja de contar como ``VISUAL_VACIO_N`` y
    se registra en ``resultado["paginas_vacias_esperadas"]``. Un binding roto en
    una página esperada sigue siendo error crítico. Por defecto ``None``
    conserva el comportamiento histórico (todo visual vacío es error).

    Returns:
        dict con: exito, total_creadas, total_visuales, mensaje, errores
    """
    selected_pbip: str | None = None
    if ruta_proyecto.lower().endswith(".pbip"):
        selected_pbip = os.path.basename(ruta_proyecto)
        ruta_proyecto = os.path.dirname(ruta_proyecto)

    logger.info(f"Validando proyecto en: {ruta_proyecto}")

    resultado = {
        "exito": False,
        "total_creadas": 0,
        "total_visuales": 0,
        "faltantes": [],
        "dax_residuals": [],
        "errores": [],
        "paginas_vacias_esperadas": [],
        "mensaje": "",
    }

    if not os.path.exists(ruta_proyecto):
        resultado["mensaje"] = "La ruta del proyecto no existe."
        resultado["errores"].append("RUTA_NO_EXISTE")
        return resultado

    # 1. Verificar archivo .pbip
    pbip_files = [f for f in os.listdir(ruta_proyecto) if f.endswith(".pbip")]
    if selected_pbip:
        pbip_files = [selected_pbip]
    if not pbip_files:
        resultado["mensaje"] = "No se encontró archivo .pbip"
        resultado["errores"].append("SIN_ARCHIVO_PBIP")
        return resultado

    # 2. Buscar carpeta .Report
    folder_report = _report_folder_from_pbip(ruta_proyecto, pbip_files)
    if folder_report is None:
        for item in os.listdir(ruta_proyecto):
            if item.endswith(".Report"):
                folder_report = os.path.join(ruta_proyecto, item)
                break

    if not folder_report:
        resultado["mensaje"] = "No se encontró la carpeta .Report"
        resultado["errores"].append("SIN_CARPETA_REPORT")
        return resultado

    # 3. Buscar carpeta .SemanticModel
    folder_semantic = None
    for item in os.listdir(ruta_proyecto):
        if item.endswith(".SemanticModel"):
            folder_semantic = os.path.join(ruta_proyecto, item)
            break

    if not folder_semantic:
        resultado["mensaje"] = "No se encontró la carpeta .SemanticModel"
        resultado["errores"].append("SIN_CARPETA_SEMANTIC")
        return resultado

    # 4. Verificar carpeta pages
    path_pages = os.path.join(folder_report, "definition", "pages")
    if not os.path.exists(path_pages):
        resultado["mensaje"] = f"No existe la carpeta de páginas: {path_pages}"
        resultado["errores"].append("SIN_CARPETA_PAGES")
        return resultado

    # 5. Verificar pages.json
    pages_json_path = os.path.join(path_pages, "pages.json")
    if not os.path.exists(pages_json_path):
        resultado["errores"].append("SIN_PAGES_JSON")
        logger.warning("No existe pages.json - el reporte podría no abrir correctamente")

    # 6. Contar páginas y visuales
    subfolders = [d for d in os.listdir(path_pages) if os.path.isdir(os.path.join(path_pages, d))]

    total_visuales = 0
    paginas_sin_visuales = []

    for folder in subfolders:
        page_folder = os.path.join(path_pages, folder)
        visuals_folder = os.path.join(page_folder, "visuals")

        # Verificar page.json
        page_json_path = os.path.join(page_folder, "page.json")
        if not os.path.exists(page_json_path):
            resultado["errores"].append(f"Página {folder} sin page.json")
            continue

        # Contar visuales
        if os.path.exists(visuals_folder):
            visual_dirs = [
                d
                for d in os.listdir(visuals_folder)
                if os.path.isdir(os.path.join(visuals_folder, d))
            ]

            visuales_validos = 0
            for vdir in visual_dirs:
                visual_json_path = os.path.join(visuals_folder, vdir, "visual.json")
                if os.path.exists(visual_json_path):
                    # Verificar que visual.json tenga visualType
                    try:
                        with open(visual_json_path, encoding="utf-8") as f:
                            vdata = json.load(f)
                            if vdata.get("visual", {}).get("visualType"):
                                visuales_validos += 1
                                total_visuales += 1
                    except Exception:
                        pass

            if visuales_validos == 0:
                paginas_sin_visuales.append(folder)
        else:
            paginas_sin_visuales.append(folder)

    resultado["total_creadas"] = len(subfolders)
    resultado["total_visuales"] = total_visuales

    # 7. Validar mínimos
    MIN_PAGINAS = 1
    MIN_VISUALES = 1

    if len(subfolders) < MIN_PAGINAS:
        resultado["mensaje"] = (
            f"Se esperaba al menos {MIN_PAGINAS} página, se encontraron {len(subfolders)}"
        )
        resultado["errores"].append("POCAS_PAGINAS")
        return resultado

    if total_visuales < MIN_VISUALES:
        resultado["mensaje"] = (
            f"Se esperaba al menos {MIN_VISUALES} visual, se encontraron {total_visuales}"
        )
        resultado["errores"].append("POCOS_VISUALES")
        return resultado

    # 8. Verificar semantic model tiene tablas
    tables_folder = os.path.join(folder_semantic, "definition", "tables")
    model_bim_path = os.path.join(folder_semantic, "model.bim")

    has_tables = False
    if os.path.exists(tables_folder):
        tmdl_files = [f for f in os.listdir(tables_folder) if f.endswith(".tmdl")]
        has_tables = len(tmdl_files) > 0
    elif os.path.exists(model_bim_path):
        has_tables = True

    if not has_tables:
        resultado["errores"].append("SIN_TABLAS_SEMANTIC")
        logger.warning("El Semantic Model no tiene tablas definidas")

    # PBIP-VAL-01: un token Tableau activo hace que Desktop rechace el modelo.
    dax_residuals = check_tmdl_residual_syntax(tables_folder)
    if dax_residuals:
        resultado["dax_residuals"] = dax_residuals[:50]
        resultado["errores"].append(f"DAX_TABLEAU_RESIDUAL_{len(dax_residuals)}")
        logger.warning(
            "Sintaxis Tableau residual en TMDL: %s",
            ", ".join(dax_residuals[:10]),
        )

    # Recolectar nombres reales (displayName)
    nombres_hojas = []
    for folder in subfolders:
        page_json = os.path.join(path_pages, folder, "page.json")
        if os.path.exists(page_json):
            try:
                with open(page_json, encoding="utf-8") as f:
                    d = json.load(f)
                    if d.get("displayName"):
                        nombres_hojas.append(d.get("displayName"))
            except (OSError, json.JSONDecodeError):
                pass

    faltantes: list[str] = []
    if expected_pages is not None:
        from core.validation.expected_sheets import normalizar

        expected_by_normalized = {normalizar(name): name for name in expected_pages if name}
        actual_normalized = {normalizar(name) for name in nombres_hojas if name}
        faltantes = [
            name for key, name in expected_by_normalized.items() if key not in actual_normalized
        ]
        resultado["extras"] = [
            name for name in nombres_hojas if normalizar(name) not in expected_by_normalized
        ]
        if faltantes:
            resultado["errores"].append(f"FALTAN_{len(faltantes)}_PAGINAS_ESPERADAS")
            logger.warning(f"Faltan páginas esperadas: {faltantes}")

    resultado["faltantes"] = faltantes

    # 9b. Validación semántica de bindings (projections vs TMDL)
    expected_empty_normalized: set[str] = set()
    if expected_empty_pages:
        from core.validation.expected_sheets import normalizar as _normalizar_vacias

        expected_empty_normalized = {
            _normalizar_vacias(name) for name in expected_empty_pages if name
        }

    binding_errors, paginas_vacias_esperadas = check_semantic_bindings(
        path_pages, tables_folder, expected_empty_normalized or None
    )
    resultado["paginas_vacias_esperadas"] = paginas_vacias_esperadas
    if paginas_vacias_esperadas:
        logger.info(
            "Páginas declaradas sin proyecciones (expected_empty): %s",
            ", ".join(paginas_vacias_esperadas),
        )
    if binding_errors:
        resultado["errores"].extend(binding_errors)
        logger.warning(f"Errores de bindings semánticos: {binding_errors}")

    # 10. Resultado final
    if not resultado["errores"]:
        resultado["exito"] = True
        resultado["mensaje"] = (
            f"✅ Validación exitosa: {resultado['total_creadas']} páginas, "
            f"{resultado['total_visuales']} visuales."
        )
    else:
        resultado["mensaje"] = f"⚠️ Validación con advertencias: {', '.join(resultado['errores'])}"
        has_critical_failure = any(
            error == "SIN_TABLAS_SEMANTIC"
            or error.startswith(
                (
                    "BINDING_ROTO_",
                    "VISUAL_VACIO_",
                    "ROL_INVALIDO_SERIES_",
                    "DAX_TABLEAU_RESIDUAL_",
                )
            )
            for error in resultado["errores"]
        )
        if faltantes:
            resultado["mensaje"] = (
                f"❌ Fallo de contrato: faltan {len(faltantes)} páginas esperadas"
            )
        elif has_critical_failure:
            resultado["mensaje"] = "❌ Fallo semántico, de bindings o visuales vacíos"
        else:
            resultado["exito"] = True

    return resultado


def _read_page_display_name(page_json_path: str) -> str:
    """Lee el ``displayName`` declarado por un ``page.json``; '' si falta o está roto."""
    if not os.path.exists(page_json_path):
        return ""
    try:
        with open(page_json_path, encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError):
        return ""
    value = data.get("displayName")
    return value if isinstance(value, str) else ""


def _es_pagina_esperada_vacia(
    display_name: str, expected_empty_pages: set[str] | None
) -> bool:
    """Indica si una página sin proyecciones fue declarada esperada vacía.

    Compara por ``displayName`` normalizado (sin acentos/mayúsculas) para no
    usar heurísticas por nombre: sólo una declaración explícita coincide.
    """
    if not expected_empty_pages or not display_name:
        return False
    from core.validation.expected_sheets import normalizar

    return normalizar(display_name) in expected_empty_pages


def check_semantic_bindings(
    path_pages: str,
    tables_folder: str,
    expected_empty_pages: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Valida que cada projection.field en los visual.json referencie
    objetos (column o measure) que existen en el TMDL del modelo semántico.

    Si ``expected_empty_pages`` es ``None`` (por defecto), los visuales sin
    proyecciones siguen contando como ``VISUAL_VACIO_N`` (comportamiento
    histórico conservado). Si se entrega un conjunto de ``displayName``
    normalizados declarados como páginas esperadas vacías, los visuales sin
    proyecciones de esas páginas dejan de contar como error y el ``displayName``
    se devuelve en el segundo elemento de la tupla. Los bindings rotos siempre
    se reportan, incluso en páginas declaradas esperadas vacías.

    Returns:
        Tupla ``(errores, paginas_vacias_esperadas)``. ``errores`` es la lista
        de códigos de error (vacía si todo está OK);
        ``paginas_vacias_esperadas`` lista los ``displayName`` declarados que
        efectivamente no tenían proyecciones.
    """
    import re as _re

    errors: list[str] = []

    # 1. Recolectar (entity, property) válidos desde TMDL
    valid_objects: set[str] = set()
    if os.path.exists(tables_folder):
        for fname in os.listdir(tables_folder):
            if not fname.endswith(".tmdl"):
                continue
            tmdl_path = os.path.join(tables_folder, fname)
            try:
                with open(tmdl_path, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            # Extraer nombre de tabla
            first_line = content.split("\n", 1)[0].strip()
            if not first_line.startswith("table "):
                continue
            table_name = first_line.replace("table ", "").strip().strip("'").strip('"')

            # Extraer columnas y medidas
            for m in _re.finditer(
                r"^\s*(?:column|measure)\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=]+))",
                content,
                _re.MULTILINE,
            ):
                obj_name = next((g for g in m.groups() if g), "").strip()
                if obj_name:
                    valid_objects.add(f"{table_name}.{obj_name}")
                    valid_objects.add(f"{table_name}.{obj_name.lower()}")

    if not valid_objects:
        return [], []  # Sin TMDL no se puede validar

    # 2. Recorrer todos los visual.json y verificar bindings, registrando el
    # displayName de la página contenedora para clasificar visuales vacíos.
    visual_entries: list[tuple[str, str]] = []
    for page_dir in os.listdir(path_pages):
        page_path = os.path.join(path_pages, page_dir)
        if not os.path.isdir(page_path):
            continue
        display_name = _read_page_display_name(os.path.join(page_path, "page.json"))
        visuals_path = os.path.join(page_path, "visuals")
        if not os.path.isdir(visuals_path):
            continue
        for vd in os.listdir(visuals_path):
            vj = os.path.join(visuals_path, vd, "visual.json")
            if os.path.exists(vj):
                visual_entries.append((vj, display_name))

    broken_count = 0
    empty_count = 0
    invalid_series_count = 0
    paginas_esperadas_vacias: list[str] = []

    for vj_path, display_name in visual_entries:
        try:
            with open(vj_path, encoding="utf-8") as f:
                vdata = json.load(f)
        except Exception:
            continue

        visual_type = vdata.get("visual", {}).get("visualType", "")
        if visual_type in ("textbox",):
            continue

        query_state = vdata.get("visual", {}).get("query", {}).get("queryState", {})
        total_projections = 0
        broken_in_this = 0

        for role_name, role_data in query_state.items():
            projections = role_data.get("projections", [])
            for proj in projections:
                total_projections += 1
                field = proj.get("field", {})

                # En gráficos cartesianos, Series es una leyenda categórica.
                # Power BI rechaza agregaciones o medidas en este rol aunque el
                # objeto exista en el modelo, por lo que la verificación de
                # binding por sí sola no basta.
                if role_name == "Series" and "Column" not in field:
                    invalid_series_count += 1

                # Extraer (entity, property) del field
                entity = None
                property_name = None

                if "Measure" in field:
                    measure_ref = field["Measure"]
                    entity = measure_ref.get("Expression", {}).get("SourceRef", {}).get("Entity")
                    property_name = measure_ref.get("Property")
                elif "Aggregation" in field:
                    col_ref = field["Aggregation"].get("Expression", {}).get("Column", {})
                    entity = col_ref.get("Expression", {}).get("SourceRef", {}).get("Entity")
                    property_name = col_ref.get("Property")
                elif "Column" in field:
                    col_ref = field["Column"]
                    entity = col_ref.get("Expression", {}).get("SourceRef", {}).get("Entity")
                    property_name = col_ref.get("Property")

                if entity and property_name:
                    key = f"{entity}.{property_name.lower()}"
                    if key not in valid_objects:
                        broken_in_this += 1
                        broken_count += 1

        if total_projections == 0 and visual_type != "textbox":
            if _es_pagina_esperada_vacia(display_name, expected_empty_pages):
                if display_name and display_name not in paginas_esperadas_vacias:
                    paginas_esperadas_vacias.append(display_name)
            else:
                empty_count += 1

    if broken_count > 0:
        errors.append(f"BINDING_ROTO_{broken_count}")
    if empty_count > 0:
        errors.append(f"VISUAL_VACIO_{empty_count}")
    if invalid_series_count > 0:
        errors.append(f"ROL_INVALIDO_SERIES_{invalid_series_count}")

    return errors, paginas_esperadas_vacias


if __name__ == "__main__":
    if len(sys.argv) > 1:
        res = validar_proyecto(sys.argv[1])
        print(f"\n{'=' * 60}")
        # PBIP-CLI-01: salida ASCII compatible con PowerShell/CI configurados en CP1252.
        print(f"RESULTADO: {'EXITO' if res['exito'] else 'FALLO'}")
        print(f"{'=' * 60}")
        print(f"Mensaje: {_console_safe(res['mensaje'])}")
        print(f"Páginas: {res['total_creadas']}")
        print(f"Visuales: {res['total_visuales']}")
        if res["errores"]:
            print(f"Errores/Advertencias: {_console_safe(res['errores'])}")
    else:
        print("Uso: python validate_pbip_output.py <ruta_proyecto>")
