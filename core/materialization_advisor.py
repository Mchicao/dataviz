import re
from dataclasses import dataclass

from core.calculation_extractor import CalculatedField


@dataclass
class MaterializationSuggestion:
    calc_name: str
    logic_type: str  # 'CASE' or 'IF'
    complexity: int  # Number of branches
    description: str

    @property
    def title(self) -> str:
        return f"Materializar lógica de {self.calc_name}"


def analyze_materialization_opportunities(
    calculations: list[CalculatedField], threshold: int = 4
) -> list[MaterializationSuggestion]:
    """
    Analiza cálculos para detectar lógica condicional compleja que se beneficiaría
    de ser movida a una tabla de búsqueda (Lookup Table) en la base de datos.
    """
    suggestions = []

    for calc in calculations:
        formula = calc.formula.upper()

        # Detectar CASE
        if "CASE" in formula:
            when_count = formula.count("WHEN")
            if when_count >= threshold:
                suggestions.append(
                    MaterializationSuggestion(
                        calc_name=calc.caption,
                        logic_type="CASE",
                        complexity=when_count,
                        description=f"El cálculo contiene {when_count} condiciones WHEN. Se recomienda crear una tabla de equivalencias en la BD.",
                    )
                )
                continue  # Skip IF check if CASE found (usually they are exclusive or nested)

        # Detectar IF / ELSEIF
        if re.search(r"\bIF\b|\bIIF\s*\(", formula):
            branch_count = len(re.findall(r"\bIF\b|\bELSEIF\b|\bIIF\s*\(", formula))
            if branch_count >= threshold:
                suggestions.append(
                    MaterializationSuggestion(
                        calc_name=calc.caption,
                        logic_type="IF/ELSEIF",
                        complexity=branch_count,
                        description=f"El cálculo contiene {branch_count} bloques lógicos. Considera mover esta lógica a una tabla dimensional.",
                    )
                )

    return sorted(suggestions, key=lambda x: x.complexity, reverse=True)
