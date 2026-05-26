import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import solidPlugin from "vite-plugin-solid";

const fastApiTarget = "http://localhost:8000";

export default defineConfig({
  base: "/dashboard-assets/",
  plugins: [solidPlugin(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../static",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          if (id.includes("solid-js") || id.includes("@solidjs/router")) {
            return "solid";
          }
          if (id.includes("marked") || id.includes("dompurify")) {
            return "markdown";
          }
          if (id.includes("diff2html")) {
            return "diff-view";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": fastApiTarget,
      "/dashboard-api": fastApiTarget,
      "/services": fastApiTarget,
      "/agents/cards": fastApiTarget,
      "/agents/reload": fastApiTarget,
      "/prompt-variables": fastApiTarget,
      "/providers/models": fastApiTarget,
      "/channels/pair": fastApiTarget,
      "/channels/identities": fastApiTarget,
      "/settings": fastApiTarget,
      "/scheduler/jobs": fastApiTarget,
      "/tasks/list": fastApiTarget,
      "/tasks": fastApiTarget,
      "/login": fastApiTarget,
      "/logout": fastApiTarget,
      "/change-password": fastApiTarget,
    },
  },
});
