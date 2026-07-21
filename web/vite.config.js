import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    fs: {
      allow: [fileURLToPath(new URL('..', import.meta.url))],
    },
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/healthz': 'http://127.0.0.1:8787',
      '/static': 'http://127.0.0.1:8787',
    },
  },
})
