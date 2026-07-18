import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// 디자인 시스템 (REDESIGN_SPEC_V2_2 "Trust Desk"):
// variables → base → components → shell → 화면별 → 테마 오버라이드.
import "./styles/variables.css";
import "./styles/base.css";
import "./styles/components.css";
import "./styles/shell.css";
import "./styles/screen-canvas.css";
import "./styles/screen-settings.css";
import "./styles/themes.css";

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("Nothing React root element was not found.");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
