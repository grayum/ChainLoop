/* Apply before the stylesheet/body paints. CSP permits this local static script. */
(function () {
  "use strict";
  const key = "chainloop-theme";
  const allowed = new Set(["system", "light", "dark"]);
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const root = document.documentElement;
  let preference = "system";
  try {
    const stored = localStorage.getItem(key);
    if (allowed.has(stored)) preference = stored;
  } catch (_) { /* Storage may be disabled; the control still works for this page. */ }

  function apply() {
    root.dataset.themePreference = preference;
    root.dataset.theme = preference === "system" ? (system.matches ? "dark" : "light") : preference;
    document.querySelectorAll('.theme-selector input[name="theme"]').forEach((input) => {
      input.checked = input.value === preference;
    });
  }

  apply();
  system.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === key || event.key === null) {
      preference = allowed.has(event.newValue) ? event.newValue : "system";
      apply();
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.querySelectorAll('.theme-selector input[name="theme"]').forEach((input) => {
      input.addEventListener("change", () => {
        if (!input.checked || !allowed.has(input.value)) return;
        preference = input.value;
        apply();
        try { localStorage.setItem(key, preference); } catch (_) { /* Keep the in-page choice. */ }
      });
    });
  });
})();
