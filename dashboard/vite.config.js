import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The FastAPI backend runs on :8000 (uvicorn dtwin.api.app:app --port 8000).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  // pre-bundle the 3D stack, so the first visit to the twin page does not
  // trigger a dependency re-optimisation and a full page reload
  optimizeDeps: { include: ["three", "@react-three/fiber", "@react-three/drei"] },
});
