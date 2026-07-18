import { useEffect } from "react";
import { startLegacyApp } from "./startLegacyApp";

let legacyStarted = false;

export function LegacyBootstrap() {
  useEffect(() => {
    if (legacyStarted) {
      return;
    }
    legacyStarted = true;
    startLegacyApp();
  }, []);

  return null;
}
