import { defineConfig } from "vite";

// In dev the UI runs on :5173 and proxies API calls to the backend on :8080.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/styles": "http://localhost:8080",
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 2000,
  },
});
