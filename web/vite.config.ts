import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react() as any,
    tailwindcss() as any,
  ],
  server: {
    port: 5173,
    proxy: {
      // Proxy API calls to FastAPI dev server in development
      '/api': 'http://localhost:8502',
      '/ws': {
        target: 'ws://localhost:8502',
        ws: true,
      },
      '/openapi.json': 'http://localhost:8502',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
})
