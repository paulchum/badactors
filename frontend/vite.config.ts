import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/games": "http://127.0.0.1:8000"
    }
  },
  test: {
    environment: "node"
  }
});
