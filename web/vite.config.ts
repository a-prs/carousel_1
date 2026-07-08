import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// SPA is served under /carousel/app/ and built into server/static-web/.
export default defineConfig({
  plugins: [react()],
  base: "/carousel/app/",
  build: {
    outDir: "../server/static-web",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/carousel/api": "http://127.0.0.1:8055",
      "/carousel/health": "http://127.0.0.1:8055",
    },
  },
});
