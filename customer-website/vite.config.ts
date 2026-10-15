/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config'
import { loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { imagetools } from 'vite-imagetools'
import { assertIsolatedLocalTarget, devApiGuard, devApiProxy } from './vite/devApiProxy.ts'

// https://vite.dev/config/
export default defineConfig(({ command, mode, isPreview }) => {
  // Dev server only: DEV_API_PROXY_TARGET comes from .env.development.local,
  // which neither vitest nor `vite build` loads; `vite preview` would reuse
  // server.proxy, hence the explicit isPreview check.
  const devEnv: Record<string, string> = command === 'serve' && !isPreview ? loadEnv(mode, process.cwd(), '') : {}
  const isolatedTarget = devEnv.DEV_ISOLATED_API_PROXY_TARGET
  if (isolatedTarget) assertIsolatedLocalTarget(isolatedTarget)
  const proxyTarget = isolatedTarget || devEnv.DEV_API_PROXY_TARGET
  return {
  plugins: [react(), tailwindcss(), imagetools(), ...(proxyTarget ? [devApiGuard(isolatedTarget)] : [])],
  // Local auth always uses same-origin /api, never a direct remote API URL.
  ...(isolatedTarget ? { define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify(''), 'import.meta.env.VITE_DEV_READ_ONLY_API': JSON.stringify('false') } } : {}),
  server: {
    ...(isolatedTarget ? { host: '127.0.0.1' } : {}),
    port: 5175,
    strictPort: true,
    ...(proxyTarget ? { proxy: devApiProxy(proxyTarget, !!isolatedTarget) } : {}),
  },
  build: {
    // Route-level code splitting is handled by React.lazy() in src/App.tsx;
    // this just keeps vendor deps in their own cacheable chunk.
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) return 'vendor'
        },
      },
    },
  },
  test: {
    // Keep mocked photo URL tests independent of uncommitted .env.local files.
    env: { VITE_API_BASE_URL: 'http://localhost:8000' },
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
  }
})
