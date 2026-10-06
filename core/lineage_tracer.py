import re


def generate_mermaid_lineage(audit_result, calc_analysis) -> str:
    """
    Genera un diagrama Mermaid del lineaje de datos.
    Siempre genera al menos un diagrama básico: Datasource → Columnas Usadas
    """

    lines = ["graph LR"]

    # Estilos de nodos
    lines.append("    classDef datasource fill:#2c3e50,stroke:#ebdbe,color:white;")
    lines.append("    classDef column fill:#27ae60,stroke:#1e8449,color:white;")
    lines.append("    classDef calc fill:#f39c12,stroke:#d35400,color:white;")
    lines.append("    classDef unused fill:#95a5a6,stroke:#7f8c8d,color:white;")

    processed_nodes = set()

    # Siempre mostrar datasources y sus columnas principales
    for ds in audit_result.datasources:
        ds_id = _clean_id(ds.name)
        caption = ds.caption.replace('"', "'")
        if len(caption) > 25:
            caption = caption[:22] + "..."

        lines.append(f'    ds_{ds_id}["📦 {caption}"]:::datasource')
        processed_nodes.add(f"ds_{ds_id}")

        # Mostrar hasta 5 columnas usadas como ejemplo
        used_cols = [c for c in ds.columns if c.is_used][:5]
        unused_count = len([c for c in ds.columns if not c.is_used])

        for col in used_cols:
            col_id = _clean_id(col.name)
            col_label = col.local_name.replace('"', "'")
            if len(col_label) > 20:
                col_label = col_label[:17] + "..."

            if col_id not in processed_nodes:
                lines.append(f'    col_{col_id}["✓ {col_label}"]:::column')
                lines.append(f"    ds_{ds_id} --> col_{col_id}")
                processed_nodes.add(col_id)

        # Nodo resumen de columnas no usadas
        if unused_count > 0:
            lines.append(f'    unused_{ds_id}["⚠️ {unused_count} no usadas"]:::unused')
            lines.append(f"    ds_{ds_id} -.-> unused_{ds_id}")

    # Agregar cálculos relevantes si existen
    if calc_analysis and calc_analysis.calculated_fields:
        complex_calcs = [c for c in calc_analysis.calculated_fields if c.complexity_score > 2][:5]

        for calc in complex_calcs:
            c_id = _clean_id(calc.name)
            caption = calc.caption.replace('"', "'")
            if len(caption) > 25:
                caption = caption[:22] + "..."

            if c_id not in processed_nodes:
                lines.append(f'    calc_{c_id}["ƒ {caption}"]:::calc')
                processed_nodes.add(c_id)

    return "\n".join(lines)


def _clean_id(text: str) -> str:
    """Genera un ID válido para Mermaid."""
    if not text:
        return "unknown"
    # Reemplazar caracteres no alfanuméricos
    s = re.sub(r"[^a-zA-Z0-9]", "_", text)
    # Evitar IDs que empiecen con numero
    if s and s[0].isdigit():
        s = "n" + s
    return s.strip("_")
