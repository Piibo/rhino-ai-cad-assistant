/// <reference types="vite/client" />

// Build-Stempel, von vite.config.ts via `define` injiziert (Git-SHA + Bauzeit
// des laufenden Frontend-Bundles). Macht den Deploy-Gap ablesbar.
declare const __BUILD_SHA__: string;
declare const __BUILD_TIME__: string;
