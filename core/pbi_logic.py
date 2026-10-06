"""
Módulo para inyectar medidas DAX en proyectos Power BI de forma segura
"""

import logging
import os
import re
import shutil
import stat
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

from core import analisis_logic, pbir_logic, tmdl_generator
from core import config_manager as config
from core.validation.validate_pbip_output import validar_proyecto


def remove_readonly(func, path, excinfo):
    """Auxiliar para borrar archivos read-only en Windows"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


logger = logging.getLogger(__name__)


def copiar_proyecto_pbip(
    ruta_archivo_pbip: str, carpeta_salida: str
) -> tuple[str | None, str | None]:
    """
    Copia SOLO los archivos esenciales de un proyecto PBIP para evitar corrupciones.

    Args:
        ruta_archivo_pbip: Ruta al archivo .pbip original
        carpeta_salida: Carpeta donde crear la copia

    Returns:
        Tupla (ruta_destino, error). Si error es None, la copia fue exitosa.
    """
    try:
        dir_origen = os.path.dirname(ruta_archivo_pbip)
        nombre_pbip = os.path.basename(ruta_archivo_pbip)
        nombre_proyecto = os.path.splitext(nombre_pbip)[0]

        folder_semantic = f"{nombre_proyecto}.SemanticModel"
        folder_report = f"{nombre_proyecto}.Report"

        timestamp = datetime.now().strftime(config.FORMATO_TIMESTAMP_BACKUP)
        nombre_nueva_carpeta = f"{nombre_proyecto}_MIGRADO_{timestamp}"
        ruta_destino_raiz = os.path.join(carpeta_salida, nombre_nueva_carpeta)

        if os.path.exists(ruta_destino_raiz):
            logger.warning(
                f"La carpeta destino ya existe, eliminando: {ruta_destino_raiz}"
            )
            shutil.rmtree(ruta_destino_raiz)
        os.makedirs(ruta_destino_raiz)

        logger.info(f"Creando copia limpia en: {Path(ruta_destino_raiz).name}")

        # Copiar .pbip
        shutil.copy2(ruta_archivo_pbip, os.path.join(ruta_destino_raiz, nombre_pbip))
        logger.debug("Archivo .pbip copiado")

        # Copiar SemanticModel
        src_sem = os.path.join(dir_origen, folder_semantic)
        if os.path.exists(src_sem):
            shutil.copytree(src_sem, os.path.join(ruta_destino_raiz, folder_semantic))
            logger.debug("Carpeta SemanticModel copiada")
        else:
            error_msg = f"No encontré la carpeta del modelo: {folder_semantic}"
            logger.error(error_msg)
            return None, error_msg

        # Copiar Report
        src_rep = os.path.join(dir_origen, folder_report)
        if os.path.exists(src_rep):
            shutil.copytree(src_rep, os.path.join(ruta_destino_raiz, folder_report))
            logger.debug("Carpeta Report copiada")

        # Copiar .gitignore si existe
        src_git = os.path.join(dir_origen, ".gitignore")
        if os.path.exists(src_git):
            shutil.copy2(src_git, os.path.join(ruta_destino_raiz, ".gitignore"))
            logger.debug(".gitignore copiado")

        logger.info("Copia del proyecto completada exitosamente")
        return ruta_destino_raiz, None

    except Exception as e:
        error_msg = f"Error copiando proyecto: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg


def limpiar_dax_ia(dax_raw: str, nombre_medida: str) -> str:
    """
    Limpia alucinaciones comunes de la IA, como incluir 'Medida =' al principio
    o bloques de markdown.
    """
    dax = str(dax_raw).strip()

    # Quitar bloques de código markdown
    dax = dax.replace("```dax", "").replace("```", "").strip()

    # Quitar si la IA puso "Nombre = CALC..." al principio
    pattern_asignacion = re.compile(
        r"^['\"]?" + re.escape(nombre_medida) + r"['\"]?\s*=\s*", re.IGNORECASE
    )
    dax = pattern_asignacion.sub("", dax)

    return dax


def inyectar_en_pbi(
    ruta_archivo_pbip: str,
    carpeta_salida_copias: str,
    ruta_excel_manual: str = "",
    ruta_tableau_original: str = "",
) -> str:
    """
    Inyecta medidas DAX en un proyecto Power BI de forma segura (crea copia primero).
    """
    logger.info(f"Iniciando migración SEGURA desde: {Path(ruta_archivo_pbip).name}")

    # 1. Validaciones de Excel
    if ruta_excel_manual and os.path.exists(ruta_excel_manual):
        ruta_excel = ruta_excel_manual
    else:
        ruta_excel = str(config.RUTA_EXCEL_AUDITORIA)

    if not os.path.exists(ruta_excel):
        return f"❌ Falta Excel: {ruta_excel}"

    if not os.path.exists(ruta_archivo_pbip):
        return "❌ Archivo PBIP no existe."

    # 2. Copia de seguridad (Sandboxing)
    path_trabajo, error = copiar_proyecto_pbip(ruta_archivo_pbip, carpeta_salida_copias)
    if error:
        return f"❌ Error al copiar: {error}"

    # 3. Localizar carpeta tables dentro del proyecto DESTINO
    target_semantico = None
    for item in os.listdir(path_trabajo):
        if item.endswith(".SemanticModel"):
            target_semantico = os.path.join(path_trabajo, item)
            break

    if not target_semantico:
        return "❌ Error crítico: La copia no tiene .SemanticModel"

    target_tables = os.path.join(target_semantico, "definition", "tables")
    os.makedirs(target_tables, exist_ok=True)

    # 3.5 Construir mapa de nombres de tabla real (desde archivos TMDL)
    # Esto es necesario para que las visuals referencien los nombres correctos
    mapa_tabla_redshift_a_tmdl = {}  # {'sap_fi_ar_v5_ajuste_postcierre': 'nombre real en tmdl'}

    for tmdl_file in os.listdir(target_tables):
        if tmdl_file.endswith(".tmdl"):
            tmdl_path = os.path.join(target_tables, tmdl_file)
            with open(tmdl_path, encoding="utf-8") as f:
                first_line = f.readline()
                # Extraer nombre de tabla de la primera línea: "table 'nombre' o table nombre"
                if first_line.startswith("table "):
                    nombre_tabla = (
                        first_line.replace("table ", "").strip().strip("'").strip('"')
                    )
                    # Buscar nombre de tabla Redshift en el archivo
                    f.seek(0)
                    contenido = f.read()
                    # Buscar FROM schema.tabla
                    import re

                    match = re.search(
                        r"FROM\s+(?:public\.)?(\w+)", contenido, re.IGNORECASE
                    )
                    if match:
                        tabla_redshift = match.group(1).lower()
                        mapa_tabla_redshift_a_tmdl[tabla_redshift] = nombre_tabla
                        logger.debug(f"Mapeado: {tabla_redshift} -> {nombre_tabla}")

    # 4. Sincronizar Orígenes de Datos (Tablas faltantes)
    if ruta_tableau_original and os.path.exists(ruta_tableau_original):
        try:
            logger.info("Sincronizando orígenes de datos Tableau -> PBI...")
            conexiones = analisis_logic.obtener_conexiones_tableau(
                ruta_tableau_original
            )

            # Listar tablas existentes en PBI
            tablas_existentes = {
                f.replace(".tmdl", "")
                for f in os.listdir(target_tables)
                if f.endswith(".tmdl")
            }

            nuevas_tablas = []
            for conn in conexiones:
                nombre_pbi = conn.get("Nombre_Tabla_Redshift") or conn.get(
                    "Fuente_Datos_Tableau"
                )
                if not nombre_pbi:
                    continue

                # Limpiar nombre para PBI
                nombre_pbi = (
                    nombre_pbi.replace("[", "").replace("]", "").replace(".", "_")
                )

                # Truncar si es muy largo para evitar errores de MAX_PATH (Windows limit ~260 chars total)
                # Path base ~140 chars. Margen seguridad: 80 chars max filename.
                if len(nombre_pbi) > 80:
                    import hashlib

                    hash_suffix = hashlib.md5(nombre_pbi.encode()).hexdigest()[:6]
                    nombre_pbi = f"{nombre_pbi[:70]}_{hash_suffix}"

                if (
                    nombre_pbi not in tablas_existentes
                    and nombre_pbi != config.NOMBRE_TABLA_MEDIDAS
                ):
                    logger.info(
                        f"Creando definición TMDL para nueva tabla: {nombre_pbi}"
                    )
                    # Obtener columnas básicas de esta fuente si es posible
                    # Por ahora usamos una lista vacía o placeholder, el usuario puede refrescar en PBI
                    # TODO: Extraer columnas reales desde el XML en analisis_logic
                    columnas = []

                    tmdl_content = tmdl_generator.crear_tabla_tmdl(
                        nombre_pbi, columnas, conn
                    )
                    with open(
                        os.path.join(target_tables, f"{nombre_pbi}.tmdl"),
                        "w",
                        encoding="utf-8",
                    ) as f:
                        f.write(tmdl_content)

                    nuevas_tablas.append(nombre_pbi)
                    tablas_existentes.add(nombre_pbi)

            # Actualizar model.tmdl si hay nuevas tablas
            if nuevas_tablas:
                ruta_model_tmdl = os.path.join(
                    target_semantico, "definition", "model.tmdl"
                )
                if os.path.exists(ruta_model_tmdl):
                    with open(ruta_model_tmdl, encoding="utf-8") as f:
                        lineas_model = f.readlines()

                    # Insertar "ref table" antes de "ref cultureInfo"
                    indice_insercion = len(lineas_model)
                    for i, linea in enumerate(lineas_model):
                        if "ref cultureInfo" in linea or "ref role" in linea:
                            indice_insercion = i
                            break

                    for nt in nuevas_tablas:
                        # Escapar si tiene espacios
                        nt_ref = f"'{nt}'" if " " in nt else nt
                        lineas_model.insert(indice_insercion, f"ref table {nt_ref}\n")
                        indice_insercion += 1

                    with open(ruta_model_tmdl, "w", encoding="utf-8") as f:
                        f.writelines(lineas_model)
                    logger.info(
                        f"model.tmdl actualizado con {len(nuevas_tablas)} tablas."
                    )

        except Exception as e:
            logger.error(f"Error sincronizando orígenes de datos: {e}", exc_info=True)

    # 5. Mapear de Fuente de Datos Tableau -> Archivo TMDL PBI
    # Paso necesario para inyectar medidas en su tabla correcta
    # mappa: { 'Nombre Datasource Tableau': 'nombre_archivo_pbi' }
    mapa_ds_pbi = {}

    # Reutilizamos 'conexiones' si existe, sino tratamos de obtenerlas
    lista_conexiones_debug = []
    if ruta_tableau_original and os.path.exists(ruta_tableau_original):
        lista_conexiones_debug = analisis_logic.obtener_conexiones_tableau(
            ruta_tableau_original
        )

    for conn in lista_conexiones_debug:
        ds_tableau = conn.get("Fuente_Datos_Tableau")
        # Misma lógica de nombre que usamos al crear las tablas
        nombre_pbi = conn.get("Nombre_Tabla_Redshift") or ds_tableau
        if nombre_pbi:
            # Limpiar nombre para PBI (igual que arriba)
            nombre_pbi_clean = (
                nombre_pbi.replace("[", "").replace("]", "").replace(".", "_")
            )
            if len(nombre_pbi_clean) > 80:
                import hashlib

                hash_suffix = hashlib.md5(nombre_pbi_clean.encode()).hexdigest()[:6]
                nombre_pbi_clean = f"{nombre_pbi_clean[:70]}_{hash_suffix}"

            mapa_ds_pbi[ds_tableau] = nombre_pbi_clean

    # 6. Leer Excel y generar TMDL de Medidas (Agrupado por tabla)
    try:
        df = pd.read_excel(ruta_excel)
        df = df.drop_duplicates(subset=["Nombre_Campo"], keep="first")
        logger.info(f"Excel cargado: {len(df)} fórmulas únicas")
    except Exception as e:
        return f"❌ Error leyendo Excel: {e}"

    # Agrupar medidas por su tabla destino (Fuente_Datos)
    medidas_por_tabla = {}
    # Inicializar con la tabla fallback
    nombre_tabla_fallback = config.NOMBRE_TABLA_MEDIDAS
    medidas_por_tabla[nombre_tabla_fallback] = []

    count = 0
    nombres_usados = set()

    for index, row in df.iterrows():
        if pd.isna(row["Formula_Tableau"]):
            continue

        nombre_original = str(row["Nombre_Campo"]).strip()
        nombre_seguro = f"{config.PREFIJO_MEDIDA_MIGRADA} {nombre_original}"
        if nombre_seguro.lower() in nombres_usados:
            nombre_seguro = f"{nombre_seguro}_{count}"
        nombres_usados.add(nombre_seguro.lower())

        nombre_tmdl = nombre_seguro.replace("'", "''")
        dax_ia = (
            str(row["DAX_IA"])
            if ("DAX_IA" in df.columns and pd.notna(row["DAX_IA"]))
            else ""
        )
        dax_clean = limpiar_dax_ia(dax_ia, nombre_original)

        formula_final = "BLANK()"
        if dax_clean and "ERROR" not in dax_clean.upper():
            formula_final = dax_clean

        bloque_dax = tmdl_generator.generar_medida_tmdl(
            nombre=nombre_tmdl,
            formula=formula_final,
            original_name=nombre_original,
            original_formula=str(row["Formula_Tableau"]),
        )

        # Determinar tabla destino
        fuente_tableau = row.get("Fuente_Datos")  # Columna del Excel
        tabla_destino = nombre_tabla_fallback

        if fuente_tableau and fuente_tableau in mapa_ds_pbi:
            posible_destino = mapa_ds_pbi[fuente_tableau]
            # Verificar que el archivo .tmdl exista realmente
            if os.path.exists(os.path.join(target_tables, f"{posible_destino}.tmdl")):
                tabla_destino = posible_destino

        if tabla_destino not in medidas_por_tabla:
            medidas_por_tabla[tabla_destino] = []

        medidas_por_tabla[tabla_destino].append(bloque_dax)
        count += 1

    # Escribir en archivos
    try:
        for tabla_dest, bloques in medidas_por_tabla.items():
            if not bloques:
                continue

            file_path = os.path.join(target_tables, f"{tabla_dest}.tmdl")

            # Si es la tabla fallback y no existe, crearla de cero
            if tabla_dest == nombre_tabla_fallback and not os.path.exists(file_path):
                content_fallback = (
                    f"table '{nombre_tabla_fallback}'\n"
                    f"\tlineageTag: {str(uuid.uuid4())}\n"
                    f"\tpartition '{nombre_tabla_fallback}' = m\n"
                    f"\t\tmode: import\n"
                    f"\t\tsource =\n"
                    f"\t\t\tlet\n"
                    f'\t\t\t\tSource = Table.FromRows({{}},{{"Col"}})\n'
                    f"\t\t\tin\n"
                    f"\t\t\t\tSource\n\n"
                )
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(
                        content_fallback
                        + "".join(bloques)
                        + "\n\tannotation PBI_ResultType = Table"
                    )
                logger.info(
                    f"Creada tabla fallback {tabla_dest} con {len(bloques)} medidas."
                )

            # Si es una tabla existente, append al final (antes de annotations si es posible, o simplemente append)
            elif os.path.exists(file_path):
                with open(file_path, "r+", encoding="utf-8") as f:
                    # En TMDL las medidas pueden ir al final del archivo.
                    f.seek(0, 2)  # Ir al final
                    f.write("\n" + "".join(bloques))
                logger.info(f"Inyectadas {len(bloques)} medidas en {tabla_dest}.")

    except Exception as e:
        return f"❌ Error escribiendo TMDL distribuido: {e}"

    # 4.5 Determinar schema final y corregir globalmente (existentes y nuevos)
    schema_final = "ciclo_ingresos_prd"  # Default
    if ruta_tableau_original and os.path.exists(ruta_tableau_original):
        try:
            conexiones_temp = analisis_logic.obtener_conexiones_tableau(
                ruta_tableau_original
            )
            for conn in conexiones_temp:
                schema_extraido = conn.get("Schema", "")
                if schema_extraido and schema_extraido != "public":
                    schema_final = schema_extraido
                    break
        except Exception:
            pass

    logger.info(f"Corrigiendo schema global 'public' -> '{schema_final}'...")
    archivos_corregidos = 0
    for tmdl_file in os.listdir(target_tables):
        if tmdl_file.endswith(".tmdl"):
            tmdl_path = os.path.join(target_tables, tmdl_file)
            with open(tmdl_path, encoding="utf-8") as f:
                contenido = f.read()

            # Solo procesar si contiene 'public.'
            if "public." in contenido.lower():
                contenido_nuevo = re.sub(
                    r"FROM\s+public\.",
                    f"FROM {schema_final}.",
                    contenido,
                    flags=re.IGNORECASE,
                )
                with open(tmdl_path, "w", encoding="utf-8") as f:
                    f.write(contenido_nuevo)
                archivos_corregidos += 1

    if archivos_corregidos:
        logger.info(
            f"Schema corregido en {archivos_corregidos} archivos TMDL (incluyendo tablas nuevas)"
        )

    # 5. Crear páginas PBIR (opcional)
    if ruta_tableau_original and os.path.exists(ruta_tableau_original):
        try:
            target_report = None
            for item in os.listdir(path_trabajo):
                if item.endswith(".Report"):
                    target_report = os.path.join(path_trabajo, item)
                    break

            if target_report:
                # --- LIMPIEZA DE PÁGINAS PREVIAS ---
                # Para evitar que 'crear_paginas_desde_tableau' salte páginas que coinciden en nombre
                # (debido a migraciones anteriores en el archivo base), limpiamos la carpeta pages.
                carpeta_pages = os.path.join(target_report, "definition", "pages")
                if os.path.exists(carpeta_pages):
                    logger.info(
                        f"Limpiando páginas preexistentes en {carpeta_pages}..."
                    )

                    # Borramos la carpeta entera y la recreamos para asegurar limpieza total
                    try:
                        print(f"INTENTANDO BORRAR: {carpeta_pages}")
                        shutil.rmtree(carpeta_pages, onerror=remove_readonly)
                        os.makedirs(carpeta_pages)
                        logger.info("Carpeta 'pages' recreada vacía.")
                        print("CARPETA PAGES BORRADA Y RECREADA ELIMINANDO REMANENTES.")
                    except Exception as e:
                        logger.warning(f"No se pudo limpiar carpeta pages: {e}")
                        print(f"ERROR BORRANDO CARPETA PAGES: {e}")

                hojas_tableau = analisis_logic.extraer_hojas_tableau(
                    ruta_tableau_original
                )
                if hojas_tableau:
                    pbir_logic.crear_paginas_desde_tableau(
                        target_report,
                        hojas_tableau,
                        ruta_tableau=ruta_tableau_original,
                        carpeta_semantic_model=target_semantico,
                        mapa_tablas_tmdl=mapa_tabla_redshift_a_tmdl,
                    )
        except Exception as e:
            logger.error(f"Error creando páginas PBIR: {e}")

    # --- VALIDACIÓN DE RESULTADOS ---
    # Validamos contra la lista maestra de 36 hojas
    logger.info("Iniciando validación final de hojas esperadas...")
    from core.validation.expected_sheets import HOJAS_ESPERADAS

    resultado_val = validar_proyecto(path_trabajo, expected_pages=HOJAS_ESPERADAS)

    if not resultado_val["exito"]:
        faltantes = resultado_val["faltantes"]
        logger.error(f"[ERROR] MIGRACION INCOMPLETA: Faltan {len(faltantes)} hojas.")

        # Log detallado para agentes IA
        logger.debug(f"Hojas creadas detectadas: {resultado_val['debug_creadas']}")
        logger.debug(f"Hojas faltantes: {faltantes}")

        logger.info(f"Borrando carpeta fallida: {path_trabajo}")
        try:
            shutil.rmtree(path_trabajo)
        except Exception as e:
            logger.warning(f"No se pudo borrar carpeta fallida: {e}")

        # Mensaje amigable para el usuario con detalle de faltantes
        msg_error = (
            f"[FAIL] Falla: Migracion incompleta. Faltan {len(faltantes)} hojas:\n"
        )
        msg_error += "\n".join(f"  - {h}" for h in faltantes[:15])
        if len(faltantes) > 15:
            msg_error += f"\n  ... y {len(faltantes) - 15} hojas mas."

        return msg_error

    # Si llegamos aquí, la validación fue exitosa
    success_msg = (
        f"--- PROCESO TERMINADO (MODO SEGURO) ---\n"
        f"Carpeta: {Path(path_trabajo).name}\n"
        f"Medidas creadas: {count}\n"
        f"Páginas PBIR creadas: {resultado_val['total_creadas']}"
    )

    return success_msg
