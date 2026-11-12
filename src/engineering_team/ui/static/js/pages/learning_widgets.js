// What the learning screens share: a project's remembered facts (with forget) and the lessons of
// the experience library (with approve and reject). All text is learned or repository-supplied
// and is shown as plain text. The server decides what is shown and what a click changes.

import { ApiError, post } from "../api.js";
import { clear, h, when } from "../dom.js";
import { button, chip, emptyState, panel, statusChip, toast } from "../ui.js";

const STATUS_TONE = { verified: "good", proposed: "info", stale: "warn" };
const CONFIDENCE_TONE = { high: "good", medium: "info", low: "warn" };

async function act(path, body, done, ok) {
  try {
    await post(path, body);
    toast(ok);
    await done();
  } catch (error) {
    toast(error instanceof ApiError ? error.message : String(error), "bad");
  }
}

export function memorySection(view, { reload }) {
  const parts = [h("p", { class: "help" }, view.note)];
  if (!view.enabled) parts.push(h("div", { class: "callout info" }, "Repository memory is switched off (memory.enabled = false), so nothing is remembered or shown to the team."));
  for (const problem of view.problems) parts.push(h("p", { class: "error", role: "alert" }, problem));
  if (!view.facts.length && !view.problems.length) {
    parts.push(emptyState("Nothing remembered yet", "Facts are recorded as runs verify this repository: the commands that work, where things are, gotchas."));
  } else if (view.facts.length) {
    parts.push(h("ul", { class: "learned" }, view.facts.map((fact) =>
      h("li", { class: `fact ${fact.status}` },
        h("div", { class: "row spread" }, h("span", { class: "row" }, chip(fact.kind, ""), statusChip(fact.status === "verified" ? "verified" : fact.status === "stale" ? "unverified" : "info", fact.status)), button("Forget", { small: true, kind: "ghost", title: "Delete this fact", onclick: () => act(`/memory/${encodeURIComponent(fact.id)}/forget`, { repo: view.repo }, reload, "Forgotten") })),
        h("p", {}, fact.text),
        h("p", { class: "help" }, [fact.run_id && `run ${fact.run_id}`, fact.check_ids.length && `checks ${fact.check_ids.join(", ")}`, fact.sources.length && `rests on ${fact.sources.join(", ")}`, fact.last_used ? `last used ${when(fact.last_used)}` : "never used"].filter(Boolean).join(" · "))))));
  }
  const c = view.counts;
  return panel("Repository memory", h("div", { class: "stack" }, parts), { hint: `${c.verified} verified · ${c.proposed} proposed · ${c.stale} stale` });
}

export function lessonsSection(view, { repo, reload }) {
  const parts = [];
  if (!view.enabled) parts.push(h("div", { class: "callout info" }, "Learning is switched off (learning.enabled = false): lessons are neither read nor kept."));
  if (view.review === "manual") parts.push(h("div", { class: "callout info" }, "New lessons wait here for your approval (learning.review = manual). Only approved lessons reach later runs."));
  for (const problem of view.problems) parts.push(h("p", { class: "error", role: "alert" }, problem));
  const pending = view.lessons.filter((l) => l.pending);
  const kept = view.lessons.filter((l) => !l.pending);
  if (!view.lessons.length) parts.push(emptyState("No lessons yet", "A lesson is distilled after a run the controller verified, and only when its evidence holds."));
  if (pending.length) parts.push(h("section", { class: "stack" }, h("h3", {}, `Waiting for approval (${pending.length})`), h("ul", { class: "learned" }, pending.map((l) => lessonItem(l, repo, reload)))));
  if (kept.length) parts.push(h("section", { class: "stack" }, h("h3", {}, `Later runs receive these (${kept.length})`), h("ul", { class: "learned" }, kept.map((l) => lessonItem(l, repo, reload)))));
  return panel("Lessons", h("div", { class: "stack" }, parts), { hint: view.libraries.map((l) => l.name).join(" + ") || "no library" });
}

export function lessonItem(l, repo, reload) {
  const target = { library: l.library, ...(repo ? { repo } : {}) };
  const buttons = reload ? h("span", { class: "row" },
    l.pending ? button("Approve", { small: true, kind: "primary", onclick: () => act(`/lessons/${encodeURIComponent(l.id)}/approve`, target, reload, "Approved: later runs will receive it") }) : null,
    button(l.pending ? "Reject" : "Forget", { small: true, kind: "ghost", onclick: () => act(`/lessons/${encodeURIComponent(l.id)}/reject`, target, reload, "Removed") })) : null;
  return h("li", { class: `lesson${l.pending ? " pending" : ""}` },
    h("div", { class: "row spread" }, h("span", { class: "row" }, chip(l.domain, ""), chip(`${l.confidence} confidence`, CONFIDENCE_TONE[l.confidence] || ""), chip(l.library, "info"), h("code", {}, l.component)), buttons),
    h("p", {}, h("strong", {}, "When: "), l.cue),
    h("p", {}, h("strong", {}, "Then: "), l.lesson),
    h("details", {}, h("summary", {}, `Evidence: run ${l.evidence.run_id}, ${l.evidence.verdict}`),
      h("p", { class: "help" }, l.evidence.check_ids.length ? `Checks ${l.evidence.check_ids.join(", ")}.` : "No check ids."),
      l.evidence.rows.length ? h("ul", { class: "plain" }, l.evidence.rows.map((r) => h("li", {}, chip(r.verdict, r.verdict === "helped" ? "good" : r.verdict === "hurt" ? "bad" : ""), " ", r.behaviour, " → ", r.effect))) : null));
}

export function loadingNote(root, text) {
  clear(root).append(h("p", { class: "help" }, text));
}
