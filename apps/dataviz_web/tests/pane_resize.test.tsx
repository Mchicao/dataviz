/** @vitest-environment jsdom */
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { VisualAuthoringEditor, createAuthoringDocument } from '../src/authoring';

afterEach(() => { cleanup(); window.localStorage.clear(); vi.unstubAllGlobals(); });

it('expands every right-hand panel when its left edge moves left and shrinks it when moved right', () => {
  vi.stubGlobal('PointerEvent', MouseEvent);
  render(<VisualAuthoringEditor initialDocument={createAuthoringDocument()} />);
  const panes = document.querySelector<HTMLElement>('.dv-right-panes')!;
  const keys = ['visual', 'filters', 'data', 'agent'];
  const resizers = [...document.querySelectorAll<HTMLElement>('.dv-pane-resizer')];
  expect(resizers).toHaveLength(keys.length);
  resizers.forEach((resizer, index) => {
    const property = `--w-${keys[index]}`;
    const initial = Number.parseInt(panes.style.getPropertyValue(property), 10);
    fireEvent.pointerDown(resizer, { clientX: 300 });
    fireEvent.pointerMove(window, { clientX: 260 });
    fireEvent.pointerUp(window);
    expect(panes.style.getPropertyValue(property)).toBe(`${initial + 40}px`);
    fireEvent.pointerDown(resizer, { clientX: 260 });
    fireEvent.pointerMove(window, { clientX: 300 });
    fireEvent.pointerUp(window);
    expect(panes.style.getPropertyValue(property)).toBe(`${initial}px`);
  });
});
