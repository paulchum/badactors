import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// A relative base, so the built site runs from any folder: GitHub Pages under
// /<repo>/, or `npm run preview` at the root.
export default defineConfig({
  base: "./",
  plugins: [react()],
  test: {
    environment: "node",
    globalSetup: ["./vitest.feeds.ts", "./vitest.bench.ts"],
  },
});
