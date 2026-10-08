import { resolve } from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Build estático de la landing para GitHub Pages (dist-pages/).
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist-pages',
    rollupOptions: {
      input: resolve(import.meta.dirname, 'pages.html'),
    },
  },
});
