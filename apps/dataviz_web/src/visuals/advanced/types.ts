/**
 * Advanced Consumption Types for DataVIZ Web Engine.
 *
 * Defines TypeScript contracts for corpus-backed consumption gaps:
 * 1. Matrix (pivot table with row/column headers & totals)
 * 2. Map Capability & Placeholder (geocoding verification & fallback)
 * 3. Parameters (parameter slicers & state management)
 * 4. Bookmarks (state snapshots & restoration)
 * 5. Page Tooltips (custom report page tooltips & hover previews)
 * 6. Accessibility (ARIA, screen reader, keyboard focus, high contrast)
 */

import type { FilterCondition } from '../../interactions/types';

// ==========================================
// 1. Matrix Visual Types
// ==========================================

export interface MatrixCell {
  rowKey: string;
  colKey: string;
  value: number | string | null;
  formattedValue: string;
}

export interface MatrixSubtotal {
  key: string;
  label: string;
  value: number;
}

export interface MatrixPivotResult {
  rowHeaders: string[];
  colHeaders: string[];
  cells: MatrixCell[][]; // rowHeaderIndex -> colHeaderIndex -> cell
  rowTotals: Record<string, number>;
  colTotals: Record<string, number>;
  grandTotal: number;
}

// ==========================================
// 2. Map Capability Types
// ==========================================

export type MapCapabilityStatus =
  | 'ready'
  | 'placeholder_missing_coords'
  | 'placeholder_unconfigured_tiles';

export interface MapDataPoint {
  latitude: number | null;
  longitude: number | null;
  locationName: string;
  value?: number | string | boolean | null;
}

export interface MapCapabilityReport {
  status: MapCapabilityStatus;
  hasLatitudeLongitude: boolean;
  hasLocationName: boolean;
  hasTileServer: boolean;
  pointCount: number;
  validPointCount: number;
  message: string;
  points: MapDataPoint[];
}

// ==========================================
// 3. Parameter Types
// ==========================================

export type ParameterType = 'choice' | 'range' | 'number' | 'text' | 'boolean';

export interface ParameterSpec {
  id: string;
  name: string;
  type: ParameterType;
  defaultValue: unknown;
  currentValue?: unknown;
  allowedValues?: unknown[];
  min?: number;
  max?: number;
  step?: number;
  targetField?: string;
}

export type ParameterStateMap = Record<string, unknown>;

// ==========================================
// 4. Bookmark Types
// ==========================================

export interface BookmarkSnapshot {
  id: string;
  name: string;
  targetPageId: string;
  activeFilters: Record<string, FilterCondition>;
  activeSelections: Record<string, unknown>;
  parameterStates: ParameterStateMap;
  createdAt: string;
  description?: string;
}

// ==========================================
// 5. Page Tooltip Types
// ==========================================

export interface TooltipPosition {
  x: number;
  y: number;
}

export interface PageTooltipState {
  tooltipId: string;
  sourceVisualId: string;
  targetPageId: string;
  isVisible: boolean;
  position: TooltipPosition;
  dataContext?: Record<string, unknown>;
}

// ==========================================
// 6. Accessibility Types
// ==========================================

export interface AdvancedAccessibilityConfig {
  altText: string;
  ariaLabel: string;
  tabIndex?: number;
  screenReaderSummary: string;
  highContrastAware?: boolean;
  role?: string;
}
