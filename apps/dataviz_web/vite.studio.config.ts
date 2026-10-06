import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

/** Construye el Studio completo sin inflar la librería embebible del viewer. */
export default defineConfig({
  base: './',
  build: {
    emptyOutDir: false,
    outDir: resolve(import.meta.dirname, 'dist/studio'),
    rollupOptions: {
      output: {
        assetFileNames: 'dataviz-studio[extname]',
        chunkFileNames: 'chunks/[name]-[hash].js',
        entryFileNames: 'dataviz-studio.js',
      },
    },
  },
  plugins: [react()],
});
