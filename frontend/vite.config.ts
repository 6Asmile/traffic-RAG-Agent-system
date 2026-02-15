import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: '0.0.0.0', 
    // --- 核心修复：添加以下配置 ---
    allowedHosts: [
      'ragqa.asmi1e.us.ci', // 允许你的 Cloudflare 域名访问
       '192.168.1.6' // <--- 必须加上你电脑的局域网 IP
    ],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('proxyRes', (proxyRes, _req, _res) => {
            proxyRes.headers['x-accel-buffering'] = 'no';
          });
        }
      },
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
})