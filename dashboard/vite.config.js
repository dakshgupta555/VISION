import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The FastAPI backend runs on :8000 (uvicorn dtwin.api.app:app --port 8000).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // DTWIN_API points a second copy at another backend, e.g. a worktree on :8765
  server: { port: 5173, proxy: { "/api": process.env.DTWIN_API || "http://127.0.0.1:8000" } },
  // pre-bundle the 3D stack, so the first visit to the twin page does not
  // trigger a dependency re-optimisation and a full page reload
  optimizeDeps: { include: ["three", "@react-three/fiber", "@react-three/drei"] },
});
