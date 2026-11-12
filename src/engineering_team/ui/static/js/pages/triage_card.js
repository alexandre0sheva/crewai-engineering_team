// The live plan on the new-run page: what the team understood, where it starts, the stages and why,
// and the estimate. Every string is made by the server (`POST /triage`); this only lays it out.

import { clear, h } from "../dom.js";
import { button, chip, emptyState, kv, loading, panel, table } from "../ui.js";

const TONE = { low: "good", medium: "warn", high: "bad" };

export function triageCard({ onRefine }) {
  const body = h("div", { class: "stack", "aria-live": "polite", id: "triage-body" });
  const element = panel("The plan", body, { hint: "updates as you type" });
  element.id = "triage";
  const idle = () => clear(body).append(emptyState("Nothing to plan yet", "Describe what you want and the plan appears here: what the team understood, where it starts, what it will do and roughly what it costs."));
  idle();
  return {
    element,
    idle,
    busy: () => clear(body).append(loading(4)),
    failed: (message) => clear(body).append(h("p", { class: "error", role: "alert" }, message)),
    show(plan, { canRefine }) {
      clear(body).append(planView(plan, canRefine ? onRefine : null));
    },
  };
}

function planView(plan, onRefine) {
  const u = plan.understood;
  const parts = [
    h("div", { class: "plan-head" },
      h("h3", {}, "Understood as: ", u.headline),
      h("p", { class: "help" }, `${plan.route.label}${u.project ? ` (${u.project})` : ""}.`),
      h("div", { class: "row" },
        chip(`${u.size} task`, ""), chip(`${u.risk} risk`, TONE[u.risk] || ""), chip(`${u.confidence} confidence`, ""), chip(`decided by ${u.by}`, "info"))),
  ];
  if (u.rationale) parts.push(h("p", {}, u.rationale));
  if (u.proved_by.length) parts.push(h("p", { class: "row" }, h("strong", {}, "Proved by: "), u.proved_by.map((p) => chip(p.replace(/_/g, " "), "good"))));
  for (const [label, items] of [["Assumed", u.assumptions], ["Rule applied", u.rules]]) {
    if (items.length) parts.push(h("ul", { class: "plain" }, items.map((text) => h("li", {}, h("strong", {}, `${label}: `), text))));
  }
  if (u.questions.length) {
    parts.push(h("div", { class: "callout info" }, h("strong", {}, "Questions triage would ask. "), "The run assumes an answer unless you settle them in the request:", h("ul", {}, u.questions.map((q) => h("li", {}, q)))));
  }
  parts.push(startingPoint(plan));
  if (plan.stages.length) {
    parts.push(h("details", { class: "plan-stages", open: true },
      h("summary", {}, `${plan.stages.length} stages${plan.recipe ? ` · recipe ${plan.recipe}` : ""}`),
      table(["#", "Stage", "Kind", "Why"], plan.stages.map((s, i) => [i + 1, h("strong", {}, s.name), h("span", {}, s.kind, s.cost ? h("span", { class: "sub" }, s.cost) : null), s.why]), { numeric: [0] })));
  }
  parts.push(h("div", { class: `callout ${plan.estimate.read_only ? "info" : ""}`, role: "status" }, plan.estimate.text));
  parts.push(kv([["Harness", plan.harness.line], ["Attempts", plan.attempts.count > 1 ? `best of ${plan.attempts.count}, varied by ${plan.attempts.vary}` : ""]]));
  for (const note of plan.notes) parts.push(h("p", { class: "callout info" }, note));
  parts.push(h("details", {}, h("summary", {}, "What the controller saw"), h("ul", { class: "plain mono" }, plan.signals.map((s) => h("li", {}, s)))));
  if (onRefine) {
    parts.push(h("div", { class: "row" }, button(plan.model_used ? "Refined by the model" : "Refine with the model", { small: true, glyph: "refresh", disabled: plan.model_used, onclick: onRefine }), h("span", { class: "help" }, plan.model_used ? "One cheap model call was made for this preview." : "The preview uses signals only (free). The run itself asks the model for one cheap triage call.")));
  }
  return h("div", { class: "stack" }, parts);
}

function startingPoint(plan) {
  if (plan.rung) {
    const r = plan.rung;
    const climb = r.max === r.start ? "" : ` If the checks still fail it escalates, up to the ${r.max} rung (${r.max_label.toLowerCase()}).`;
    return h("div", { class: "callout" }, h("strong", {}, `Starts on ${r.start} — ${r.start_label.toLowerCase()}.`), climb, r.reasons.length ? h("ul", {}, r.reasons.map((why) => h("li", {}, why))) : null);
  }
  return h("div", { class: "callout" }, h("strong", {}, `Strategy ${plan.strategy}.`), " ", plan.route.text);
}
