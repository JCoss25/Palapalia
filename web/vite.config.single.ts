/**
 * Build de un solo archivo, para abrir con doble clic (`file://`) sin servidor.
 *
 * - `format: 'iife'`: los módulos ES están bloqueados en `file://` por CORS.
 * - `base: './'`: rutas relativas para scripts, estilos e imágenes.
 * - `assetsInlineLimit`: todo lo pequeño se incrusta; las fotos se quedan en `./images`.
 *
 * Después del build, `scripts/inline.mjs` mete JS y CSS dentro del HTML.
 */

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist-single',
    emptyOutDir: true,
    assetsInlineLimit: 100 * 1024 * 1024,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        format: 'iife',
        inlineDynamicImports: true,
        entryFileNames: 'app.js',
        assetFileNames: '[name][extname]',
      },
    },
  },
});
