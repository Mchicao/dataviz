import React, { useMemo, useState } from 'react';

import type { QueryResults, Scalar } from '../../runtime/types';
import type { HierarchyNavigatorSpec } from '../../interactions';

export interface HierarchyNavigatorSelection {
  id: string;
  label: string;
  path: readonly string[];
}

export interface HierarchyNavigatorNode {
  id: string;
  label: string;
  parentId: string;
  path: readonly string[];
  children: HierarchyNavigatorNode[];
}

function scalarColumn(results: QueryResults, field: string): Scalar[] {
  const value = results[`field:${field}`];
  if (!Array.isArray(value)) {return value === undefined ? [] : [value as Scalar];}
  return value as Scalar[];
}

export function buildRecursiveHierarchy(
  navigator: HierarchyNavigatorSpec,
  results: QueryResults,
  sourceRoles: Record<string, string> = {},
): HierarchyNavigatorNode[] {
  const ids = scalarColumn(results, navigator.data.idField);
  const directLabels = scalarColumn(results, navigator.data.labelField);
  const fallbackLabelColumns = Object.values(sourceRoles)
    .filter((ref) => Array.isArray(results[ref]))
    .map((ref) => {
      const column = results[ref] as Scalar[];
      const sample = column.slice(0, 80);
      const score = sample.reduce<number>((total, value) => {
        if (typeof value !== 'string') {return total - 2;}
        if (!/[A-Za-z?-?]/.test(value)) {return total - 1;}
        if (value.includes('/') || value.includes(navigator.pathSeparator)) {return total - 3;}
        return total + 2;
      }, 0);
      return { column, score };
    })
    .sort((left, right) => right.score - left.score);
  const roleLabels = fallbackLabelColumns[0]?.score > 0 ? fallbackLabelColumns[0].column : [];
  const labels = directLabels.length > 0 ? directLabels : roleLabels;
  const parents = scalarColumn(results, navigator.data.parentField);
  const nodes = new Map<string, HierarchyNavigatorNode>();
  for (let index = 0; index < ids.length; index += 1) {
    const rawId = ids[index];
    if (rawId === null || rawId === undefined) {continue;}
    const id = String(rawId);
    const label = labels[index] === null || labels[index] === undefined ? id : String(labels[index]);
    const parentId = parents[index] === null || parents[index] === undefined ? '' : String(parents[index]);
    if (!nodes.has(id)) {nodes.set(id, { id, label, parentId, path: [label], children: [] });}
  }
  const roots: HierarchyNavigatorNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.parentId && node.parentId !== node.id ? nodes.get(node.parentId) : undefined;
    if (parent) {parent.children.push(node);}
    else {roots.push(node);}
  }
  const attachPaths = (node: HierarchyNavigatorNode, prefix: readonly string[]) => {
    node.path = [...prefix, node.label];
    node.children.forEach((child) => attachPaths(child, node.path));
  };
  roots.forEach((root) => attachPaths(root, []));
  return roots;
}

export function buildFlatHierarchy(
  navigator: HierarchyNavigatorSpec,
  results: QueryResults,
  sourceRoles: Record<string, string>,
): HierarchyNavigatorNode[] {
  const direct = navigator.data.pathFields.map((field) => scalarColumn(results, field));
  let paths: string[][] = [];
  if (direct.length > 0 && direct.every((column) => column.length > 0)) {
    const rowCount = Math.max(...direct.map((column) => column.length));
    for (let row = 0; row < rowCount; row += 1) {
      const path = direct.map((column) => column[row]).filter((value) => value !== null && value !== undefined).map(String);
      if (path.length) {paths.push(path);}
    }
  } else {
    const detailRef = sourceRoles.detail;
    const detail = detailRef ? results[detailRef] : undefined;
    if (Array.isArray(detail)) {
      paths = (detail as Scalar[])
        .filter((value): value is string | number | boolean => value !== null && value !== undefined)
        .map((value) => String(value).split(navigator.pathSeparator).filter(Boolean));
    }
  }
  const roots: HierarchyNavigatorNode[] = [];
  const byPath = new Map<string, HierarchyNavigatorNode>();
  for (const path of paths) {
    let parentChildren = roots;
    for (let depth = 0; depth < path.length; depth += 1) {
      const segment = path[depth];
      const key = path.slice(0, depth + 1).join(navigator.pathSeparator);
      let node = byPath.get(key);
      if (!node) {
        node = {
          children: [],
          id: key,
          label: segment,
          parentId: depth ? path.slice(0, depth).join(navigator.pathSeparator) : '',
          path: path.slice(0, depth + 1),
        };
        byPath.set(key, node);
        parentChildren.push(node);
      }
      parentChildren = node.children;
    }
  }
  return roots;
}

function matches(node: HierarchyNavigatorNode, needle: string): boolean {
  if (!needle) {return true;}
  return node.label.toLocaleLowerCase().includes(needle) || node.children.some((child) => matches(child, needle));
}

interface Props {
  navigator: HierarchyNavigatorSpec;
  results: QueryResults;
  sourceRoles: Record<string, string>;
  selectedId?: string;
  onSelect: (selection: HierarchyNavigatorSelection) => void;
}

export const HierarchyNavigatorVisual: React.FC<Props> = ({ navigator, results, sourceRoles, selectedId, onSelect }) => {
  const roots = useMemo(
    () => navigator.mode === 'recursive'
      ? buildRecursiveHierarchy(navigator, results, sourceRoles)
      : buildFlatHierarchy(navigator, results, sourceRoles),
    [navigator, results, sourceRoles],
  );
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(roots.map((node) => node.id)));
  const [search, setSearch] = useState('');
  const needle = search.trim().toLocaleLowerCase();

  const renderNode = (node: HierarchyNavigatorNode, depth: number): React.ReactNode => {
    if (!matches(node, needle)) {return null;}
    const hasChildren = node.children.length > 0;
    const isExpanded = needle ? true : expanded.has(node.id);
    return (
      <li key={node.id} role="treeitem" aria-expanded={hasChildren ? isExpanded : undefined}>
        <div className="dv-hierarchy-node" style={{ paddingInlineStart: `${depth * 16}px` }}>
          {hasChildren ? (
            <button
              aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${node.label}`}
              className="dv-hierarchy-toggle"
              onClick={() => setExpanded((current) => {
                const next = new Set(current);
                if (next.has(node.id)) {next.delete(node.id);} else {next.add(node.id);}
                return next;
              })}
              type="button"
            >{isExpanded ? '?' : '+'}</button>
          ) : <span className="dv-hierarchy-spacer" />}
          <button
            aria-current={selectedId === node.id ? 'true' : undefined}
            className="dv-hierarchy-label"
            onClick={() => onSelect({ id: node.id, label: node.label, path: node.path })}
            type="button"
          >{node.label}</button>
        </div>
        {hasChildren && isExpanded && <ul role="group">{node.children.map((child) => renderNode(child, depth + 1))}</ul>}
      </li>
    );
  };

  return (
    <section className="dv-hierarchy-navigator" data-hierarchy-mode={navigator.mode}>
      <header><strong>Hierarchy Navigator</strong></header>
      {navigator.searchable && (
        <input
          aria-label="Search hierarchy"
          onChange={(event) => setSearch(event.currentTarget.value)}
          placeholder="Search"
          type="search"
          value={search}
        />
      )}
      <ul aria-label="Hierarchy" className="dv-hierarchy-tree" role="tree">
        {roots.map((root) => renderNode(root, 0))}
      </ul>
    </section>
  );
};

