import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The dev server proxies /api to the Flask API so the browser talks to a single
// origin — no CORS in development, and the bearer token never crosses origins.
// The Flask API listens on :5002 (see app/config.py).
//
// API_TARGET overrides that target. It is needed when Vite itself runs in a
// container: there, "localhost" is the container, not the host the API runs on.
//   API_TARGET=http://host.docker.internal:5002
const API_TARGET = process.env.API_TARGET || 'http://localhost:5002'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
