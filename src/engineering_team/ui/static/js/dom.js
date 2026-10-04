// Tiny DOM helpers. Everything goes in as text nodes or attributes — never as HTML — so text that
// came from a run (agent output, repository files, logs) cannot become markup.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "value" || key === "checked" || key === "disabled" || key === "selected") el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

const ICONS = {
  check: "M4 12.5 9.5 18 20 6.5",
  x: "M6 6l12 12M18 6 6 18",
  alert: "M12 8v5m0 3.5v.01M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  clock: "M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z",
  spinner: "M12 3a9 9 0 1 0 9 9",
  play: "M7 4.5v15l12-7.5-12-7.5Z",
  pause: "M8 5v14M16 5v14",
  stop: "M6 6h12v12H6Z",
  upload: "M12 16V4m0 0L7 9m5-5 5 5M4 20h16",
  download: "M12 4v12m0 0 5-5m-5 5-5-5M4 20h16",
  file: "M7 3h7l5 5v13H7ZM14 3v5h5",
  folder: "M3 6.5A1.5 1.5 0 0 1 4.5 5H10l2 2.5h7.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5Z",
  branch: "M7 4v10m0 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm10-6a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm0 0c0 4-10 2-10 6",
  sun: "M12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 5 5",
  copy: "M9 9h11v11H9ZM5 15V4h11",
  circle: "M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z",
  ban: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM5.6 5.6l12.8 12.8",
  refresh: "M20 11a8 8 0 0 0-14.5-4M4 4v4h4M4 13a8 8 0 0 0 14.5 4M20 20v-4h-4",
  chevron: "M9 6l6 6-6 6",
  plus: "M12 5v14M5 12h14",
  layers: "M12 3 3 8l9 5 9-5-9-5ZM3 13l9 5 9-5M3 17.5l9 5 9-5",
  box: "M3 7.5 12 3l9 4.5v9L12 21l-9-4.5ZM3 7.5 12 12m0 0 9-4.5M12 12v9",
  list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  wrench: "M14.7 6.3a4 4 0 0 0 5 5L21 17l-4 4-5.7-1.3a4 4 0 0 0-5-5L3 9l4-4Z",
  flag: "M5 21V4m0 1h13l-2.5 4L18 13H5",
  shield: "M12 3 4.5 6v6c0 4.5 3 7.7 7.5 9 4.5-1.300 7.500-4.500 7.500-9V6ZM8.500 12l2.500 2.500 4.500-5",
  message: "M4 5h16v11H9l-5 4Z",
  lock: "M6 11h12v9H6ZM8.500 11V8a3.500 3.500 0 0 1 7 0v3",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5.500v.01M9.500 9.500a2.500 2.500 0 1 1 3.500 2.300c-.700.400-1 .900-1 1.700",
  grid: "M4 4h7v7H4ZM13 4h7v7h-7ZM4 13h7v7H4ZM13 13h7v7h-7Z",
  rows: "M4 5h16v4H4ZM4 11h16v4H4ZM4 17h16v3H4Z",
  gantt: "M4 6h8M8 12h10M6 18h7",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  rewind: "M11 6 4 12l7 6ZM20 6l-7 6 7 6Z",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.500 21a7.500 7.500 0 0 1 15 0",
  tool: "M4 17l6-6M14 4l6 6-3 3-6-6ZM8 13l3 3",
  dot: "M12 13.500a1.500 1.500 0 1 0 0-3 1.500 1.500 0 0 0 0 3Z",
  close: "M6 6l12 12M18 6 6 18",
};

export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || ICONS.circle);
  svg.append(path);
  return svg;
}

export const pad = (n) => String(n).padStart(2, "0");

export function money(value) {
  if (value == null) return "—";
  return value < 0.01 && value > 0 ? "<$0.01" : `$${value.toFixed(value < 10 ? 2 : 1)}`;
}

export function tokens(n) {
  if (n == null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return String(n);
}

export function bytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function duration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${pad(s % 60)}s`;
  return `${Math.floor(m / 60)}h ${pad(m % 60)}m`;
}

export function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function clock(iso) {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function seconds(from, to) {
  if (!from) return null;
  return ((to ? new Date(to) : new Date()) - new Date(from)) / 1000;
}

export function download(name, blob) {
  const url = URL.createObjectURL(blob);
  const link = h("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function debounce(fn, ms) {
  let timer;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}
