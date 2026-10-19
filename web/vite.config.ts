import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The client talks to the studio backend through a same-origin '/api' prefix.
// In dev, Vite proxies that prefix to the local backend so the session cookie
// and CSRF flow behave exactly as they do in a deployed single-origin setup.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8091',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
});
