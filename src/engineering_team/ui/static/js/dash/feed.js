// The activity feed: every event of the run, newest at the bottom, filterable, and cheap however
// many there are. Only the rows in view exist in the DOM (a spacer gives the scrollbar its length)
// and redraws are batched into animation frames, so thousands of events stay smooth.

import { clock, h, icon } from "../dom.js";
import { button } from "../ui.js";
import { avatar, label } from "./colors.js";
import { STATUS_LABEL } from "./store.js";

const ROW = 30;
const OPEN = 132; // an expanded row
const OVERSCAN = 8;
const TYPES = [["all", "All events"], ["tool", "Tool calls"], ["board", "Board"], ["stage", "Stages and lanes"], ["check", "Checks"], ["budget", "Budget"], ["question", "Questions"]];
const SEVERITIES = [["all", "Everything"], ["warn", "Warnings and errors"], ["error", "Errors only"]];

export function classify(e) {
  const d = e.data || {};
  const t = e.type;
  if (t === "tool.call") return { kind: "tool", severity: d.ok === false ? "error" : "info", text: `${d.tool}${d.args ? ` ${typeof d.args === "string" ? d.args : JSON.stringify(d.args)}` : ""}${d.ok === false ? " — failed" : ""}`, card: null };
  if (t === "board.card_moved") {
    const to = d.to_status;
    return { kind: "board", severity: to === "failed" ? "error" : to === "blocked" || d.card?.kind === "repair" ? "warn" : "info", text: `${d.card_id}: ${STATUS_LABEL[d.from_status] || d.from_status} → ${STATUS_LABEL[to] || to}${d.reason ? ` (${d.reason})` : ""}`, card: d.card_id };
  }
  if (t.startsWith("board.")) return { kind: "board", severity: d.card?.kind === "repair" ? "warn" : "info", text: `${t.slice(6).replace(/_/g, " ")}${d.card ? ` ${d.card.id}: ${d.card.title}` : ""}`, card: d.card?.id || d.card_id || null };
  if (t.startsWith("check.")) return { kind: "check", severity: d.status === "failed" ? "error" : d.status === "unavailable" ? "warn" : "info", text: `${t.slice(6)} ${d.check}${d.status ? `: ${d.status}` : ""}${d.summary ? ` — ${d.summary}` : ""}`, card: null };
  if (t.startsWith("budget.")) return { kind: "budget", severity: t === "budget.exceeded" ? "error" : "warn", text: `${t.slice(7)} ${d.limit || ""}${d.fraction ? ` at ${Math.round(d.fraction * 100)}%` : ""}${d.note ? ` — ${d.note}` : ""}`, card: null };
  if (t.startsWith("question")) return { kind: "question", severity: t === "question" ? "warn" : "info", text: `${t}${d.text ? `: ${d.text}` : ""}`, card: d.card_id || null };
  if (/^(stage|lane)\./.test(t)) {
    const bad = d.status === "failed" || /fail|error/.test(t);
    const retry = /retry|repair/.test(t);
    return { kind: "stage", severity: bad ? "error" : retry ? "warn" : "info", text: `${t}${e.stage ? ` [${e.stage}]` : ""}${d.unit ? ` ${d.unit}` : ""}${d.status ? ` ${d.status}` : ""}${d.error ? ` — ${d.error}` : ""}`, card: null };
  }
  const rest = Object.entries(d).filter(([k]) => k !== "card").map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ");
  return { kind: "other", severity: /fail|error/.test(t) ? "error" : "info", text: `${t} ${rest}`.trim(), card: d.card_id || null };
}

