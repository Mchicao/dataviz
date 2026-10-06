"""
DAX Formatter - Formatea código DAX usando la API de SQLBI.

Integra con daxformatter.com para formatear expresiones DAX
generadas durante la migración Tableau → Power BI.
"""

import re

# Flag para indicar si requests está disponible
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


DAX_FORMATTER_URL = "https://www.daxformatter.com/api/daxformatter/DaxFormat"


def format_dax(dax_code: str, timeout: int = 10) -> str:
    """
    Formatea código DAX usando la API de SQLBI.

    Args:
        dax_code: Código DAX a formatear
        timeout: Timeout en segundos para la petición

    Returns:
        Código DAX formateado, o el original si hay error
    """
    if not REQUESTS_AVAILABLE:
        return _format_dax_local(dax_code)

    if not dax_code or not dax_code.strip():
        return dax_code

    try:
        response = requests.post(
            DAX_FORMATTER_URL,
            data={"fx": dax_code},
            timeout=timeout,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code == 200:
            result = response.json()
            formatted = result.get("formatted", dax_code)
            # Si hay errores, retornar original
            if result.get("errors"):
                return dax_code
            return formatted

        return dax_code

    except Exception:
        # Fallback a formateo local básico
        return _format_dax_local(dax_code)


def _format_dax_local(dax_code: str) -> str:
    """
    Formateo local básico cuando la API no está disponible.

    Aplica reglas simples de indentación y espaciado.
    """
    if not dax_code:
        return dax_code

    # Normalizar espacios
    code = " ".join(dax_code.split())

    # Agregar saltos de línea después de keywords principales
    keywords = [
        "RETURN",
        "VAR",
        "EVALUATE",
        "DEFINE",
        "MEASURE",
        "COLUMN",
    ]

    for kw in keywords:
        code = re.sub(rf"\b({kw})\b", r"\n\1", code, flags=re.IGNORECASE)

    # Agregar saltos después de comas en listas de argumentos largas
    # Solo si hay más de 3 argumentos
    if code.count(",") > 3:
        code = re.sub(r",\s*", ",\n    ", code)

    # Limpiar múltiples saltos de línea
    code = re.sub(r"\n\s*\n", "\n", code)

    return code.strip()


def format_measure(measure_name: str, expression: str) -> str:
    """
    Formatea una medida DAX completa.

    Args:
        measure_name: Nombre de la medida
        expression: Expresión DAX

    Returns:
        Medida formateada
    """
    formatted_expr = format_dax(expression)
    return f"{measure_name} = \n{formatted_expr}"


def validate_dax_syntax(dax_code: str) -> tuple[bool, str]:
    """
    Valida sintaxis DAX básica (sin API).

    Args:
        dax_code: Código DAX a validar

    Returns:
        Tuple (es_valido, mensaje_error)
    """
    if not dax_code or not dax_code.strip():
        return False, "Código DAX vacío"

    # Verificar balance de paréntesis
    open_parens = dax_code.count("(")
    close_parens = dax_code.count(")")
    if open_parens != close_parens:
        return False, f"Paréntesis desbalanceados: {open_parens} abiertos, {close_parens} cerrados"

    # Verificar balance de comillas
    quotes = dax_code.count('"')
    if quotes % 2 != 0:
        return False, "Comillas desbalanceadas"

    # Verificar balance de corchetes
    open_brackets = dax_code.count("[")
    close_brackets = dax_code.count("]")
    if open_brackets != close_brackets:
        return (
            False,
            f"Corchetes desbalanceados: {open_brackets} abiertos, {close_brackets} cerrados",
        )

    return True, ""


if __name__ == "__main__":
    # Ejemplo de uso
    sample_dax = """
    CALCULATE(SUM(Sales[Amount]),FILTER(ALL(Date),Date[Year]=YEAR(TODAY())),Products[Category]="Electronics")
    """

    print("DAX Original:")
    print(sample_dax)
    print("\nDAX Formateado:")
    print(format_dax(sample_dax))
