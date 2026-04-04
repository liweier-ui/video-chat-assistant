import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 监听所有网卡：隧道公网访问时也需要 proxy 能接收外部请求
    host: '0.0.0.0',
    port: 5173,
    // 固定 5173：避免换端口后用户仍打开旧地址导致「打不开」；若占用请先运行 STOP.bat 或关掉旧 Vite
    strictPort: true,
    // 启动成功后自动打开浏览器（双击 bat 时不用再猜地址）
    open: false,
    // 开发时把 API 转到本机 FastAPI，浏览器只访问 5173，不再触发跨域/CORS 问题
    proxy: {
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/upload': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/chat': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/cv': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/debug': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/videos': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
