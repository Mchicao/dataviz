"""Minimal TMDL (Tabular Model Definition Language) text reader.

Parses Power BI TMDL text files into a neutral nested block tree so the
Power BI importer can map them to :class:`SourceAST` nodes without invoking
any destination runtime (TOM engine, PBIP compiler, Power BI Desktop).

Design (Ponytail): stdlib only. The reader is intentionally forgiving -- it
classifies header vs property lines by indentation and a small keyword set,
extracts the structured pieces the importer/IR compiler need (tables, columns,
measures, relationships, expressions) and preserves the verbatim source text of
every block so no information is dropped. Unknown keywords survive as generic
blocks with their ``raw_text`` intact.

Boundary: Python standard library only. No ``core.compilers.*``, no
``core.pbir_*``, no ``core.tom_engine``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Keywords that start a TMDL block (camelCase, first word lowercase per spec).
#: Anything matching ``^<word>\s+`` whose first word is here is treated as a
#: block header. Other header-shaped lines still parse as generic blocks.
_BLOCK_KEYWORDS: frozenset[str] = frozenset(
    {
        "model",
        "role",
        "tablePermission",
        "table",
        "column",
        "calculatedColumn",
        "calculatedTableColumn",
        "measure",
        "partition",
        "hierarchy",
        "relationship",
        "expression",
        "annotation",
        "culture",
        "perspective",
        "queryGroup",
        "var",
        "refreshPolicy",
        "detailRowsDefinition",
        "group",
        "modelExtensions",
        "linguisticMetadata",
        "dataSource",
        "providerDataSource",
        "structuredDataSource",
        "changedProperty",
        "removedProperty",
        "extendedProperty",
        "alternateOf",
    }
)

#: Regex for a property line ``key: value`` (TMDL canonical form).
_PROP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
#: Regex that strips surrounding single quotes from a TMDL identifier.
_QUOTED_RE = re.compile(r"^'(.*)'$")
#: Opening fence for multi-line measure expressions.
_FENCE = "```"


def _indent_level(line: str) -> int:
    """Return indentation level: leading tabs counted directly.

    Falls back to groups of leading spaces (4 per level) when no tabs are
    present, so space-indented TMDL still parses.
    """
    tabs = 0
    spaces = 0
    for ch in line:
        if ch == "\t":
            tabs += 1
        elif ch == " ":
            spaces += 1
        else:
            break
    if tabs:
        return tabs
    return spaces // 4


def _unquote(name: str) -> str:
    """Strip surrounding single quotes from a TMDL identifier if present."""
    m = _QUOTED_RE.match(name)
    return m.group(1) if m else name


@dataclass
class TmdlBlock:
    """A single TMDL block (e.g. ``table``, ``column``, ``measure``).

    Attributes:
        keyword: first token of the header line (e.g. ``"table"``, ``"measure"``).
        signature: full text after the keyword on the header line.
        name: parsed identifier (quotes stripped); empty when the block has no
            name (e.g. bare ``annotation``).
        inline_expr: text after ``=`` on the header line, for measure /
            partition / expression blocks that declare their body inline.
            Empty when the body is multi-line or absent.
        multiline_expr: joined body lines for fenced (```` ``` ````) measure
            bodies; empty otherwise.
        properties: ``key: value`` property lines directly under the header.
        children: nested :class:`TmdlBlock` nodes.
        raw_text: verbatim source lines (header + body) joined by ``\\n``;
            lossless preservation for the importer.
    """

    keyword: str
    signature: str = ""
    name: str = ""
    inline_expr: str = ""
    multiline_expr: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    children: list[TmdlBlock] = field(default_factory=list)
    raw_text: str = ""

    def __post_init__(self) -> None:
        if self.children is None or isinstance(self.children, type):
            object.__setattr__(self, "children", [])
        elif not isinstance(self.children, list):
            object.__setattr__(self, "children", list(self.children))

    @property
    def expression(self) -> str:
        """Best-effort DAX/M expression body: inline, else fenced multiline."""
        if self.inline_expr and self.inline_expr != _FENCE:
            return self.inline_expr
        return self.multiline_expr

    def child(self, keyword: str, name: str | None = None) -> TmdlBlock | None:
        """First direct child matching ``keyword`` (and ``name`` if given)."""
        for c in self.children:
            if c.keyword == keyword and (name is None or c.name == name):
                return c
        return None

    def children_of(self, keyword: str) -> list[TmdlBlock]:
        """All direct children with the given keyword, in source order."""
        return [c for c in self.children if c.keyword == keyword]


def _split_header(content: str) -> tuple[str, str, str]:
    """Split a header line into ``(keyword, name, inline_expr)``.

    A header is ``<keyword> <signature>`` where ``<signature>`` is either a
    bare name, ``<name> = <expr>``, or just ``= <expr>`` (annotation/value).
    Returns empty strings for the parts that are absent.
    """
    stripped = content.strip()
    sp = stripped.split(None, 1)
    keyword = sp[0] if sp else ""
    rest = sp[1].strip() if len(sp) > 1 else ""

    inline_expr = ""
    name = rest
    if "=" in rest:
        head, _, tail = rest.partition("=")
        name = head.strip()
        inline_expr = tail.strip()
    return keyword, _unquote(name), inline_expr


def parse_tmdl(text: str) -> list[TmdlBlock]:
    """Parse TMDL ``text`` into a list of top-level :class:`TmdlBlock`.

    The parser is indentation-based and tolerant: lines are classified as block
    headers (first token is a word, optionally followed by a name/``= expr``)
    or property lines (``key: value``). Nested blocks are children of the most
    recent header at a lower indent. Every block keeps its verbatim source in
    ``raw_text`` so the importer can preserve unknowns losslessly.
    """
    # Tokenize non-blank lines into (indent, content, raw_line).
    entries: list[tuple[int, str, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = _indent_level(raw)
        entries.append((indent, raw.strip(), raw))

    blocks: list[TmdlBlock] = []
    # Stack of (indent, block) for open ancestors.
    stack: list[tuple[int, TmdlBlock]] = []
    # Fence tracking: when collecting a fenced measure body, this holds the
    # (block, fence_indent) we are appending lines to.
    fence_ctx: tuple[TmdlBlock, int] | None = None
    # Indented (non-fenced) expression body: headers like ``source =`` open a
    # verbatim body captured into ``multiline_expr`` until a line at the same
    # or lower indent closes it. Needed to recover partition M source code.
    expr_ctx: tuple[TmdlBlock, int] | None = None

    for indent, content, raw in entries:
        # Inside a fenced multiline expression, capture every deeper line
        # verbatim until the closing fence.
        if fence_ctx is not None:
            blk, fence_indent = fence_ctx
            if content == _FENCE:
                blk.raw_text += "\n" + raw
                fence_ctx = None
                continue
            blk.multiline_expr += (
                ("\n" + raw.strip("\t ")) if blk.multiline_expr else raw.strip("\t ")
            )
            blk.raw_text += "\n" + raw
            continue

        # Inside an indented expression body (``source =``): capture deeper
        # lines verbatim; the first line at header indent or lower closes it.
        if expr_ctx is not None:
            blk, base_indent = expr_ctx
            if indent <= base_indent:
                expr_ctx = None  # fall through: classify this line normally
            else:
                body_line = raw.strip("\t ")
                if body_line == _FENCE:
                    # A fenced body nested under an ``=`` header contributes
                    # its content only, not the fence markers themselves.
                    blk.raw_text += "\n" + raw
                    continue
                blk.multiline_expr += ("\n" + body_line) if blk.multiline_expr else body_line
                blk.raw_text += "\n" + raw
                continue

        prop_match = _PROP_RE.match(content)
        # A line is a property only if its key is NOT a block keyword; this
        # avoids misclassifying ``table Foo`` (no colon anyway) and keeps
        # ``column Amount`` headers from being read as properties.
        is_property = bool(prop_match) and prop_match.group(1) not in _BLOCK_KEYWORDS

        # Pop ancestors that are at the same or deeper indent.
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if is_property and stack:
            key = prop_match.group(1)
            val = prop_match.group(2).strip()
            parent = stack[-1][1]
            parent.properties[key] = val
            parent.raw_text += "\n" + raw
            continue

        keyword, name, inline_expr = _split_header(content)
        blk = TmdlBlock(
            keyword=keyword,
            signature=content[len(keyword) :].strip(),
            name=name,
            inline_expr=inline_expr,
            raw_text=raw,
        )
        # If this block's header opens a fenced expression, switch to fence mode
        # so following deeper lines become the multiline body.
        if inline_expr == _FENCE:
            fence_ctx = (blk, indent)
        elif content.rstrip().endswith("=") and not inline_expr:
            # ``source =`` / ``expression Foo =``: open an indented body capture.
            expr_ctx = (blk, indent)

        if stack:
            stack[-1][1].children.append(blk)
        else:
            blocks.append(blk)
        stack.append((indent, blk))

    return blocks


__all__ = ["TmdlBlock", "parse_tmdl"]
