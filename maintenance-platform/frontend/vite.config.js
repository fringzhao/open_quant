import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  cacheDir: process.env.VITE_CACHE_DIR || '.vite-cache-v2',
  server: {
    host: '127.0.0.1',
    port: 8071,
    strictPort: true,
    proxy: {
      '/api': process.env.VITE_API_TARGET || 'http://127.0.0.1:8072',
    },
  },
})
