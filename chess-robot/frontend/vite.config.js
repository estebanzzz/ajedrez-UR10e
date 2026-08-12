import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// La UI se sirve desde el backend en /ui (kiosk). En desarrollo, el proxy
// apunta al backend local para /api y /ws.
export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
