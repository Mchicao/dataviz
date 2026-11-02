"""
Utilidades genéricas de comparación de nombres de hojas.
Cada corpus público debe declarar sus propias hojas esperadas.
"""

HOJAS_ESPERADAS: list[str] = []


def normalizar(nombre: str) -> str:
    """Normaliza nombre para comparacion flexible (sin acentos, minusculas, sin pipes extras)."""
    import unicodedata

    if not nombre:
        return ""
    # Quitar acentos
    nombre_sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", nombre) if unicodedata.category(c) != "Mn"
    )
    # Limpieza agresiva de caracteres especiales y espacios para asegurar matching
    return (
        nombre_sin_acentos.lower()
        .strip()
        .replace("|", "")
        .replace(".", "")
        .replace("  ", " ")
        .replace(" ", "")
    )


def obtener_hojas_faltantes(hojas_creadas: list) -> list:
    """
    Compara las hojas creadas contra la lista maestra.
    Retorna lista de hojas esperadas que NO fueron encontradas.
    """
    creadas_norm = {normalizar(h) for h in hojas_creadas if h}
    faltantes = []
    for hoja_esperada in HOJAS_ESPERADAS:
        hoja_norm = normalizar(hoja_esperada)
        if hoja_norm not in creadas_norm:
            faltantes.append(hoja_esperada)
    return faltantes


def obtener_hojas_extras(hojas_creadas: list) -> list:
    """
    Retorna hojas creadas que NO estaban en la lista esperada (extras).
    Util para debugging.
    """
    esperadas_norm = {normalizar(h) for h in HOJAS_ESPERADAS}
    extras = []
    for hoja in hojas_creadas:
        if hoja and normalizar(hoja) not in esperadas_norm:
            extras.append(hoja)
    return extras
