// The component vocabulary: chips, meters, states, toasts, tabs. Screens compose these and add no
// styles of their own, so the live dashboard can reuse them as they are.

import { h, icon, clear, append } from "./dom.js";

const STATUS = {
  starting: ["Starting", "info", "spinner", true],
  pending: ["Pending", "", "circle"],
  running: ["Running", "info", "spinner", true],
  succeeded: ["Succeeded", "good", "check"],
  verified: ["Verified", "good", "check"],
  failed: ["Failed", "bad", "x"],
  cancelled: ["Cancelled", "warn", "ban"],
  interrupted: ["Interrupted", "warn", "alert"],
  partial: ["Partial", "warn", "alert"],
  "needs-info": ["Needs info", "warn", "alert"],
  skipped: ["Skipped", "", "circle"],
  passed: ["Passed", "good", "check"],
  unavailable: ["Unavailable", "warn", "alert"],
  referenced: ["Referenced", "info", "circle"],
  unverified: ["Unverified", "warn", "alert"],
  ok: ["OK", "good", "check"],
  warn: ["Warning", "warn", "alert"],
  fail: ["Failing", "bad", "x"],
  info: ["Info", "info", "circle"],
  paused: ["Paused", "warn", "pause"],
  backlog: ["Backlog", "", "circle"],
  ready: ["Ready", "", "circle"],
  in_progress: ["In progress", "info", "spinner", true],
  verifying: ["Verifying", "info", "shield"],
  blocked: ["Blocked", "warn", "lock"],
  done: ["Done", "good", "check"],
};

export function statusChip(status, label) {
  const [text, tone, glyph, live] = STATUS[status] || [status, "", "circle"];
  return h("span", { class: `chip ${tone}${live ? " live" : ""}` }, icon(glyph), label || text);
}

export const chip = (text, tone = "", glyph) => h("span", { class: `chip ${tone}` }, glyph && icon(glyph), text);

export function meter(percent, { tone = "", label = "", thin = false } = {}) {
  const value = Math.max(0, Math.min(100, Math.round(percent || 0)));
  const bar = h("span");
  bar.style.width = `${value}%`;
  return h(
    "div",
    { class: `meter ${tone}${thin ? " thin" : ""}`, role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": value, "aria-label": label || "Progress" },
    bar,
  );
}

export function button(label, { kind = "", glyph, onclick, type = "button", small = false, big = false, title, disabled } = {}) {
  return h(
    "button",
    { class: `btn ${kind}${small ? " small" : ""}${big ? " big" : ""}`, type, onclick, title, disabled },
    glyph && icon(glyph),
    label,
  );
}

export function panel(title, body, { hint, actions } = {}) {
  return h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, title), hint && h("span", { class: "hint" }, hint), actions),
    h("div", { class: "panel-body" }, body),
  );
}

export function loading(lines = 3) {
  const wrap = h("div", { role: "status", "aria-label": "Loading" });
  for (let i = 0; i < lines; i += 1) {
    const bar = h("div", { class: "skeleton" });
    bar.style.height = i === 0 ? "28px" : "16px";
    bar.style.width = `${100 - i * 12}%`;
    wrap.append(bar);
  }
  return wrap;
}

export function emptyState(title, text, ...actions) {
  return h("div", { class: "empty" }, h("h3", {}, title), h("p", {}, text), actions.length ? h("div", { class: "row" }, actions) : null);
}

export function errorState(error, retry) {
  return h(
    "div",
    { class: "callout bad", role: "alert" },
    h("strong", {}, "That did not work. "),
    String(error?.message || error),
    retry && h("div", { class: "row mt" }, button("Try again", { glyph: "refresh", small: true, onclick: retry })),
  );
}

export function announce(message) {
  const live = document.getElementById("live");
  live.textContent = "";
  setTimeout(() => {
    live.textContent = message;
  }, 30);
}

export function toast(message, tone = "") {
  const box = document.getElementById("toasts");
  const note = h("div", { class: `toast ${tone}`, role: tone === "bad" ? "alert" : "status" }, message);
  box.append(note);
  setTimeout(() => note.remove(), tone === "bad" ? 9000 : 4500);
}

// A roving-tabindex tablist: arrow keys move, Home/End jump. `onSelect(key)` runs on change.
export function tabs(items, { selected, onSelect, label, className = "seg" }) {
  const list = h("div", { class: className, role: "tablist", "aria-label": label });
  const buttons = new Map();
  const select = (key, focus = false) => {
    for (const [k, btn] of buttons) {
      btn.setAttribute("aria-selected", String(k === key));
      btn.tabIndex = k === key ? 0 : -1;
    }
    if (focus) buttons.get(key).focus();
    onSelect(key);
  };
  for (const { key, label: text, controls } of items) {
    const btn = h("button", { type: "button", role: "tab", id: `tab-${key}`, "aria-controls": controls, onclick: () => select(key) }, text);
    buttons.set(key, btn);
    list.append(btn);
  }
  list.addEventListener("keydown", (event) => {
    const keys = [...buttons.keys()];
    const at = keys.findIndex((k) => buttons.get(k) === document.activeElement);
    const next = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: keys.length - 1 }[event.key];
    if (next === undefined || at < 0) return;
    event.preventDefault();
    select(keys[(next + keys.length) % keys.length], true);
  });
  select(selected);
  return { element: list, select };
}

export function copyCommand(text) {
  return h(
    "div",
    { class: "cmd" },
    h("code", {}, text),
    button("Copy", {
      glyph: "copy",
      small: true,
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast("Copied");
        } catch {
          toast("Select the command and copy it by hand.", "bad");
        }
      },
    }),
  );
}

export function kv(pairs) {
  const list = h("dl", { class: "kv" });
  for (const [k, v] of pairs) {
    if (v == null || v === "") continue;
    append(list, [h("dt", {}, k), h("dd", {}, v)]);
  }
  return list;
}

export function table(head, rows, { numeric = [] } = {}) {
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, head.map((text, i) => h("th", { class: numeric.includes(i) ? "num" : "", scope: "col" }, text)))),
      h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((cell, i) => h("td", { class: numeric.includes(i) ? "num" : "" }, cell))))),
    ),
  );
}

export { clear };
