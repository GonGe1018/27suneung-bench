import { useEffect, useLayoutEffect, useState } from "react";

export type Theme = "light" | "dark";
const storageKey = "csat-theme";

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Storage can be unavailable in a private or embedded browser.
  }
  return "dark";
}

export function useTheme() {
  const [theme, setTheme] = useState(readTheme);

  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    const toolbar = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (toolbar) toolbar.content = getComputedStyle(root).getPropertyValue("--surface").trim();
  }, [theme]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) setTheme(readTheme());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem(storageKey, next);
    } catch {
      // The selection still applies to the open page.
    }
  }

  return { theme, toggleTheme };
}
