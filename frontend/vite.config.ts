import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks to /api/*; Vite forwards it to the backend and strips the prefix.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
