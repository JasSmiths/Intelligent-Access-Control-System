import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Keep e2e traffic local. API calls are fulfilled by Playwright and cannot fall
// through Vite's development proxy to the backend.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true
  }
});
