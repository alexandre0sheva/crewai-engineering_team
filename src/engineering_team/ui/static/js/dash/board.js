// The kanban board and the swimlane view. Both are one matrix of cells (a row per teammate, or a
// single row; a column per status); a card element lives in the cell its card belongs to and is
// moved, with a FLIP animation, when its status changes.

import { clear, h, icon } from "../dom.js";
import { avatar, label } from "./colors.js";
import { makeCard, updateCard } from "./cards.js";
import { STATUS_LABEL } from "./store.js";

const COLUMNS = ["backlog", "ready", "in_progress", "verifying", "blocked", "done"];
const SIDE = ["failed", "cancelled"];
const COLUMN_ICON = { backlog: "list", ready: "circle", in_progress: "spinner", verifying: "shield", blocked: "lock", done: "check", failed: "x", cancelled: "ban" };
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export function createBoard(store, open, { lanes = false } = {}) {
  const root = h("div", { class: `board${lanes ? " lanes" : ""}`, role: "group", "aria-label": lanes ? "Task board by teammate" : "Task board" });
  const els = new Map(); // card id -> element
  const cells = new Map(); // "row|status" -> list element
  const cellKey = new Map(); // list element -> "row|status"
  const slot = (card) => `${SIDE.includes(card.status) ? "" : rowOf(card)}|${card.status}`;
  let rows = [];
  let focused = null;
  const sideOpen = { failed: false, cancelled: false };

  const rowOf = (card) => (lanes ? card.assignee || "controller" : "");

  function layout(cards) {
    const names = lanes ? [...new Set(cards.map(rowOf))].sort((a, b) => (a === "controller") - (b === "controller") || a.localeCompare(b)) : [""];
    const side = SIDE.filter((s) => cards.some((c) => c.status === s));
    const key = JSON.stringify([names, side]);
    if (key === layout.key) return;
    layout.key = key;
    rows = names;
    cells.clear();
    cellKey.clear();
    clear(root);
    const head = h("div", { class: "b-row b-head" }, lanes ? h("div", { class: "b-label" }) : null, ...COLUMNS.map(columnHead));
    root.append(head);
    for (const name of names) {
      const row = h("div", { class: "b-row", role: "group", "aria-label": lanes ? label(name) : "Columns" });
      if (lanes) row.append(h("div", { class: "b-label" }, avatar(name === "controller" ? null : name), h("span", {}, label(name))));
      for (const status of COLUMNS) row.append(cell(name, status));
      root.append(row);
    }
    if (side.length) {
      const wrap = h("div", { class: "b-side" });
      for (const status of side) {
        const details = h("details", { class: "b-fold", open: sideOpen[status], ontoggle: () => (sideOpen[status] = details.open) }, h("summary", {}, icon(COLUMN_ICON[status]), STATUS_LABEL[status], " ", h("span", { class: "count", dataset: { count: status } })), cell("", status));
        wrap.append(details);
      }
      root.append(wrap);
    }
    // Cards must be re-placed into the new cells.
    for (const el of els.values()) el.remove();
    els.clear();
  }

  function columnHead(status) {
    return h("div", { class: "b-col-head", title: STATUS_LABEL[status], dataset: { status } }, icon(COLUMN_ICON[status]), h("span", {}, STATUS_LABEL[status]), h("span", { class: "count", dataset: { count: status } }));
  }

  function cell(row, status) {
    const list = h("div", { class: "b-cell", role: "group", "aria-label": `${STATUS_LABEL[status]}${lanes ? `, ${label(row)}` : ""}`, dataset: { status } });
    cells.set(`${row}|${status}`, list);
    cellKey.set(list, `${row}|${status}`);
    return list;
  }

  function update() {
    const cards = store.viewCards.filter((c) => c.kind !== "user_note");
    const active = document.activeElement;
    const hadFocus = root.contains(active) ? active.dataset.id : null;
    layout(cards);
    const before = new Map();
    for (const [id, el] of els) before.set(id, el.getBoundingClientRect());
    const wanted = new Set(cards.map((c) => c.id));
    for (const [id, el] of els) {
      if (!wanted.has(id)) {
        el.remove();
        els.delete(id);
      }
    }
    const moved = [];
    const at = store.replay ? store.events[store.replay.index]?.ts : null;
    for (const card of cards) {
      let el = els.get(card.id);
      if (!el) {
        el = makeCard(card, open, at);
        els.set(card.id, el);
      } else {
        const was = el.dataset.status;
        updateCard(el, card, at);
        if (was !== card.status) moved.push(el);
      }
    }
    // Place in cell order (by id) so the order inside a column is stable.
    const grouped = new Map();
    for (const card of cards) {
      const key = slot(card);
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(card);
    }
    for (const [key, list] of cells) {
      const mine = grouped.get(key) || [];
      const want = mine.map((c) => els.get(c.id));
      if (list.childNodes.length !== want.length || want.some((el, i) => list.childNodes[i] !== el)) list.replaceChildren(...want);
      if (!mine.length) list.dataset.empty = "true";
      else delete list.dataset.empty;
    }
    for (const status of [...COLUMNS, ...SIDE]) {
      const total = cards.filter((c) => c.status === status).length;
      for (const badge of root.querySelectorAll(`[data-count="${status}"]`)) badge.textContent = String(total);
    }
    // Columns with nothing in them shrink to a strip, so the ones with cards fit the screen.
    const template = `${lanes ? "150px " : ""}${COLUMNS.map((s) => (cards.some((c) => c.status === s) ? "minmax(200px, 1fr)" : "minmax(70px, .2fr)")).join(" ")}`;
    for (const row of root.querySelectorAll(".b-row")) row.style.gridTemplateColumns = template;
    for (const head of root.querySelectorAll(".b-col-head")) head.toggleAttribute("data-empty", !cards.some((c) => c.status === head.dataset.status));
    if (!reduced()) {
      for (const el of moved) {
        const from = before.get(el.dataset.id);
        const to = el.getBoundingClientRect();
        if (from && (from.left !== to.left || from.top !== to.top)) {
          el.animate([{ transform: `translate(${from.left - to.left}px, ${from.top - to.top}px)`, zIndex: 5 }, { transform: "none", zIndex: 5 }], { duration: 420, easing: "cubic-bezier(.2,.7,.2,1)" });
        }
        el.animate([{ boxShadow: "0 0 0 3px var(--accent)" }, { boxShadow: "0 0 0 0 transparent" }], { duration: 1400 });
      }
    }
    roving(hadFocus);
  }

  // One card is in the tab order; arrow keys move between cards, column by column and row by row.
  function roving(restore) {
    const all = [...els.values()];
    const keep = (focused && els.get(focused)) || all[0];
    for (const el of all) el.tabIndex = el === keep ? 0 : -1;
    if (restore && els.get(restore)) els.get(restore).focus({ preventScroll: true });
  }

  root.addEventListener("focusin", (event) => {
    const card = event.target.closest?.(".kcard");
    if (card) {
      focused = card.dataset.id;
      for (const el of els.values()) el.tabIndex = el === card ? 0 : -1;
    }
  });
  root.addEventListener("keydown", (event) => {
    const card = event.target.closest?.(".kcard");
    if (!card || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const here = card.parentElement;
    const siblings = [...here.children];
    const at = siblings.indexOf(card);
    let target = null;
    if (event.key === "ArrowDown") target = siblings[at + 1];
    else if (event.key === "ArrowUp") target = siblings[at - 1];
    else if (event.key === "Home") target = siblings[0];
    else if (event.key === "End") target = siblings[siblings.length - 1];
    else {
      const rowName = cellKey.get(here).split("|")[0];
      const order = COLUMNS.map((status) => cells.get(`${rowName}|${status}`)).filter((c) => c && (c.children.length || c === here));
      const next = order[order.indexOf(here) + (event.key === "ArrowRight" ? 1 : -1)];
      if (next) target = next.children[Math.min(at, next.children.length - 1)];
    }
    if (target) {
      event.preventDefault();
      target.focus();
    }
  });

  const off = store.on("view", update);
  update();
  return { element: root, destroy: off, update };
}
