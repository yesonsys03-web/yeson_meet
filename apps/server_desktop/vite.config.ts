// === ANCHOR: VITE_CONFIG_START ===
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://v2.tauri.app/start/frontend/vite/
// Port 5274 keeps the server-console dev server clear of the client app (5174).
export default defineConfig(async () => ({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5274,
    strictPort: true,
    host: false,
    hmr: { protocol: "ws", host: "localhost", port: 5275 },
    watch: { ignored: ["**/src-tauri/**"] },
  },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  build: {
    target: ["es2021", "chrome105", "safari13"],
    minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
  },
}));
// === ANCHOR: VITE_CONFIG_END ===
