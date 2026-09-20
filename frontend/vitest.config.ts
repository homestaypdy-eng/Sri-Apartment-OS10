import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Kept separate from vite.config.ts (rather than adding a `test` block
// there) so the app's real build config is never touched by adding tests —
// this file only affects `npm test`, never `npm run dev`/`build`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
