"""Power BI compiler package: Power BI SourceAST -> neutral IRs + RenderPlan.

Long-term compiler for the Power BI branch of the neutral DataVIZ pipeline. It
consumes a Power BI-origin :class:`core.contracts.source_ast.SourceAST` and
emits the four neutral artifacts: the accepted
:class:`core.contracts.semantic_ir.SemanticModel` (reused from the Power BI
importer's :class:`~core.importers.powerbi.PowerBIAstCompiler`),
:class:`core.contracts.presentation_ir.PresentationIR`,
:class:`core.contracts.interaction_ir.InteractionIR`, and the executable
:class:`core.compilers.render_plan.RenderPlan` wrapped in a
:class:`~core.compilers.base.DestinationPlan` envelope.

Boundary (one-way dependency, same as :mod:`core.compilers.base`):

* depends on stdlib, ``core.contracts.*``, ``core.compilers.*`` and the sibling
  Power BI semantic compiler (``core.importers.powerbi``) -- which itself only
  depends on contracts + stdlib;
* never imports destination runtimes (``core.pbir_*`` / ``core.pbip_*`` /
  ``core.visual_*`` / DAX / connector engines);
* never re-parses PBIP/PBIT bytes.

Public API:

* :class:`PowerBIRenderPlanCompiler` -- the compiler.
* :func:`compile_powerbi_render_plan` -- shortcut for the default compiler.
* :class:`PowerBIRenderPlanCompilation` -- the bundled neutral artifacts.
"""

from core.compilers.powerbi.compiler import (
    PowerBIRenderPlanCompilation,
    PowerBIRenderPlanCompiler,
    compile_powerbi_render_plan,
)

__all__ = [
    "PowerBIRenderPlanCompilation",
    "PowerBIRenderPlanCompiler",
    "compile_powerbi_render_plan",
]
