import React from 'react';
import type { ParameterSpec } from './types';
import type { FilterCondition } from '../../interactions/types';

export interface ParameterControlProps {
  parameter: ParameterSpec;
  value?: unknown;
  onChange: (parameterId: string, newValue: unknown) => void;
  className?: string;
}

/**
 * Derives an active FilterCondition from a parameter state update.
 */
export function deriveParameterFilter(
  param: ParameterSpec,
  val: unknown,
): FilterCondition {
  return {
    filter_id: `param_filter_${param.id}`,
    is_interactive_slicer: true,
    name: param.name,
    operator: 'equals',
    scope: 'workbook',
    target_field: param.targetField || param.id,
    values: [val],
  };
}

/**
 * Parameter Control Slicer for DataVIZ Web Engine.
 *
 * Fulfills Corpus Gap 3: Parameter controls and parameter state management.
 */
export const ParameterControl: React.FC<ParameterControlProps> = ({
  parameter,
  value,
  onChange,
  className = '',
}) => {
  const currentValue = value ?? parameter.currentValue ?? parameter.defaultValue;
  const inputId = `param_ctrl_${parameter.id}`;

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    let newVal: unknown = e.target.value;
    if (parameter.type === 'number' || parameter.type === 'range') {
      newVal = Number(e.target.value);
    } else if (parameter.type === 'boolean') {
      newVal = (e.target as HTMLInputElement).checked;
    }
    onChange(parameter.id, newVal);
  };

  return (
    <div
      className={`dataviz-parameter-control ${className}`}
      role="group"
      aria-label={`Parameter control for ${parameter.name}`}
      style={{
        backgroundColor: '#FFFFFF',
        border: '1px solid #E2E8F0',
        borderRadius: '6px',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        margin: '8px 0',
        padding: '8px',
      }}
    >
      <label
        htmlFor={inputId}
        style={{ color: '#334155', fontSize: '13px', fontWeight: 600 }}
      >
        {parameter.name}
      </label>

      {parameter.type === 'choice' && parameter.allowedValues && (
        <select
          id={inputId}
          value={String(currentValue)}
          onChange={handleChange}
          aria-label={parameter.name}
          style={{ border: '1px solid #CBD5E1', borderRadius: '4px', padding: '6px 10px' }}
        >
          {parameter.allowedValues.map((val, idx) => (
            <option key={idx} value={String(val)}>
              {String(val)}
            </option>
          ))}
        </select>
      )}

      {parameter.type === 'range' && (
        <div style={{ alignItems: 'center', display: 'flex', gap: '8px' }}>
          <input
            id={inputId}
            type="range"
            min={parameter.min ?? 0}
            max={parameter.max ?? 100}
            step={parameter.step ?? 1}
            value={Number(currentValue)}
            onChange={handleChange}
            aria-label={parameter.name}
            aria-valuemin={parameter.min ?? 0}
            aria-valuemax={parameter.max ?? 100}
            aria-valuenow={Number(currentValue)}
            style={{ flex: 1 }}
          />
          <span style={{ fontSize: '13px', fontWeight: 500, minWidth: '32px' }}>
            {String(currentValue)}
          </span>
        </div>
      )}

      {parameter.type === 'number' && (
        <input
          id={inputId}
          type="number"
          min={parameter.min}
          max={parameter.max}
          step={parameter.step ?? 1}
          value={Number(currentValue)}
          onChange={handleChange}
          aria-label={parameter.name}
          style={{ border: '1px solid #CBD5E1', borderRadius: '4px', padding: '6px 10px' }}
        />
      )}

      {parameter.type === 'text' && (
        <input
          id={inputId}
          type="text"
          value={String(currentValue)}
          onChange={handleChange}
          aria-label={parameter.name}
          style={{ border: '1px solid #CBD5E1', borderRadius: '4px', padding: '6px 10px' }}
        />
      )}

      {parameter.type === 'boolean' && (
        <div style={{ alignItems: 'center', display: 'flex', gap: '8px' }}>
          <input
            id={inputId}
            type="checkbox"
            checked={Boolean(currentValue)}
            onChange={handleChange}
            aria-label={parameter.name}
          />
          <span style={{ color: '#475569', fontSize: '13px' }}>
            {currentValue ? 'Enabled' : 'Disabled'}
          </span>
        </div>
      )}
    </div>
  );
};
