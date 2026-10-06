import React from 'react';
import { RUNTIME_RESULTS_SCHEMA_VERSION } from '../../runtime/types';
import type { RenderPlan, RuntimeResults,InterpretedVisual } from '../../runtime/types';
import { RenderPlanInterpreter } from '../../runtime/interpreter';

import type { VisualProps } from './types';
import { CardVisual } from './CardVisual';
import { TableVisual } from './TableVisual';
import { CartesianVisual } from './CartesianVisual';
import { UnsupportedVisual } from './UnsupportedVisual';
import { SlicerVisual } from './SlicerVisual';
import { TextVisual } from './TextVisual';
import { CircularVisual } from './CircularVisual';
import { TreemapVisual } from './TreemapVisual';

import { MatrixVisual } from '../advanced/MatrixVisual';
import { MapVisualAdvanced } from '../advanced/MapVisualAdvanced';
import { CustomVisual } from '../custom/CustomVisual';

/**
 * Registry mapping a visual kind to its React component.
 *
 * Neutral intents are mapped to small native React renderers. Unknown future
 * intents still fall through to `UnsupportedVisual` explicitly.
 */
const REGISTRY: Record<string, React.FC<VisualProps>> = {
  area: (props) => <CartesianVisual {...props} variant="area" />,
  bar: (props) => <CartesianVisual {...props} variant="bar" />,
  card: CardVisual,
  column: (props) => <CartesianVisual {...props} variant="column" />,
  custom_visual: CustomVisual,
  donut: (props) => <CircularVisual {...props} variant="donut" />,
  kpi: CardVisual,
  line: (props) => <CartesianVisual {...props} variant="line" />,
  map: MapVisualAdvanced,
  matrix: MatrixVisual,
  pie: (props) => <CircularVisual {...props} variant="pie" />,
  scatter: (props) => <CartesianVisual {...props} variant="scatter" />,
  slicer: SlicerVisual,
  table: TableVisual,
  text_box: TextVisual,
  treemap: TreemapVisual,
};

/**
 * Render a single interpreted visual.
 *
 * Looks up the component by `visual.kind`; unknown/unimplemented kinds render
 * the explicit `UnsupportedVisual` fallback.
 */
export function renderVisual(
  visual: InterpretedVisual,
  onFilterChange?: VisualProps['onFilterChange'],
  filterValue?: string,
  onDataSelect?: VisualProps['onDataSelect'],
  selectedMark?: { fieldRef: string; value: string | number },
): React.ReactElement {
  const Comp = REGISTRY[visual.kind] ?? UnsupportedVisual;
  return <Comp visual={visual} filterValue={filterValue} onFilterChange={onFilterChange} onDataSelect={onDataSelect} selectedFieldRef={selectedMark?.fieldRef} selectedValue={selectedMark?.value} />;
}

/**
 * Interpret a full RenderPlan against query results and return the rendered
 * visual elements, one per visual in plan order.
 */
export function renderPlan(
  plan: RenderPlan,
  results: RuntimeResults = { schema_version: RUNTIME_RESULTS_SCHEMA_VERSION, visuals: {} },
  onFilterChange?: VisualProps['onFilterChange'],
  filterValues: Record<string, string> = {},
  onDataSelect?: VisualProps['onDataSelect'],
  dataSelectEnabledFor?: (visual: InterpretedVisual) => boolean,
  selectedMarkFor?: (visual: InterpretedVisual) => { fieldRef: string; value: string | number } | undefined,
): React.ReactElement[] {
  const interpreter = new RenderPlanInterpreter(results);
  return interpreter.interpret(plan).map((v) => (
    renderVisual(
      v,
      onFilterChange,
      filterValues[v.name],
      onDataSelect && (!dataSelectEnabledFor || dataSelectEnabledFor(v)) ? onDataSelect : undefined,
      selectedMarkFor?.(v),
    )
  ));
}
