import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { execSync } from "node:child_process";

// Build-Stempel: kurzer Git-SHA + Bauzeit, ins Bundle injiziert, damit das
// laufende Frontend seinen Code-Stand verraet (gegen das "ist mein Push schon
// drin?"-Raten). Faellt auf "unknown" zurueck, wenn git nicht verfuegbar ist.
function gitShortSha(): string {
  try {
    return execSync("git rev-parse --short HEAD").toString().trim() || "unknown";
  } catch {
    return "unknown";
  }
}
const BUILD_SHA = gitShortSha();
const BUILD_TIME = new Date().toISOString();

// The built frontend is served under /app by the FastAPI backend,
// so asset URLs must resolve against that base path.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  define: {
    __BUILD_SHA__: JSON.stringify(BUILD_SHA),
    __BUILD_TIME__: JSON.stringify(BUILD_TIME),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/ws": {
        target: "ws://127.0.0.1:8765",
        ws: true,
      },
      "/api": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
});
