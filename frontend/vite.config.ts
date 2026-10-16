import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  server: { port: 5173, proxy: { '/api': { target: loadEnv(mode, '.', '').VITE_DEV_API_TARGET || 'http://127.0.0.1:8010', changeOrigin: true, timeout: 600000, proxyTimeout: 600000 } } },
  build: { sourcemap: false },
}))
