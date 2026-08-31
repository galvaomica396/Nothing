import { useEffect } from "react";
import { startApplicationComposition } from "./compositionRoot";

export function AppCompositionRoot() {
  useEffect(() => {
    return startApplicationComposition();
  }, []);

  return null;
}
