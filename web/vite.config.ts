import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8780', changeOrigin: true, configure(proxy) { proxy.on('proxyReq', (request, incoming) => { if (incoming.headers.origin === 'http://127.0.0.1:5173') request.setHeader('Origin','http://127.0.0.1:8780') }) } } },
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
})
