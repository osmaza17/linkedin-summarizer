import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// La app se sirve desde el backend Flask de LinkedIn (puerto 3002) en la raíz (/),
// con assets relativos. En desarrollo (npm run dev) se redirige /api y /reports al
// backend.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 3003,
    proxy: {
      "/api": "http://127.0.0.1:3002",
      "/reports": "http://127.0.0.1:3002",
    },
  },
});
