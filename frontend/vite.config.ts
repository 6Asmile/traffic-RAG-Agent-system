// vite.config.ts 完整修改

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // --- 核心修复：禁用代理缓存 ---
        ws: true, // 支持 websocket
        configure: (proxy, _options) => {
          proxy.on('proxyRes', (proxyRes, _req, _res) => {
            // 告诉代理服务器：这是一个流，不要缓存我！
            proxyRes.headers['x-accel-buffering'] = 'no';
          });
        }
      }
    }
  }
})