export function createFeed(store, open) {
  const state = { agent: "all", type: "all", severity: "all", card: "", stick: true, expanded: new Set(), rows: [], tops: [], total: 0 };
  const viewport = h("div", { class: "feed-view", tabindex: 0, role: "region", "aria-label": "Activity feed" });
  const spacer = h("div", { class: "feed-spacer" });
  const windowEl = h("div", { class: "feed-window", role: "list" });
  viewport.append(spacer, windowEl);
  const status = h("span", { class: "chip" });
  const jump = button("Jump to latest", { small: true, glyph: "chevron", onclick: () => { state.stick = true; viewport.scrollTop = viewport.scrollHeight; jump.hidden = true; } });
  jump.hidden = true;

  const agentSel = h("select", { id: "f-agent", "aria-label": "Filter by teammate", onchange: () => { state.agent = agentSel.value; rebuild(); } }, h("option", { value: "all" }, "All teammates"));
  const typeSel = h("select", { id: "f-type", "aria-label": "Filter by kind of event", onchange: () => { state.type = typeSel.value; rebuild(); } }, TYPES.map(([v, t]) => h("option", { value: v }, t)));
  const sevSel = h("select", { id: "f-sev", "aria-label": "Filter by severity", onchange: () => { state.severity = sevSel.value; rebuild(); } }, SEVERITIES.map(([v, t]) => h("option", { value: v }, t)));
  const cardIn = h("input", { type: "search", id: "f-card", placeholder: "Card id, e.g. K-004", "aria-label": "Filter by card", oninput: () => { state.card = cardIn.value.trim().toUpperCase(); rebuild(); } });
  const bar = h("div", { class: "toolbar" }, agentSel, typeSel, sevSel, cardIn);
  const element = h("div", { class: "feed" }, bar, viewport, h("div", { class: "row spread" }, status, jump));

  const known = new Set();
  function agents() {
    for (const e of store.events) {
      if (e.agent && !known.has(e.agent)) {
        known.add(e.agent);
        agentSel.append(h("option", { value: e.agent }, label(e.agent)));
      }
    }
  }

  const keep = (e, c) => {
    if (state.agent !== "all" && e.agent !== state.agent) return false;
    if (state.type !== "all" && c.kind !== state.type) return false;
    if (state.severity === "error" && c.severity !== "error") return false;
    if (state.severity === "warn" && c.severity === "info") return false;
    return !state.card || c.card === state.card;
  };

  // Recompute the visible list and the row offsets (cheap: one pass).
  function rebuild() {
    agents();
    const last = store.replay ? store.replay.index : store.events.length - 1;
    const rows = [];
    for (let i = 0; i <= last; i += 1) {
      const e = store.events[i];
      const c = classify(e);
      if (keep(e, c)) rows.push({ e, c, id: e.seq });
    }
    state.rows = rows;
    let y = 0;
    state.tops = rows.map((r) => {
      const top = y;
      y += state.expanded.has(r.id) ? OPEN : ROW;
      return top;
    });
    state.total = y;
    spacer.style.height = `${y}px`;
    status.textContent = `${rows.length.toLocaleString()} of ${(last + 1).toLocaleString()} events${store.streamState === "live" ? " · live" : store.streamState === "reconnecting" ? " · reconnecting…" : ""}`;
    if (state.stick && !store.replay) viewport.scrollTop = state.total;
    draw();
  }

  let frame = 0;
  const schedule = () => {
    frame ||= requestAnimationFrame(() => {
      frame = 0;
      rebuild();
    });
  };

  function draw() {
    const top = viewport.scrollTop;
    const height = viewport.clientHeight || 400;
    let from = 0;
    let lo = 0;
    let hi = state.tops.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (state.tops[mid] <= top) {
        from = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    from = Math.max(0, from - OVERSCAN);
    const nodes = [];
    for (let i = from; i < state.rows.length && state.tops[i] < top + height + OVERSCAN * ROW; i += 1) nodes.push(row(state.rows[i], state.tops[i]));
    windowEl.replaceChildren(...nodes);
  }

  function row({ e, c, id }, top) {
    const open_ = state.expanded.has(id);
    const el = h("div", { class: `f-row ${c.severity}${open_ ? " open" : ""}`, role: "listitem" });
    el.style.transform = `translateY(${top}px)`;
    el.style.height = `${open_ ? OPEN : ROW}px`;
    const toggle = h("button", { type: "button", class: "f-line", "aria-expanded": String(open_), onclick: () => { open_ ? state.expanded.delete(id) : state.expanded.add(id); rebuild(); } },
      h("span", { class: "f-time mono" }, clock(e.ts)),
      e.agent ? avatar(e.agent) : h("span", { class: "avatar none", "aria-hidden": "true" }, "·"),
      h("span", { class: "f-sev", title: c.severity }, c.severity === "error" ? icon("x") : c.severity === "warn" ? icon("alert") : icon("dot"), h("span", { class: "sr-only" }, c.severity)),
      h("span", { class: "f-text" }, c.text));
    el.append(toggle);
    if (open_) {
      const detail = h("pre", { class: "f-detail mono" }, JSON.stringify({ seq: e.seq, type: e.type, agent: e.agent, stage: e.stage, lane: e.lane, data: { ...e.data, card: e.data?.card ? `(card ${e.data.card.id})` : undefined } }, null, 2));
      el.append(detail);
      if (c.card) el.append(button(`Open ${c.card}`, { small: true, kind: "ghost", onclick: () => open(c.card) }));
    }
    return el;
  }

  viewport.addEventListener("scroll", () => {
    state.stick = viewport.scrollTop + viewport.clientHeight >= state.total - 24;
    jump.hidden = state.stick;
    draw();
  });
  const offs = [store.on("events", schedule), store.on("replay", schedule)];
  rebuild();
  return { element, destroy: () => { offs.forEach((f) => f()); cancelAnimationFrame(frame); }, refresh: rebuild };
}
