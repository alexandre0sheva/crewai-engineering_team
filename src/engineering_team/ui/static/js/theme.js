// Runs before first paint (a classic script, so the page never flashes the wrong theme).
try {
  const saved = localStorage.getItem("et-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch {
  // Storage can be blocked; the page then follows the system setting.
}
