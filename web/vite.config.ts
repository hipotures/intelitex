/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { configDefaults } from 'vitest/config'

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
  // Vitest runs unit tests from src/ only. tests/*.test.mjs are Playwright integration tests for node:test: each one
  // starts a fixture server and a browser, so they must run one at a time (npm run test:browser). Discovering them
  // here ran all of them at once and OOM-killed the VM twice.
  test: {
    include: ['src/**/*.{test,spec}.?(c|m)[jt]s?(x)'],
    exclude: [...configDefaults.exclude, 'tests/**'],
  },
})
