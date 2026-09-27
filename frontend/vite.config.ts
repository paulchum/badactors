import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/worlds": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/leaderboard": "http://127.0.0.1:8000",
      "/games": "http://127.0.0.1:8000",
      "/seasons": "http://127.0.0.1:8000"
    }
  },
  test: {
    environment: "node"
  }
});
