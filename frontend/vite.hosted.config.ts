import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: { outDir: "dist-hosted", sourcemap: false, rollupOptions: { input: "hosted.html" } },
});
