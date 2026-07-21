import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // D2 ships a WASM engine + web worker; pre-bundling it breaks worker resolution.
  optimizeDeps: { exclude: ['@terrastruct/d2'] },
  worker: { format: 'es' },
  // Excalidraw reads process.env.IS_PREACT at runtime; Vite strips `process`
  // entirely from client bundles, which turns that read into a bare
  // ReferenceError at import time. Dedupe react/react-dom too: Excalidraw
  // bundles its own copy of React 19 hooks internally, and without dedupe a
  // second React instance loaded via a nested dependency breaks hooks
  // (invalid hook call) under Vite's module graph.
  define: { 'process.env.IS_PREACT': '"false"' },
  resolve: { dedupe: ['react', 'react-dom'] },
  server: {
    port: 8898,
    proxy: {
      '/api': 'http://127.0.0.1:8899',
      // The 144 MB vendored draw.io app is served by the backend, never
      // copied into web/public -- proxy it in dev so it never enters the
      // Vite module graph or dev-serve tree either.
      '/drawio': 'http://127.0.0.1:8899',
    },
  },
})
