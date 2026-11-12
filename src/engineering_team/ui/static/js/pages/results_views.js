// The results page's views for the answer path, the escalation ladder and best-of-N attempts. The
// server made every string and number (`views` of GET /runs/ID/results); this lays them out.
// Text an agent wrote (an answer, a judge's reason) is shown as text, kept apart from the checks.

import { h } from "../dom.js";
import { chip, emptyState, panel, statusChip, table } from "../ui.js";

const jump = (n) => document.getElementById(`cite-${n}`)?.scrollIntoView({ block: "center", behavior: "smooth" });

function prose(parts) {
  return h("p", { class: "prose" }, parts.map((part) => (part.cite ? h("button", { type: "button", class: "cite", "aria-label": `Go to source ${part.cite}`, onclick: () => jump(part.cite) }, `[${part.cite}]`) : part.text)));
}

// -- the answer (or the stored plan) --------------------------------------------------------------

export function answerView(a) {
  if (!a) return emptyState("No answer", "This run did not answer a question.");
  if (a.kind === "plan") {
    return h("div", { class: "stack" }, h("div", { class: "callout info" }, a.note), h("pre", { class: "prose plan-text" }, a.text), a.truncated ? h("p", { class: "help" }, "Cut for display; PLAN.md in the run directory has all of it.") : null);
  }
  const parts = [
    h("div", { class: `callout ${a.tone}`, role: "status" }, h("strong", {}, a.headline), ". ", a.note),
    h("div", { class: "answer-summary" }, prose(a.summary_parts), h("div", { class: "row" }, chip(`confidence: ${a.confidence} (the agent's own)`, ""))),
    ...a.sections.map((s) => h("section", { class: "answer-section" }, h("h3", {}, s.heading), prose(s.parts))),
    sources(a),
  ];
  if (a.changed.length) parts.push(h("div", { class: "callout bad" }, h("strong", {}, "The project changed during this read-only run: "), a.changed.join(", ")));
  if (a.open_questions.length) parts.push(panel("Open questions", h("ul", {}, a.open_questions.map((q) => h("li", {}, q)))));
  return h("div", { class: "stack" }, parts);
}

function sources(a) {
  if (!a.citations.length) return h("p", { class: "help" }, "The answer cites no sources.");
  return panel(`Sources (${a.verified} of ${a.total} verified)`, h("ol", { class: "sources" }, a.citations.map((c) =>
    h("li", { id: `cite-${c.number}`, class: c.ok ? "" : "unverified" },
      h("div", { class: "row" }, h("code", {}, c.kind === "web" ? c.target : c.target), statusChip(c.ok ? "verified" : "unverified", c.ok ? "verified" : c.status.replace(/_/g, " "))),
      c.title ? h("div", { class: "sub" }, c.title) : null,
      c.quote ? h("blockquote", {}, c.quote) : null,
      !c.ok && c.detail ? h("p", { class: "error" }, c.detail) : null))));
}

// -- the escalation ladder ---------------------------------------------------------------------------

export function escalationView(e) {
  if (!e) return emptyState("No escalation ladder", "Only runs on the auto strategy have one.");
  const parts = [h("div", { class: `callout ${e.climbed ? "warn" : "good"}`, role: "status" }, h("strong", {}, e.headline), e.reasons.length ? ` — started there because ${e.reasons.join("; ")}.` : ".")];
  parts.push(h("ol", { class: "ladder" }, e.rungs.map((r, i) =>
    h("li", { class: `rung ${r.tone}`, "aria-label": `Rung ${i + 1}: ${r.rung}` },
      h("div", { class: "row" }, statusChip(r.status === "escalated" ? "failed" : r.status, r.status === "escalated" ? "escalated" : r.status), h("strong", {}, r.label), h("code", {}, r.rung)),
      h("p", { class: "sub" }, r.why),
      r.outcome ? h("p", {}, h("strong", {}, "Outcome: "), r.outcome) : null,
      r.failing_checks.length ? h("p", { class: "row" }, h("strong", {}, "Failing checks: "), r.failing_checks.map((c) => chip(c, "bad"))) : null,
      h("p", { class: "help" }, [r.cost && `cost ${r.cost}`, r.estimate && `estimated ${r.estimate}`, r.seconds != null && `${r.seconds}s`].filter(Boolean).join(" · ")),
      r.handed_on ? h("details", {}, h("summary", {}, "What the next rung was told"), h("pre", { class: "summary" }, r.handed_on)) : null))));
  if (e.stopped) parts.push(h("div", { class: "callout bad" }, h("strong", {}, "Stopped. "), e.stopped));
  return h("div", { class: "stack" }, parts);
}

// -- attempts ------------------------------------------------------------------------------------------

export function attemptsView(a) {
  if (!a) return emptyState("No attempts", "Run with --attempts N (or the ladder's last rung) for best-of-N.");
  const parts = [
    h("div", { class: `callout ${a.method === "none-passed" || a.method === "stopped" ? "bad" : "good"}`, role: "status" }, h("strong", {}, a.headline)),
  ];
  if (a.reason) parts.push(h("p", {}, h("strong", {}, a.method === "judge" ? "The judge's reason: " : "Why: "), a.reason));
  if (a.judge) parts.push(h("p", { class: "help" }, [a.judge.model && `Judge ${a.judge.model}, ${a.judge.cost}.`, a.judge.error && `The judge's answer was not used: ${a.judge.error}`].filter(Boolean).join(" ")));
  parts.push(h("div", { class: "attempt-grid" }, a.attempts.map(attemptCard)));
  if (a.attempts.some((x) => x.scores)) {
    parts.push(panel("The judge's scores", table(["Attempt", "Correctness", "Minimality", "Consistency", "Tests", "Total"], a.attempts.filter((x) => x.scores).map((x) => [`${x.label} (${x.candidate})`, x.scores.correctness, x.scores.minimality, x.scores.consistency, x.scores.tests, x.scores.total]), { numeric: [1, 2, 3, 4, 5] }), { hint: "0 to 5 each; the judge saw anonymous ids only" }));
  }
  parts.push(h("ul", { class: "plain" }, a.notes.map((n) => h("li", {}, n))));
  if (a.stopped) parts.push(h("div", { class: "callout bad" }, h("strong", {}, "Stopped. "), a.stopped));
  return h("div", { class: "stack" }, parts);
}

function attemptCard(x) {
  return h("article", { class: `attempt ${x.chosen ? "chosen" : ""} ${x.tone}`, "aria-label": `Attempt ${x.label}` },
    h("div", { class: "row spread" }, h("strong", {}, x.label, x.candidate ? h("span", { class: "sub" }, `candidate ${x.candidate}`) : null), x.chosen ? chip("chosen", "good", "check") : null),
    statusChip(x.status === "passed" ? "passed" : x.status),
    h("dl", { class: "kv" },
      h("dt", {}, "Varied by"), h("dd", {}, x.vary),
      h("dt", {}, "Required checks"), h("dd", {}, x.checks),
      x.failing_checks.length ? [h("dt", {}, "Failing"), h("dd", {}, x.failing_checks.join(", "))] : null,
      h("dt", {}, "Diff"), h("dd", {}, `${x.files_changed} file(s), ${x.diff_lines} line(s)`),
      h("dt", {}, "Findings"), h("dd", {}, String(x.findings)),
      h("dt", {}, "Cost"), h("dd", {}, x.cost),
      x.patch ? [h("dt", {}, "Patch"), h("dd", {}, h("code", {}, x.patch))] : null),
    x.outcome ? h("p", { class: "help" }, x.outcome) : null);
}
