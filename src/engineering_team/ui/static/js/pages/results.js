// Screen 3: what a run produced. Verdict and summary, the criteria coverage matrix, checks,
// the diff, the project's files, screenshots, downloads, and how to take the change.

import { blob, get } from "../api.js";
import { clear, download, duration, h, money, tokens } from "../dom.js";
import { button, chip, copyCommand, emptyState, errorState, kv, loading, panel, statusChip, table, tabs, toast } from "../ui.js";
import { diffView, filesView, screenshotsView } from "./results_changes.js";

const SEVERITY_TONE = { critical: "bad", high: "bad", medium: "warn", low: "info", info: "" };

export async function mount(root, [runId]) {
  root.append(loading(5));
  const report = await get(`/runs/${runId}/results`);
  const cleanups = [];

  const sections = [
    { key: "summary", label: "Summary", build: () => summary(report) },
    { key: "criteria", label: `Criteria (${report.coverage.length})`, build: () => criteria(report) },
    { key: "checks", label: `Checks (${report.checks.length})`, build: () => checks(report) },
    { key: "changes", label: report.diff ? `Changes (${report.diff.files.length})` : "Changes", build: () => diffView(report) },
    { key: "files", label: "Files", build: () => filesView(runId) },
    { key: "shots", label: `Screenshots (${report.screenshots.length})`, build: () => {
      const view = screenshotsView(runId, report.screenshots);
      if (view.cleanup) cleanups.push(view.cleanup);
      return view;
    } },
    { key: "merge", label: "Take the change", build: () => mergeView(report) },
  ];
  const body = h("div", { id: "result-panel", role: "tabpanel" });
  const strip = tabs(sections.map((s) => ({ key: s.key, label: s.label, controls: "result-panel" })), {
    selected: "summary",
    label: "Result sections",
    className: "tabs",
    onSelect: (key) => {
      body.setAttribute("aria-labelledby", `tab-${key}`);
      clear(body).append(sections.find((s) => s.key === key).build());
    },
  });

  const banner = report.banner;
  const exportPatch = async () => {
    try {
      const answer = await get(`/runs/${runId}/diff`);
      download(`${runId}.patch`, new Blob([answer.diff], { type: "text/plain" }));
      if (answer.truncated) toast("The patch was cut at 2 MB; use the CLI export for all of it.", "bad");
    } catch (error) {
      toast(error.message, "bad");
    }
  };
  const getReport = async () => {
    try {
      download(`${runId}-report.html`, await blob(`/runs/${runId}/report?format=html`));
    } catch (error) {
      toast(error.message, "bad");
    }
  };
  const cost = report.usage?.estimated_cost_usd ?? report.usage?.known_cost_usd;
  clear(root).append(
    h("div", { class: "stack" }, h("p", { class: "crumbs" }, h("a", { href: "#/runs" }, "Runs"), " / ", h("a", { href: `#/runs/${runId}` }, runId), " / results"),
      h("section", { class: `verdict ${banner.tone}`, "aria-labelledby": "verdict-title" },
        h("div", { class: "row spread" }, h("div", { class: "row" }, statusChip(report.status), report.verdict && chip(`verdict: ${report.verdict}`, banner.tone)), h("div", { class: "row" }, report.diff && button("Export patch", { glyph: "download", onclick: exportPatch }), button("Download report", { glyph: "download", onclick: getReport }), h("a", { class: "btn", href: `#/runs/${runId}` }, "Run page"))),
        h("h1", { id: "verdict-title" }, `${banner.label}: ${report.project}`),
        banner.reasons.length ? h("ul", {}, banner.reasons.map((r) => h("li", {}, r))) : null,
        h("div", { class: "stats mt" }, stat(duration(report.duration_seconds), "duration"), stat(money(cost), "cost"), stat(tokens(report.usage?.totals?.total_tokens), "tokens"), stat(String(report.tool_calls), "tool calls"), report.diff ? stat(`+${report.diff.added} −${report.diff.removed}`, "lines") : null)),
      strip.element, body),
  );
  return () => cleanups.forEach((fn) => fn());
}

const stat = (value, label) => h("div", { class: "stat" }, h("span", { class: "num" }, value), h("span", { class: "cap" }, label));

