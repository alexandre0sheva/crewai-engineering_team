// The checks (what the controller ran itself) and the review findings, live.

import { duration, h } from "../dom.js";
import { chip, statusChip } from "../ui.js";

const TONE = { critical: "bad", high: "bad", medium: "warn", low: "info", info: "" };
const ORDER = ["critical", "high", "medium", "low", "info"];

export function createChecks(store) {
  const box = h("div", { class: "stack" });

  function paint() {
    const checks = store.run?.checks || [];
    const findings = [...(store.timeline?.findings || [])].sort((a, b) => ORDER.indexOf(a.severity) - ORDER.indexOf(b.severity));
    box.replaceChildren(
      h("div", {}, h("h3", { class: "mini-h" }, "Checks"), checks.length ? h("ul", { class: "c-list" }, checks.map((c) => h("li", {}, statusChip(c.status === "running" ? "running" : c.status), h("span", { class: "c-name" }, c.name, c.summary ? h("span", { class: "sub" }, c.summary) : null), c.duration != null ? h("span", { class: "counter" }, duration(c.duration)) : null))) : h("p", { class: "help" }, "None has run yet.")),
      h("div", {}, h("h3", { class: "mini-h" }, "Review findings"), findings.length ? h("ul", { class: "c-list" }, findings.map((f) => h("li", {}, chip(f.severity, TONE[f.severity] || ""), h("span", { class: "c-name" }, f.summary, f.file ? h("span", { class: "sub mono" }, `${f.file}${f.line ? `:${f.line}` : ""}`) : null)))) : h("p", { class: "help" }, "No findings yet.")),
    );
  }
  const offs = [store.on("run", paint), store.on("timeline", paint)];
  paint();
  return { element: box, destroy: () => offs.forEach((f) => f()) };
}
