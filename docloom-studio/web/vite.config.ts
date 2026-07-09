import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // D2 ships a WASM engine + web worker; pre-bundling it breaks worker resolution.
  optimizeDeps: { exclude: ['@terrastruct/d2'] },
  worker: { format: 'es' },
  server: {
    port: 8898,
    proxy: {
      '/api': 'http://127.0.0.1:8899',
    },
  },
})