function summary(report) {
  const parts = [];
  if (report.warnings.length) parts.push(h("div", { class: "stack" }, report.warnings.map((w) => h("div", { class: `callout ${w.tone === "info" ? "info" : w.tone}` }, w.text))));
  if (report.request) parts.push(panel("Request", h("pre", { class: "summary" }, report.request)));
  const said = Object.entries(report.summaries);
  if (said.length) parts.push(panel("What the team said", h("div", { class: "stack" }, said.map(([stage, text]) => h("div", {}, h("strong", {}, stage), h("pre", { class: "summary" }, text))), h("p", { class: "help" }, "The team's own words: context, not evidence. The checks are the evidence."))));
  if (report.findings.length) parts.push(panel(`Review findings (${report.findings.length})`, table(["Severity", "Finding", "Where"], report.findings.map((f) => [chip(f.severity, SEVERITY_TONE[f.severity] || ""), h("span", {}, f.summary, f.suggested_fix ? h("span", { class: "sub" }, `Suggested: ${f.suggested_fix}`) : null), h("code", {}, f.file ? `${f.file}${f.line ? `:${f.line}` : ""}` : "project-wide")]))));
  if (report.agents.length) parts.push(panel("Who did what", table(["Teammate", "Tool calls", "Failed", "Cards"], report.agents.map((a) => [a.agent, a.tool_calls, a.failed_calls, a.cards.join(", ") || "—"]), { numeric: [1, 2] })));
  return h("div", { class: "stack" }, parts.length ? parts : emptyState("Nothing to summarise", "This run recorded no summary yet."));
}

function criteria(report) {
  if (!report.coverage.length) return emptyState("No acceptance criteria", "The run's spec listed none, or the spec stage did not run.");
  const open = report.coverage.filter((c) => c.status === "unverified").length;
  return h("div", { class: "stack" },
    h("div", { class: `callout ${open ? "warn" : "good"}`, role: "status" }, open ? `${open} of ${report.coverage.length} criteria are not verified by a passing check and need a human.` : "Every criterion is backed by a passing check."),
    table(["Criterion", "Status", "Checks", "Note"], report.coverage.map((c) => [h("span", {}, h("strong", {}, c.id), h("span", { class: "sub" }, c.text)), statusChip(c.status), c.checks.length ? c.checks.map((id) => h("code", {}, `${id} `)) : "—", c.note || ""])));
}

function checks(report) {
  if (!report.checks.length) return emptyState("No checks recorded", "Checks run by the controller (tests, lint, build) appear here with their evidence.");
  return h("div", { class: "stack" }, report.checks.map((c) =>
    h("details", { class: "diff-file", open: c.status === "failed" },
      h("summary", {}, statusChip(c.status), h("span", { class: "grow" }, c.name || c.id), c.required ? chip("required") : null, h("span", { class: "counter" }, duration(c.duration))),
      h("div", { class: "panel-body stack" }, kv([["Summary", c.summary], ["Command", c.command && h("code", {}, c.command)], ["Exit code", c.exit_code], ["Hint", c.hint], ["Criteria", c.criteria_ids.join(", ")]]), c.log_tail ? h("pre", { class: "summary" }, c.log_tail) : null))));
}

function mergeView(report) {
  const merge = report.merge;
  if (!merge) {
    return h("div", { class: "stack" }, h("div", { class: "callout info" }, "This run built a new project in its own folder; there is nothing to merge."), kv([["Project folder", h("code", {}, report.environment?.find(([k]) => /workspace/i.test(k))?.[1] || "see the run's report")], ["Look at it", "Use the Files tab, or open the folder in your editor."]]));
  }
  return h("div", { class: "stack" },
    h("div", { class: "callout" }, h("strong", {}, "Nothing was pushed or merged for you. "), `The team worked in ${merge.mode} mode${merge.branch ? ` on branch ${merge.branch}` : ""}, starting from ${merge.base_commit ? merge.base_commit.slice(0, 12) : "your branch"}.`),
    h("ol", { class: "steps" }, merge.steps.map((step) => h("li", {}, h("div", {}, h("strong", {}, step.title), step.commands.map(copyCommand))))),
    merge.notes?.length ? h("div", { class: "callout info" }, merge.notes.join(" ")) : null);
}
