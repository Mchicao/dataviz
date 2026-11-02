/** @vitest-environment jsdom */
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { VisualAuthoringEditor, createAuthoringDocument } from '../src/authoring';

afterEach(() => { cleanup(); window.localStorage.clear(); });

it('renders unified top header with DataVIZ brand link, language toggle, and navigation buttons in English by default, and switches to Spanish on click', () => {
  render(<VisualAuthoringEditor initialDocument={createAuthoringDocument()} />);
  const brand = document.querySelector('.dv-brand-title');
  expect(brand).not.toBeNull();
  expect(brand?.textContent).toBe('DataVIZ');

  // DV square must NOT exist
  const dvSquare = [...document.querySelectorAll('.dv-studio-brand span')].find(el => el.textContent === 'DV');
  expect(dvSquare).toBeUndefined();

  // Navigation buttons must exist in English by default
  const navLinks = document.querySelector('.dv-nav-links');
  expect(navLinks).not.toBeNull();
  expect(navLinks?.textContent).toContain('Create dashboards');
  expect(navLinks?.textContent).toContain('View dashboard');
  expect(navLinks?.textContent).toContain('Agents');

  // Language toggle should exist
  const esBtn = [...document.querySelectorAll('.demo-lang-toggle button')].find(b => b.textContent === 'ES');
  expect(esBtn).not.toBeNull();

  // Switch to Spanish
  fireEvent.click(esBtn!);
  expect(navLinks?.textContent).toContain('Crear dashboards');
  expect(navLinks?.textContent).toContain('Ver dashboard');
  expect(navLinks?.textContent).toContain('Agentes');
});

it('minimizes the assistant panel when clicking its header banner and expands back on strip click', () => {
  render(<VisualAuthoringEditor initialDocument={createAuthoringDocument()} />);

  // Initially assistant panel should be visible (Assistant in English by default)
  const assistantHeading = document.querySelector('.dv-agent-pane .dv-pane-header');
  expect(assistantHeading).not.toBeNull();
  expect(assistantHeading?.textContent).toContain('Assistant');

  // Click on the assistant banner to minimize
  fireEvent.click(assistantHeading!);

  // Now the side strip should be visible
  const strip = document.querySelector('.dv-side-strip[aria-label*="assistant"], .dv-side-strip[aria-label*="asistente"]');
  expect(strip).not.toBeNull();
  expect(strip?.textContent).toContain('Assistant');

  // And the assistant pane should be collapsed (not rendered)
  expect(document.querySelector('.dv-agent-pane')).toBeNull();

  // Click the side strip to expand again
  fireEvent.click(strip!);

  // Assistant pane is restored
  expect(document.querySelector('.dv-agent-pane')).not.toBeNull();
});
