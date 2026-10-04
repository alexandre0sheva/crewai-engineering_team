// One colour per teammate, the same everywhere (avatar, card edge, swimlane, feed). The hue comes
// from the name, so it is stable between visits; CSS turns it into a colour that passes AA
// contrast in both themes (app/dashboard.css: .avatar).

import { h } from "../dom.js";

const HUES = [14, 200, 142, 276, 38, 332, 172, 84, 232, 354];

export function hueOf(name) {
  let sum = 0;
  for (const ch of name || "?") sum = (sum * 31 + ch.charCodeAt(0)) >>> 0;
  return HUES[sum % HUES.length];
}

export function initials(name) {
  const parts = String(name || "?").split(/[_\s-]+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] || "?").slice(0, 2);
  return letters.toUpperCase();
}

export const label = (name) => String(name || "unassigned").replace(/_/g, " ");

export function avatar(name, { large = false } = {}) {
  const el = h("span", { class: `avatar${large ? " large" : ""}`, title: label(name), "aria-hidden": "true" }, name ? initials(name) : "·");
  el.style.setProperty("--h", String(hueOf(name)));
  return el;
}

export function tint(el, name) {
  el.style.setProperty("--h", String(hueOf(name)));
  return el;
}
