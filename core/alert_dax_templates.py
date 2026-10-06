

class AlertDaxGenerator:
    @staticmethod
    def generate_message_measure(
        table_name: str,
        check_empty: bool = True,
        check_maintenance: bool = True,
        check_outdated: bool = False,
        date_column: str | None = None,
        days_tolerance: int = 7,
        custom_message: str | None = None,
        kpi_rules: list[dict] | None = None,
    ) -> str:
        """
        Genera la medida DAX de Mensaje de Alerta soportando múltiples reglas KPI.
        Cada kpi_rule debe ser un dict con: {'expression': str, 'operator': str, 'threshold': str, 'message': Optional[str]}
        """

        dax = "// Medida generada automáticamente por BI Bridge Studio\n"
        dax += "VAR _ActivarMantenimiento = 0 -- Cambiar a 1 para activar manualmente\n"

        # Conteo de filas
        dax += f"VAR _ConteoDatos = COUNTROWS('{table_name}') + 0\n"

        # Lógica de Fecha
        if check_outdated and date_column:
            dax += f"VAR _UltimaFecha = MAX('{table_name}'[{date_column}])\n"
            dax += "VAR _DiasDesactualizado = DATEDIFF(_UltimaFecha, TODAY(), DAY)\n"

        # Lógica KPI (Múltiples Reglas)
        kpi_vars = []
        if kpi_rules:
            for i, rule in enumerate(kpi_rules):
                expr = rule.get("expression")
                op = rule.get("operator", "<")
                val = rule.get("threshold", "0")
                var_name = f"_KPIAlerta_{i}"
                dax += f"VAR {var_name} = ({expr}) {op} {val}\n"
                kpi_vars.append(var_name)

        dax += "\n// Criterios de Activación\n"
        conditions = []
        messages_parts = []

        if check_maintenance:
            dax += 'VAR _MsgMaint = IF(_ActivarMantenimiento = 1, "🔧 MODO MANTENIMIENTO ACTIVO", BLANK())\n'
            conditions.append("_ActivarMantenimiento = 1")
            messages_parts.append("_MsgMaint")

        if check_empty:
            dax += 'VAR _MsgEmpty = IF(_ConteoDatos = 0, "🚫 LA TABLA NO TIENE DATOS", BLANK())\n'
            conditions.append("_ConteoDatos = 0")
            messages_parts.append("_MsgEmpty")

        if check_outdated and date_column:
            dax += f'VAR _MsgOutdated = IF(_DiasDesactualizado > {days_tolerance}, "📅 DATOS DESACTUALIZADOS (" & _DiasDesactualizado & " días)", BLANK())\n'
            conditions.append(f"_DiasDesactualizado > {days_tolerance}")
            messages_parts.append("_MsgOutdated")

        if kpi_rules:
            for i, rule in enumerate(kpi_rules):
                rule_msg = rule.get("message", f"KPI fuera de rango ({i + 1})")
                dax += f'VAR _MsgKPI_{i} = IF({kpi_vars[i]}, "⚠️ {rule_msg}", BLANK())\n'
                conditions.append(kpi_vars[i])
                messages_parts.append(f"_MsgKPI_{i}")

        dax += "\nRETURN\n"
        if custom_message:
            # Si hay mensaje personalizado del usuario, mostramos ESE mensaje si algo se activa
            condition_str = " || ".join(conditions) if conditions else "FALSE()"
            dax += f'    IF({condition_str}, "{custom_message}", BLANK())\n'
        else:
            # Si no, concatenamos los mensajes automáticos
            # Usamos COMBINEVALUES para evitar espacios extra si faltan algunos
            msgs_joined = ", ".join(messages_parts)
            dax += f'    COMBINEVALUES(" | ", {msgs_joined})\n'

        return dax

    @staticmethod
    def generate_color_text_measure(table_name: str, measure_name_message: str) -> str:
        """
        Genera la medida DAX para Color de Texto.
        Depende de la medida de Mensaje (si mensaje no es blank -> alerta activa).
        """
        dax = f"""
VAR _AlertaActiva = NOT(ISBLANK([{measure_name_message}]))
RETURN
    IF(
        _AlertaActiva,
        "#D64550", -- Rojo
        "#FFFFFF00" -- Transparente
    )
"""
        return dax.strip()

    @staticmethod
    def generate_color_bg_measure(table_name: str, measure_name_message: str) -> str:
        """
        Genera la medida DAX para Color de Fondo.
        Depende de la medida de Mensaje.
        """
        dax = f"""
VAR _AlertaActiva = NOT(ISBLANK([{measure_name_message}]))
RETURN
    IF(
        _AlertaActiva,
        "#FFFFFF", -- Blanco Opaco (tapa el visual de atrás)
        "#FFFFFF00" -- Transparente
    )
"""
        return dax.strip()
