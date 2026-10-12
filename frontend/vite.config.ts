import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The dev server proxies the API to the local backend so the browser only ever
// talks to one origin, and the built bundle is served by the backend itself.
const BACKEND = process.env.AEM_BACKEND_URL ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/health': { target: BACKEND, changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
