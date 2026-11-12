// A run's thread: where it sits among the runs that follow from the same first one, and the box
// that starts a follow-up (`continue`) from it. The server says what is possible (`views.thread`).

import { ApiError, post } from "../api.js";
import { clear, h } from "../dom.js";
import { announce, button, errorState, panel, statusChip } from "../ui.js";

export function threadStrip(thread) {
  if (!thread.in_thread) return null;
  return h("nav", { class: "thread-strip", "aria-label": "Runs in this thread" },
    h("span", { class: "eyebrow" }, "Thread"),
    h("ol", {}, thread.runs.map((r, i) => h("li", { class: r.current ? "current" : "", title: r.request },
      r.current ? h("span", { "aria-current": "page" }, `${i + 1}. this run`) : h("a", { href: `#/runs/${r.run_id}/results` }, `${i + 1}. ${r.run_id.slice(9, 15)}`),
      statusChip(r.status === "succeeded" && r.verdict ? "verified" : r.status, r.verdict || r.status)))));
}

export function followUpBox(thread, runId) {
  const text = h("textarea", { id: "follow-up", rows: 3, placeholder: "Now also handle empty input.", "aria-describedby": "follow-up-help" });
  const slot = h("div", { "aria-live": "assertive" });
  const go = button("Start follow-up", { kind: "primary", glyph: "plus", type: "submit" });
  const implement = thread.can_implement ? button("Implement the stored plan", { glyph: "play", onclick: () => submit(true) }) : null;
  async function submit(implementPlan) {
    clear(slot);
    const request = text.value.trim();
    if (!request && !implementPlan) return clear(slot).append(errorState("Say what to do next."));
    go.disabled = true;
    try {
      const body = { mode: "continue", parent: runId };
      if (request) body.request = request;
      if (implementPlan) body.implement = true;
      const answer = await post("/runs", body);
      announce("Follow-up started");
      location.hash = `#/runs/${answer.run_id}`;
    } catch (error) {
      go.disabled = false;
      clear(slot).append(errorState(error instanceof ApiError ? error.message : String(error)));
    }
  }
  const form = h("form", { class: "stack", novalidate: true, onsubmit: (e) => { e.preventDefault(); submit(false); } },
    h("div", { class: "field" }, h("label", { for: "follow-up" }, "What next?"), text, h("span", { class: "help", id: "follow-up-help" }, "The follow-up works on the same branch with this run's spec, plan, answer and verdict as context. Nothing it proved is reused: the checks run again.")),
    slot, h("div", { class: "row" }, go, implement));
  if (!thread.can_continue) {
    text.disabled = go.disabled = true;
    return panel("Follow up", h("div", { class: "stack" }, h("p", { class: "callout info" }, thread.why_not), form));
  }
  return panel("Follow up", form, { hint: thread.in_thread ? `run ${thread.runs.findIndex((r) => r.current) + 1} of ${thread.runs.length}` : "continue" });
}
