import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    vue(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  // 开发时由 Vite 转发到本机 admin，便于单独 `npm run dev` + IDE 调试（admin 默认 8011）
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_ADMIN_PROXY ?? 'http://127.0.0.1:8011',
        changeOrigin: true,
      },
      '/covers': {
        target: process.env.VITE_ADMIN_PROXY ?? 'http://127.0.0.1:8011',
        changeOrigin: true,
      },
    },
  },
})