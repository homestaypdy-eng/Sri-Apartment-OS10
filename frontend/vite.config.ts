import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'Sri Apartment OS',
        short_name: 'Sri Apartment OS',
        description: 'Sri Apartment Homestay — operations, bookings, and guest portal',
        theme_color: '#0f172a',
        background_color: '#ffffff',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: '/pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          { src: '/pwa-maskable-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Cache the app shell for offline install/launch. API calls are
        // NOT cached (network-first is the default for anything not
        // matched below) — a booking system showing stale cached data
        // instead of a network error would be actively misleading, so
        // this deliberately only makes the app SHELL installable/offline-
        // launchable, not the live data within it.
        globPatterns: ['**/*.{js,css,html,svg,png,ico}'],
        navigateFallback: '/index.html',
      },
    }),
  ],
  server: {
    port: 5173,
  },
})
