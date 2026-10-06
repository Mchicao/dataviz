"""Query planner: validates a ``QuerySpec`` against an injected ``QueryPolicy``.

The planner sits between the query AST (:mod:`core.contracts.query_ast`) and the
in-memory executor (:mod:`apps.dataviz_service.runtime.executor`). It enforces a
declarative :class:`~core.contracts.query_ast.QueryPolicy` (datasource / field
allowlists, limit caps, raw-expression guard) and yields a :class:`PlannedQuery`
that the executor can run unchanged.

Design (Ponytail): the AST is already structurally raw-SQL-free -- it has no raw
field and only the closed ``ALLOWED_OPS`` / ``ALLOWED_FUNCS`` allowlists. The
planner does not re-parse, does not emit SQL and does not import any SQL engine;
it only walks the query graph to enforce policy and optionally rewrites
``limit``. Field/schema validity against the concrete dataset stays owned by the
executor, so the planner remains datasource-agnostic and trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from apps.dataviz_service.runtime.errors import DataVizRuntimeError
from core.contracts.query_ast import QueryPolicy, QuerySpec
from core.contracts.semantic_ir import Expression, walk_expression

#: Attribute names that would indicate a raw (non-AST) expression leaked into
#: the query graph. ``QuerySpec`` is structurally free of these; the scan below
#: enforces the invariant at planning time so a future regression cannot bypass
#: the allowlist by smuggling a raw SQL/Python payload.
_RAW_ATTRS: tuple[str, ...] = ("raw_sql", "raw_expr", "raw_expression", "python_code")


class PolicyViolationError(DataVizRuntimeError):
    """Raised when a query violates the injected :class:`QueryPolicy`."""


@dataclass(frozen=True)
class PlannedQuery:
    """Validated (and possibly rewritten) query ready for execution.

    Attributes:
        query: The query to execute; ``limit`` may have been clamped/injected.
        applied: Audit trail of policy decisions applied during planning
            (e.g. ``"limit clamped: 5000 -> 1000"``). Empty when untouched.
        policy: The policy snapshot used to plan, kept for traceability.
    """

    query: QuerySpec
    applied: tuple[str, ...]
    policy: QueryPolicy


class QueryPlanner:
    """Plans a :class:`QuerySpec` against a :class:`QueryPolicy`.

    Enforcement order (fail-fast; each violation raises
    :class:`PolicyViolationError`):

      1. raw-expression guard -- rejects any raw SQL/Python surface,
      2. datasource allowlist,
      3. field allowlist/denylist across ``select`` / ``filters`` /
         ``selections`` / ``group_by``,
      4. ``require_non_empty_select`` (rejects implicit ``SELECT *``),
      5. limit policy -- inject ``default_limit``, clamp to ``max_limit``.

    Note: ``sort_by`` is intentionally **not** field-checked here. Sort keys
    operate on *output* columns (which may be aggregation aliases); their
    validity is verified by the executor against the produced output. Every
    *input* field is still policy-checked via ``select`` / ``filters`` /
    ``group_by``, so no unauthorized field can ever be read -- a sort referencing
    an unselected field is rejected downstream by the executor.
    """

    def __init__(self, policy: QueryPolicy | None = None) -> None:
        self._policy = policy or QueryPolicy()

    @property
    def policy(self) -> QueryPolicy:
        """The policy this planner enforces."""
        return self._policy

    def plan(self, query: QuerySpec) -> PlannedQuery:
        """Validate ``query`` against the policy and return a :class:`PlannedQuery`."""
        policy = self._policy
        self._reject_raw_expressions(query, policy)
        self._check_datasource(query, policy)
        self._check_fields(query, policy)
        self._check_non_empty_select(query, policy)
        new_limit, note = self._enforce_limit(query.limit, policy)
        applied: list[str] = [note] if note else []
        if new_limit == query.limit:
            return PlannedQuery(query=query, applied=tuple(applied), policy=policy)
        rewritten = replace(query, limit=new_limit)
        return PlannedQuery(query=rewritten, applied=tuple(applied), policy=policy)

    # --- guards -----------------------------------------------------------

    @staticmethod
    def _reject_raw_expressions(query: QuerySpec, policy: QueryPolicy) -> None:
        if not policy.reject_raw_expressions:
            return
        nodes: list[Any] = [
            query,
            *query.select,
            *query.filters,
            *getattr(query, "selections", ()),
            *query.group_by,
            *query.sort_by,
        ]
        for node in nodes:
            leaked = [attr for attr in _RAW_ATTRS if hasattr(node, attr)]
            if leaked:
                raise PolicyViolationError(
                    f"raw expression attribute(s) {leaked!r} are forbidden in the query AST"
                )

    @staticmethod
    def _check_datasource(query: QuerySpec, policy: QueryPolicy) -> None:
        if policy.allowed_datasources and query.from_datasource not in policy.allowed_datasources:
            raise PolicyViolationError(
                f"datasource {query.from_datasource!r} not in policy allowlist "
                f"({len(policy.allowed_datasources)} allowed)"
            )

    @staticmethod
    def _check_fields(query: QuerySpec, policy: QueryPolicy) -> None:
        if not policy.allowed_fields and not policy.denied_fields:
            return
        expressions: list[Expression] = [item.expression for item in query.select]
        expressions.extend(predicate.expression for predicate in query.filters)
        expressions.extend(predicate.expression for predicate in query.selections)
        expressions.extend(query.group_by)
        referenced = [
            node.name
            for expression in expressions
            for node in walk_expression(expression)
            if node.kind == "field_ref"
        ]
        for name in referenced:
            if name in policy.denied_fields:
                raise PolicyViolationError(f"field {name!r} is denied by policy")
            if policy.allowed_fields and name not in policy.allowed_fields:
                raise PolicyViolationError(f"field {name!r} not in field allowlist")

    @staticmethod
    def _check_non_empty_select(query: QuerySpec, policy: QueryPolicy) -> None:
        if policy.require_non_empty_select and not query.select:
            raise PolicyViolationError(
                "policy requires an explicit select list (SELECT * is not allowed)"
            )

    @staticmethod
    def _enforce_limit(limit: int | None, policy: QueryPolicy) -> tuple[int | None, str]:
        """Return ``(new_limit, audit_note)``; empty note means nothing applied."""
        if limit is None:
            if policy.default_limit is not None:
                return policy.default_limit, f"default_limit applied: {policy.default_limit}"
            return None, ""
        if policy.max_limit is not None and limit > policy.max_limit:
            return policy.max_limit, f"limit clamped: {limit} -> {policy.max_limit}"
        return limit, ""


__all__ = [
    "PlannedQuery",
    "PolicyViolationError",
    "QueryPlanner",
]
