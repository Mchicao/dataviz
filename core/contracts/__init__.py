"""Contratos canónicos versionados del migrador.

Este paquete define contratos pasivos: sólo transportan datos y nunca importan
módulos destino (PBIR / visual JSON). Los productores (parsers) y consumidores
(generadores PBIR, verificadores) dependen de estos contratos, no al revés.
"""

from core.contracts.coherence import validate_cross_ir_coherence
from core.contracts.interaction_ir import InteractionIR
from core.contracts.permissions import Permission
from core.contracts.presentation_ir import PresentationIR
from core.contracts.source_ast import SCHEMA_VERSION, SourceAST

__all__ = [
    "SCHEMA_VERSION",
    "InteractionIR",
    "Permission",
    "PresentationIR",
    "SourceAST",
    "validate_cross_ir_coherence",
]
