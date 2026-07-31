import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";

// Build-Stempel beim Laden ins Console-Log — so siehst du auf einen Blick
// (DevTools), welchen Frontend-Stand das Panel gerade faehrt (Deploy-Gap).
console.info(
  `%cAI Furniture%c  build ${__BUILD_SHA__} · ${__BUILD_TIME__}`,
  "font-weight:bold",
  "color:#888",
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
