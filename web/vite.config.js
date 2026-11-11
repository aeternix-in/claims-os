import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
// Pages: / = the product (the CTO's UI on real data: engine + Supabase), /demo/ = the CTO's demo exactly (sample data, nothing saved
// to the server), /classic/ = the earlier screens, /live/ = old address, forwards to /.
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 2000,
    rollupOptions: { input: { main: resolve(__dirname, 'index.html'), demo: resolve(__dirname, 'demo/index.html'), live: resolve(__dirname, 'live/index.html'), classic: resolve(__dirname, 'classic/index.html') } },
  },
});
