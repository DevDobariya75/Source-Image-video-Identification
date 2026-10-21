import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, calls to /api are forwarded to the FastAPI backend, so the
// browser sees one origin and no CORS setup is needed.
// Point it elsewhere with:  VITE_BACKEND=http://192.168.1.10:8000 npm run dev
const backend = process.env.VITE_BACKEND || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: backend, changeOrigin: true } },
  },
});
