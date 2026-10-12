import { defineConfig } from 'vite'
import path from 'path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev server binds to localhost only by default. To expose it (e.g. on a
// tailnet), set VITE_HOST=0.0.0.0 (or an interface IP) and list the hostnames
// you'll browse from in VITE_ALLOWED_HOSTS (comma-separated, or "all").
const host = process.env.VITE_HOST || 'localhost'
const allowedHostsEnv = (process.env.VITE_ALLOWED_HOSTS || '').trim()
const allowedHosts: true | string[] =
  allowedHostsEnv === 'all'
    ? true
    : allowedHostsEnv
        .split(',')
        .map((h) => h.trim())
        .filter(Boolean)

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host,
    allowedHosts,
    proxy: {
      '/api': {
        target: process.env.API_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
