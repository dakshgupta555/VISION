import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// GridTwin network dashboard: its own app on :5180, its own backend on :8001
// (uvicorn dtwin_network.app:app --port 8001). Nothing is imported from
// ../dashboard; shared styles were copied, so changes cannot leak between apps.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5180, strictPort: true, proxy: { "/api": process.env.NET_API || "http://127.0.0.1:8001" } },
});
