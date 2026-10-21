import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Puerto propio: en esta máquina hay otros proyectos usando 5173 y 4173.
    host: '127.0.0.1',
    port: 5177,
    strictPort: true,
    proxy: {
      // La app web habla con la API real cuando está arriba; si no, usa los datos de demostración.
      '/api': {
        target: process.env.API_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  // `vite preview` no hereda el proxy del servidor de desarrollo: se declara otra vez para que el
  // build de producción también pueda hablar con la API en local.
  preview: {
    host: '127.0.0.1',
    port: 4177,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.API_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
  },
});
