import React from 'react';
import type { Scalar } from '../../runtime/types';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';

/** Treemap compacto y determinista para el intent neutral `treemap`. */
export const TreemapVisual: React.FC<VisualProps> = ({ visual }) => {
  const title = displayTitle(visual.title, visual.name);
  const preferredLabelNames = ['label', 'category', 'x_axis', 'color'];
  const labelCandidates = Object.entries(visual.roles)
    .filter(([, role]) => !role.ref.startsWith('measure:'))
    .sort(([left], [right]) => {
      const leftRank = preferredLabelNames.indexOf(left);
      const rightRank = preferredLabelNames.indexOf(right);
      return (leftRank === -1 ? preferredLabelNames.length : leftRank)
        - (rightRank === -1 ? preferredLabelNames.length : rightRank);
    });
  const labelRole = labelCandidates
    .map(([, role]) => role)
    .find((role) => columnForRef(role.ref, role.data).some((value) => value !== null && String(value).trim() !== ''));
  const sizeRole = getRole(visual, ['size', 'value', 'y_axis', 'x_axis']);
  const labels = labelRole ? columnForRef(labelRole.ref, labelRole.data) : [];
  const sizes = sizeRole ? columnForRef(sizeRole.ref, sizeRole.data) : [];
  const grouped = new Map<string, { label: Scalar; value: number }>();
  sizes.forEach((value, index) => {
    const numericValue = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0;
    if (!numericValue) {return;}
    const label = labels[index] ?? `Item ${index + 1}`;
    const key = String(label);
    const current = grouped.get(key);
    grouped.set(key, current
      ? { label: current.label, value: current.value + numericValue }
      : { label, value: numericValue });
  });
  const items = [...grouped.values()].sort((left, right) => right.value - left.value);
  const total = items.reduce((sum, item) => sum + item.value, 0);

  if (!total) {
    return <section className="dv-treemap dv-treemap--empty">{title}: no data</section>;
  }

  const visibleItems = items.slice(0, 12);
  const hiddenItems = items.slice(12);
  const otherValue = hiddenItems.reduce((sum, item) => sum + item.value, 0);
  const displayItems = otherValue > 0
    ? [...visibleItems, { label: `Other ${hiddenItems.length} groups`, value: otherValue }]
    : visibleItems;

  return (
    <figure className="dv-treemap" aria-label={`${title}: ${items.length} groups`} style={frameStyle}>
      <figcaption className="dv-treemap__caption">
        <span>{title}</span>
        <span className="dv-treemap__summary">Top {Math.min(12, items.length)} of {items.length} groups</span>
      </figcaption>
      <div
        className="dv-treemap__grid"
        style={{ gridTemplateColumns: displayItems.length === 1 ? '1fr' : displayItems.length <= 4 ? 'repeat(2, minmax(0, 1fr))' : undefined }}
      >
        {displayItems.map((item, index) => (
          <div
            className={`dv-treemap__tile${index === 0 && displayItems.length >= 4 ? ' dv-treemap__tile--lead' : ''}`}
            key={`${String(item.label)}-${index}`}
            title={`${String(item.label)}: ${formatScalar(item.value)}`}
            style={{ background: COLORS[index % COLORS.length] }}
          >
            <strong>{String(item.label as Scalar)}</strong>
            <span>{formatScalar(item.value)}</span>
          </div>
        ))}
      </div>
    </figure>
  );
};

const COLORS = ['#123b63', '#1d5d8f', '#2877ad', '#3d8fbd', '#5aa6c8', '#7ebbd1'];
const frameStyle: React.CSSProperties = {
  border: 0,
  borderRadius: 0,
  boxSizing: 'border-box',
  display: 'block',
  height: '100%',
  margin: 0,
  padding: 0,
  width: '100%',
};
