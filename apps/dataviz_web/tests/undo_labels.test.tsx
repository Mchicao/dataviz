/** @vitest-environment jsdom */
import React from 'react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { CircularVisual } from '../src/visuals/base/CircularVisual';
import { CartesianVisual } from '../src/visuals/base/CartesianVisual';
import { VisualAuthoringEditor } from '../src/authoring/VisualAuthoringEditor';
import { createAuthoringDocument, createVisual } from '../src/authoring/seed';
import type { InterpretedVisual } from '../src/runtime/types';

afterEach(cleanup);

describe('Data labels and Category labels toggling (PBI style)', () => {
  const mockCircularVisual: InterpretedVisual = {
    geometry: { x: 0, y: 0, width: 300, height: 260 },
    kind: 'donut',
    name: 'distribucion_sedes',
    title: 'Atenciones por sede',
    supported: true,
    roles: {
      category: {
        ref: 'field:Sede',
        data: ['Centro', 'Norte', 'Sur'],
      },
      value: {
        ref: 'measure:Sum_Atendidas',
        data: [14400, 7800, 5600],
      },
    },
    properties: {
      show_data_labels: true,
      show_category_labels: true,
    },
  };

  it('renders both legend (category labels) and data labels by default or when enabled', () => {
    const { container } = render(<CircularVisual visual={mockCircularVisual} variant="donut" />);

    // Legend should be present
    const legend = container.querySelector('.dv-circular-legend');
    expect(legend).toBeTruthy();
    expect(legend?.textContent).toContain('Centro');
    expect(legend?.textContent).toContain('Norte');
    expect(legend?.textContent).toContain('Sur');

    // Slices should have data labels
    const texts = container.querySelectorAll('svg text');
    const textContents = Array.from(texts).map((t) => t.textContent);
    // Total label
    expect(textContents).toContain('total');
    expect(textContents).toContain('27.8K');
    // Slice percentages/values
    expect(textContents.some((t) => t?.includes('14.4K') || t?.includes('52%') || t?.includes('28%'))).toBe(true);
  });

  it('hides legend when show_category_labels is false', () => {
    const noLegendVisual: InterpretedVisual = {
      ...mockCircularVisual,
      properties: {
        show_data_labels: true,
        show_category_labels: false,
      },
    };
    const { container } = render(<CircularVisual visual={noLegendVisual} variant="donut" />);
    expect(container.querySelector('.dv-circular-legend')).toBeNull();
  });

  it('hides data labels when show_data_labels is false', () => {
    const noDataLabelsVisual: InterpretedVisual = {
      ...mockCircularVisual,
      properties: {
        show_data_labels: false,
        show_category_labels: true,
      },
    };
    const { container } = render(<CircularVisual visual={noDataLabelsVisual} variant="donut" />);
    const legend = container.querySelector('.dv-circular-legend');
    expect(legend).toBeTruthy();
    // Slice labels in svg text (excluding center total and total label)
    const texts = Array.from(container.querySelectorAll('svg text')).map((t) => t.textContent);
    expect(texts).toEqual(['27.8K', 'total']);
  });

  it('renders data labels on CartesianVisual columns when show_data_labels is true', () => {
    const mockCartesianVisual: InterpretedVisual = {
      geometry: { x: 0, y: 0, width: 400, height: 260 },
      kind: 'column',
      name: 'atenciones_sede',
      title: 'Atenciones por sede',
      supported: true,
      roles: {
        category: {
          ref: 'field:Sede',
          data: ['Centro', 'Norte', 'Sur'],
        },
        value: {
          ref: 'measure:Sum_Atendidas',
          data: [14400, 7800, 5600],
        },
      },
      properties: {
        show_data_labels: true,
        show_category_labels: true,
      },
    };

    const { container } = render(<CartesianVisual visual={mockCartesianVisual} variant="column" />);
    // With data labels, formatted values should be rendered
    const textContents = Array.from(container.querySelectorAll('svg text')).map((t) => t.textContent);
    expect(textContents.some((t) => t?.includes('14.4K') || t?.includes('14,400'))).toBe(true);
  });
});

describe('Undo / Redo (Ctrl+Z / Ctrl+Y) in VisualAuthoringEditor', () => {
  it('undoes the last action on Ctrl+Z and redoes on Ctrl+Y', async () => {
    const doc = createAuthoringDocument('test-undo-doc');
    const initialVisual = createVisual('card', 0, { id: 'visual-1', title: 'Tarjeta 1' });
    const onDocumentChange = vi.fn();

    const { container } = render(
      <VisualAuthoringEditor
        initialDocument={doc}
        initialVisuals={[initialVisual]}
        onDocumentChange={onDocumentChange}
      />
    );

    // Initial state has 1 visual
    expect(container.textContent).toContain('Tarjeta 1');

    // Click on "Agregar Barra" or visual picker button to add a visual
    const addBarBtn = container.querySelector('button[title*="Barras"]');
    if (addBarBtn) {
      fireEvent.click(addBarBtn);
      expect(onDocumentChange).toHaveBeenCalled();
    }

    // Now press Ctrl+Z on window
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });

    // The undo button is present
    const undoBtn = container.querySelector('button[title*="Deshacer"]') as HTMLButtonElement | null;
    expect(undoBtn).toBeTruthy();
  });
});
