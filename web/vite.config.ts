import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The built page is served by the simulator backend (python -m groupflow demo) from web/dist.
// During UI work, `npm run dev` serves it on 5173 and forwards /api to the simulator on 8788.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8788', changeOrigin: false },
      '/human': { target: 'http://127.0.0.1:8788', changeOrigin: false },
    },
  },
});
