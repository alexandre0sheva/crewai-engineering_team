// The timeline: a Gantt chart of the stages and, under each, the parallel lanes that ran inside it.
// Failed bars are hatched and carry a ✕, repaired or retried stages say how many attempts they took
// (colour is never the only signal), and while a run is replayed a cursor marks the moment.

import { clear, duration, h, icon, seconds } from "../dom.js";

const GLYPH = { succeeded: "check", running: "spinner", failed: "x", cancelled: "ban", interrupted: "alert", skipped: "circle", pending: "circle" };

export function createTimeline(store) {
  const root = h("div", { class: "gantt", role: "group", "aria-label": "Timeline of stages and parallel lanes" });

  function bar(start, end, total, status, text, title) {
    const el = h("div", { class: `g-bar ${status}`, tabindex: 0, role: "img", "aria-label": title, title }, h("span", {}, status === "failed" ? "✕ " : "", text));
    el.style.left = `${(start / total) * 100}%`;
    el.style.width = `${Math.max(((end - start) / total) * 100, 0.8)}%`;
    return el;
  }

  function paint() {
    const t = store.timeline;
    clear(root);
    if (!t || !t.stages.length) return root.append(h("p", { class: "help pad" }, "Nothing has run yet."));
    const total = Math.max(t.seconds, 1);
    const ticks = 6;
    const axis = h("div", { class: "g-row g-axis" }, h("div", { class: "g-label" }, "seconds"), h("div", { class: "g-track" }, Array.from({ length: ticks + 1 }, (_, i) => {
      const tick = h("span", { class: "g-tick" }, `${Math.round((total * i) / ticks)}`);
      tick.style.left = `${(i / ticks) * 100}%`;
      return tick;
    })));
    root.append(axis);
    for (const stage of t.stages) {
      const end = stage.end ?? total;
      const attempts = stage.attempts > 1 ? ` · ${stage.attempts} attempts` : "";
      const title = `${stage.name}: ${stage.status}${attempts}${stage.start != null ? ` · ${duration(end - (stage.start ?? 0))}` : ""}${stage.detail ? ` · ${stage.detail}` : ""}`;
      const track = h("div", { class: "g-track" });
      if (stage.start != null) track.append(bar(stage.start, end, total, stage.status, stage.attempts > 1 ? `↻${stage.attempts}` : "", title));
      root.append(h("div", { class: "g-row g-stage" }, h("div", { class: "g-label" }, icon(GLYPH[stage.status] || "circle"), h("strong", {}, stage.name), stage.attempts > 1 ? h("span", { class: "tag" }, `${stage.attempts}×`) : null), track));
      for (const lane of t.lanes.filter((l) => l.stage === stage.name)) {
        const text = `${lane.unit}${lane.status === "failed" ? " failed" : ""}`;
        const ltitle = `Lane ${lane.lane} · ${lane.unit}: ${lane.status} · ${duration(lane.end - lane.start)}${lane.error ? ` · ${lane.error}` : ""}`;
        root.append(h("div", { class: "g-row g-lane" }, h("div", { class: "g-label" }, h("span", { class: "help" }, `lane ${lane.lane}`), h("span", { class: "mono" }, lane.unit)), h("div", { class: "g-track" }, bar(lane.start, lane.end, total, lane.status, text, ltitle))));
      }
    }
    checks(total);
    cursor(total);
  }

  // Check and repair cards: when each ran, and the ones that failed.
  function checks(total) {
    const created = store.run?.manifest?.created;
    const cards = [...store.cards.values()].filter((c) => ["check", "repair"].includes(c.kind) && c.started);
    if (!created || !cards.length) return;
    root.append(h("div", { class: "g-row g-group" }, h("div", { class: "g-label" }, icon("shield"), h("strong", {}, "Checks and repairs")), h("div", { class: "g-track" })));
    for (const card of cards.sort((a, b) => new Date(a.started) - new Date(b.started))) {
      const track = h("div", { class: "g-track" });
      for (const part of attempts(card)) {
        const start = seconds(created, part.from);
        const end = part.to ? seconds(created, part.to) : total;
        const title = `${card.id} ${card.title}, attempt ${part.n}: ${part.status === "succeeded" ? "passed" : part.status}`;
        track.append(bar(start, Math.max(end, start + total * 0.01), total, part.status, `${card.title}${part.n > 1 || part.status === "failed" ? ` #${part.n}` : ""}`, title));
      }
      root.append(h("div", { class: "g-row g-lane" }, h("div", { class: "g-label" }, h("span", { class: "mono" }, card.id), h("span", { class: "help" }, card.kind)), track));
    }
  }

  // One bar per time the card was worked on: from "in progress" (through verifying) to the move
  // that ended it.
  function attempts(card) {
    const parts = [];
    let open = null;
    for (const move of card.history) {
      if (move.to_status === "in_progress") {
        open = { from: move.ts, n: parts.length + 1 };
      } else if (open && ["done", "failed", "blocked", "cancelled"].includes(move.to_status)) {
        parts.push({ ...open, to: move.ts, status: move.to_status === "failed" ? "failed" : move.to_status === "cancelled" ? "cancelled" : "succeeded" });
        open = null;
      }
    }
    if (open) parts.push({ ...open, to: null, status: "running" });
    return parts;
  }

  function cursor(total) {
    root.querySelector(".g-cursor")?.remove();
    const run = store.run;
    if (!store.replay || !run?.manifest) return;
    const at = seconds(run.manifest.created, store.events[store.replay.index]?.ts);
    const line = h("div", { class: "g-cursor", "aria-hidden": "true" });
    line.style.left = `calc(var(--g-label) + (100% - var(--g-label)) * ${Math.min(Math.max(at / total, 0), 1)})`;
    root.append(line);
  }

  const offs = [store.on("timeline", paint), store.on("view", () => !store.replay && paint()), store.on("replay", () => cursor(Math.max(store.timeline?.seconds || 1, 1)))];
  paint();
  return { element: root, destroy: () => offs.forEach((f) => f()) };
}
