import type { InterpretedVisual } from '../../runtime/types';

/** Common props for every base visual component. */
export interface VisualProps {
  visual: InterpretedVisual;
  filterValue?: string;
  onFilterChange?: (visual: InterpretedVisual, value: string) => void;
  onDataSelect?: (visual: InterpretedVisual, fieldRef: string, value: string | number, rowIndex?: number) => void;
  selectedFieldRef?: string;
  selectedValue?: string | number;
}
