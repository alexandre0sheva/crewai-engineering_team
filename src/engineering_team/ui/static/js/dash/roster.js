// The teammates: who is doing what right now (or at the replayed moment).

import { clear, h, icon, money, seconds, tokens } from "../dom.js";
import { avatar, label } from "./colors.js";

const STATE = {
  working: ["Working", "spinner", "info"],
  waiting: ["Waiting for you", "help", "warn"],
  blocked: ["Blocked", "lock", "warn"],
  idle: ["Idle", "dot", ""],
};

export function createRoster(store, open) {
  const list = h("div", { class: "roster", role: "list", "aria-label": "Teammates" });

  function ago(iso, now) {
    const s = Math.max(0, Math.round(seconds(iso, now)));
    return s < 2 ? "just now" : s < 90 ? `${s}s ago` : `${Math.round(s / 60)} min ago`;
  }

  function paint() {
    const agents = store.viewAgents;
    const now = store.replay ? store.events[store.replay.index]?.ts : null;
    clear(list);
    if (!agents.length) return list.append(h("p", { class: "help" }, "No teammate has started yet."));
    for (const a of agents) {
      const [text, glyph, tone] = STATE[a.state] || STATE.idle;
      list.append(
        h("div", { class: `r-card ${a.state}`, role: "listitem" },
          h("div", { class: "r-top" }, avatar(a.agent, { large: true }), h("div", { class: "r-name" }, h("strong", {}, label(a.agent)), h("span", { class: `chip ${tone}${a.state === "working" ? " live" : ""}` }, icon(glyph), text))),
          a.card_id ? h("button", { type: "button", class: "r-card-link", onclick: () => open(a.card_id) }, h("span", { class: "mono" }, a.card_id), " ", a.card_title) : h("p", { class: "help" }, a.state === "idle" ? "No card in progress." : ""),
          h("p", { class: "r-last" }, a.last_tool ? [icon("tool"), `${a.last_tool} `, h("span", { class: a.last_tool_ok ? "help" : "bad-text" }, `${a.last_tool_ok ? "" : "failed "}${ago(a.last_tool_at, now)}`)] : "No tool calls yet."),
          h("dl", { class: "r-stats" }, h("div", {}, h("dt", {}, "calls"), h("dd", {}, a.tool_calls)), h("div", {}, h("dt", {}, "errors"), h("dd", { class: a.failed_calls ? "bad-text" : "" }, a.failed_calls)), h("div", {}, h("dt", {}, "tokens"), h("dd", {}, tokens(a.tokens))), h("div", {}, h("dt", {}, "cost"), h("dd", {}, a.cost_usd == null ? "—" : money(a.cost_usd)))),
          a.model ? h("p", { class: "help mono" }, a.model) : null),
      );
    }
  }
  const offs = [store.on("view", paint)];
  const tick = setInterval(paint, 5000); // "3s ago" keeps counting
  paint();
  return { element: list, destroy: () => { offs.forEach((f) => f()); clearInterval(tick); } };
}
