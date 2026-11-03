import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const download = loadEnv(mode, process.cwd()).VITE_COMPANION_DOWNLOAD_URL?.trim();
  if (download) {
    const url = URL.canParse(download) ? new URL(download) : null;
    if (!url || url.protocol !== "https:" || url.username || url.password || url.hash) {
      throw new Error("VITE_COMPANION_DOWNLOAD_URL must be an absolute HTTPS URL without credentials or a fragment");
    }
  }
  return {
    plugins: [react()],
    base: "/app/",
    define: { __COMPANION_DOWNLOAD_URL__: JSON.stringify(download ?? "") },
    build: { outDir: "dist-hosted", sourcemap: false, rollupOptions: { input: "hosted.html" } },
  };
});
