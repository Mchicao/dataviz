import os
import xml.etree.ElementTree as ET


def generate_optimized_twb_content(
    twb_path: str, unused_columns_per_datasource: dict[str, list[str]]
) -> str:
    """
    Genera el contenido XML de un TWB con las columnas no usadas marcadas como ocultas.

    Args:
        twb_path: Ruta al archivo TWB original.
        unused_columns_per_datasource: Diccionario {datasource_name: [lista_columnas_unused]}

    Returns:
        String con el contenido XML modificado.
    """

    if not os.path.exists(twb_path):
        raise FileNotFoundError(f"Archivo no encontrado: {twb_path}")

    try:
        tree = ET.parse(twb_path)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f"Error parseando TWB: {e}")

    modified_count = 0

    for ds in root.findall(".//datasources/datasource"):
        ds_name = ds.get("name") or ""
        ds_caption = ds.get("caption", ds_name) or ""

        # Intentar matchear por caption o name
        columns_to_hide = unused_columns_per_datasource.get(
            ds_caption
        ) or unused_columns_per_datasource.get(ds_name)

        if not columns_to_hide:
            continue

        # Normalizar lista para comparación rápida (quitar corchetes)
        normalized_targets = {c.replace("[", "").replace("]", "").strip() for c in columns_to_hide}

        for col in ds.findall("column"):
            col_name = col.get("name")
            if not col_name:
                continue

            # Normalizar nombre del XML
            clean_name = col_name.replace("[", "").replace("]", "").strip()

            if clean_name in normalized_targets:
                # Verificar si ya está oculto
                if col.get("hidden") != "true":
                    col.set("hidden", "true")
                    modified_count += 1

    # Retornar XML como string
    # ET.tostring devuelve bytes, decode a utf-8
    xml_str = ET.tostring(root, encoding="utf-8").decode("utf-8")

    # Agregar header XML que ET suele omitir o simplificar
    if not xml_str.startswith("<?xml"):
        xml_str = "<?xml version='1.0' encoding='utf-8' ?>\n" + xml_str

    return xml_str
