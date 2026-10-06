"""PBIP compiler package: neutral IR -> concrete PBIP project (TMDL + PBIR).

Exports the public adapter surface. Internal emitters (``dax``, ``tmdl``,
``pbir``) are kept as sibling modules so each stays small and testable; the
:mod:`core.compilers.pbip.adapter` module wires them into a single
:class:`PBIPAdapter` that produces a :class:`PBIPProject`.

Boundary (one-way dependency, same as :mod:`core.compilers.base`):

* depends on stdlib, ``core.contracts`` and ``core.compilers`` only;
* never imports concrete destination runtimes (``core.pbir_*`` /
  ``core.pbip_*`` / ``core.visual_*`` / DAX / connector engines);
* never reparses Tableau -- it consumes the accepted neutral IR.
"""

from core.compilers.pbip.adapter import (
    SCHEMA_VERSION,
    PBIPAdapter,
    PBIPFile,
    PBIPProject,
)

__all__ = [
    "PBIPAdapter",
    "PBIPFile",
    "PBIPProject",
    "SCHEMA_VERSION",
]
