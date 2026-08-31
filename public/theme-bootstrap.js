(() => {
  const root = document.documentElement;
  let preference = "light";

  let stored = null;
  try {
    stored = localStorage.getItem("makiiing-v2-settings");
  } catch {
    stored = null;
  }
  if (stored) {
    preference = "light";
    try {
      const parsed = JSON.parse(stored);
      if (parsed && typeof parsed === "object") {
        if (parsed.theme === "light" || parsed.theme === "dark" || parsed.theme === "system") {
          preference = parsed.theme;
        }
      }
    } catch {
      preference = "light";
    }
  }

  const prefersDark = matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = preference === "system" ? (prefersDark ? "dark" : "light") : preference;
  root.setAttribute("data-theme-preference", preference);
  root.setAttribute("data-theme", resolved);
})();
