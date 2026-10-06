"""
Lista maestra de hojas esperadas en la migración Tableau -> Power BI.
Usada para validar que todas las páginas críticas se migraron correctamente.
"""

HOJAS_ESPERADAS = [
    "1.0 AR | Resumen",
    "1.1 AR | Bruto",
    "1.2 AR | Neto",
    "1.3 AR | Contable",
    "2. AR | Pacientes Hospitalizados",
    "3. AR | Pacientes Pendientes Cierre",
    "4. AR | Pendiente Envio Isapre",
    "5. AR | Cuenta Devueltas",
    "6. AR | Pendiente Bonificacion",
    "7. AR | Saldos Anticipos -Abonos",
    "8. AR | Pendiente Digitación",
    "9. AR | Documentos por Facturar",
    "10. AR | Seguros Complementarios",
    "11. AR | Cobro Paciente",
    "12. AR | Ejecución de Garantía",
    "13. AR | Pre-Judicial",
    "14. AR | Judicial",
    "15. AR | Otras Carteras",
    "16. AR | Facturas por Cobrar",
    "17. AR | Deudores Comerciales",
    "18. AR | Provisiones",
    "19. AR | Convenios de pago y cheques a fecha",
    "20. AR | Pendiente regularización",
    "21. AR | Stock Cuentas Puente",
    "22. AR | Presupuestos",
    "23. AR | Garante I",
    "23. AR | Garante II",
    "23. AR | Garante III",
    "24. AR | Prefacturas sin Hoja de Ruta",
    "25. AR | Otras clases de documentos",
    "26. Descarga Datos",
    "27 AR | Resumen Post-Cierre",
    "27. AR | Ajuste Post-Cierre AR Neto",
    "27 AR | Ajuste Post-Cierre Contable",
    "28. AR | Pendiente Compañía de Salud UC CHRISTUS",
    "29. Credito y Boletas por cobrar",
    "30. Cuenta Paciente Ambulatoria",
]


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
