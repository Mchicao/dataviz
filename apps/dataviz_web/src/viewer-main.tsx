import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './app';
import { optionalQueryValue } from './entryMode';

/**
 * Entrada embebible del runtime de consumo.
 *
 * Se mantiene separada del Studio para que quienes sólo reproducen un
 * RenderPlan no descarguen el editor, su historial ni el planner local.
 */
const rootElement = document.querySelector('#root');
if (!rootElement) {throw new Error('DataVIZ viewer requires a #root element');}
const query = new URL(window.location.href).searchParams;
ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <App
      endpoint={optionalQueryValue(query, 'endpoint')}
      versionId={optionalQueryValue(query, 'version')}
    />
  </React.StrictMode>,
);
