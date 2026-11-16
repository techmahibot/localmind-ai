import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, forward /api calls to the FastAPI backend.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
