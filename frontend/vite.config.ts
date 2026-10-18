declare const process: { env: Record<string, string | undefined> }

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前后端通过 Vite 代理连在一起，开发期完全不涉及 CORS：
//   /api/* -> http://127.0.0.1:8000
//   /ws    -> ws://127.0.0.1:8000（WebSocket upgrade 也代理）
// 这样前端代码里只写相对路径，部署时也可以让 FastAPI 直接托管 dist。
export default defineConfig({
  // Toy 静态部署需要相对路径：构建时 VITE_BASE=./
  base: process.env.VITE_BASE ?? '/',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
