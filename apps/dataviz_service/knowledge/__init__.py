"""Knowledge Plane persistence surfaces.

Production code uses :class:`PostgresKnowledgeRegistry`, backed by the same
tenant-scoped PostgreSQL authority as durable versions/governance. The legacy
SQLite :class:`KnowledgeRegistry` remains only as a fast test/dev compatibility
backend; it is not a production authority.
"""

from apps.dataviz_service.knowledge.postgres_registry import PostgresKnowledgeRegistry
from apps.dataviz_service.knowledge.registry import KnowledgeRegistry, RegistrationConflict

__all__ = ["KnowledgeRegistry", "PostgresKnowledgeRegistry", "RegistrationConflict"]
