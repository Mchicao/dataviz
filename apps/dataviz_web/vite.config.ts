/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig(({ command }) => ({
  // Library builds already expose React as a peer global. Use the classic JSX
  // transform only there; Vitest and the dev server keep the automatic runtime
  // so TSX tests and modules do not need legacy React-in-scope imports.
  plugins: [react({ jsxRuntime: command === 'build' ? 'classic' : 'automatic' })],
  build: {
    lib: {
      entry: resolve(import.meta.dirname, 'src/viewer-main.tsx'),
      fileName: (format) => `dataviz-web.${format}.js`,
      name: 'DataVIZWeb',
    },
    rollupOptions: {
      external: ['react', 'react-dom', 'react-dom/client'],
      output: {
        globals: {
          react: 'React',
          'react-dom': 'ReactDOM',
          'react-dom/client': 'ReactDOM',
        },
      },
    },
  },
  server: {
    host: true,
    port: 3000,
    proxy: {
      // El orden importa: Vite toma la primera clave que matchee, y
      // '/api' también matchea '/api/assistant/*'. El prefijo largo va primero.
      '/api/assistant': 'http://127.0.0.1:8766',
      '/api': 'http://127.0.0.1:8765',
    },
  },
  test: {
    environment: 'node',
    globals: true,
    include: ['tests/**/*.test.{ts,tsx}', '../../tests/e2e/web/**/*.test.{ts,tsx}'],
    passWithNoTests: true,
  },
}));

