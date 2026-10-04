// The run's header: title, status, controls, progress, cost against the budget, and the strip of
// things that need a person (the server lists them; this only shows them).

import { post } from "../api.js";
import { clear, duration, h, icon, money, seconds, tokens } from "../dom.js";
import { announce, button, chip, meter, statusChip, toast } from "../ui.js";

const MODE_LABEL = { new: "Build new", build: "Build new", feature: "Add feature", fix: "Fix bug", maintain: "Maintain", review: "Review" };
const LIMIT = {
  max_cost_usd: ["cost", (v) => money(v)],
  max_tokens: ["tokens", (v) => tokens(Math.round(v))],
  max_wall_seconds: ["time", (v) => duration(v)],
  max_tool_calls: ["tool calls", (v) => String(Math.round(v))],
};
const RESUMABLE = ["failed", "cancelled", "interrupted"];

export function createHead(store, openCard) {
  const runId = store.runId;
  const head = h("div", { class: "run-head" });
  const attention = h("div", { class: "attention-strip", "aria-live": "polite" });
  const elapsed = h("span");
  let answering = null;

  const act = (label, fn, ok) => async () => {
    try {
      const answer = await fn();
      toast(ok || answer.message || `${label} sent`);
      await store.refreshRun();
    } catch (error) {
      toast(error.message, "bad");
    }
  };

  function controls(run) {
    const live = ["running", "starting", "pending"].includes(run.status);
    const out = [];
    if (live) {
      out.push(run.paused ? button("Resume work", { glyph: "play", onclick: act("Unpause", () => post(`/runs/${runId}/unpause`), "Unpaused") }) : button("Pause", { glyph: "pause", onclick: act("Pause", () => post(`/runs/${runId}/pause`), "Pausing at the next safe point") }));
      out.push(button("Cancel run", { kind: "danger", glyph: "stop", onclick: () => confirm("Stop this run? Work done so far is kept and the run can be resumed.") && act("Cancel", () => post(`/runs/${runId}/cancel`))() }));
    }
    if (RESUMABLE.includes(run.status)) out.push(button("Resume run", { kind: "primary", glyph: "play", onclick: act("Resume", () => post(`/runs/${runId}/resume`), "Resuming") }));
    if (run.manifest && run.status !== "starting") out.push(h("a", { class: `btn${store.terminal ? " primary" : ""}`, href: `#/runs/${runId}/results` }, "Results"));
    return out;
  }

  function budgetMeter(run) {
    const b = run.budget;
    if (!b || !b.limits.length) return h("div", { class: "stat" }, h("span", { class: "num" }, "—"), h("span", { class: "cap" }, "no budget set"));
    const worst = [...b.limits].sort((x, y) => y.fraction - x.fraction)[0];
    const [name, fmt] = LIMIT[worst.name] || [worst.name, String];
    const tone = b.state === "exceeded" ? "bad" : b.state === "warning" ? "warn" : "";
    const detail = b.limits.map((l) => `${(LIMIT[l.name] || [l.name])[0]} ${Math.round(l.fraction * 100)}%`).join(" · ");
    return h("div", { class: "budget", title: detail },
      h("div", { class: "row spread" }, h("span", { class: "cap" }, `budget · ${name}`), tone ? chip(b.state === "exceeded" ? "over budget" : "80% used", tone, "alert") : null),
      meter(Math.min(worst.fraction * 100, 100), { tone, label: `Budget used: ${detail}` }),
      h("span", { class: "help" }, `${fmt(worst.used)} of ${fmt(worst.max)} (${Math.round(worst.fraction * 100)}%)`));
  }

  function paintHead() {
    const run = store.run;
    const mode = run.manifest?.mode;
    const p = run.progress;
    const usage = run.usage;
    const cost = usage?.estimated_cost_usd ?? (usage?.known_cost_usd || null);
    elapsed.textContent = run.manifest ? duration(seconds(run.manifest.created, store.terminal ? run.manifest.finished : null)) : "—";
    clear(head).append(
      h("p", { class: "crumbs" }, h("a", { href: "#/runs" }, "Runs"), " / ", run.project || "starting", " / ", runId),
      h("div", { class: "title-row" }, h("h1", {}, `${run.project || "New run"}${mode ? ` · ${MODE_LABEL[mode] || mode}` : ""}`), h("div", { class: "row" }, store.paused && !store.terminal ? statusChip("paused") : statusChip(run.status), run.cancel_requested && !store.terminal ? chip("cancelling", "warn", "alert") : null)),
      h("div", { class: "row" }, controls(run)),
      h("div", { class: "head-stats" },
        h("div", { class: "progress-block" }, p ? [meter(p.overall_percent, { label: "Run progress", tone: run.status === "failed" ? "bad" : run.status === "succeeded" ? "good" : "" }), h("span", { class: "help" }, `${Math.round(p.overall_percent)}% · ${p.cards_done} of ${p.cards_total} cards done`)] : h("span", { class: "help" }, "No task board yet.")),
        h("div", { class: "stat" }, h("span", { class: "num" }, elapsed), h("span", { class: "cap" }, "elapsed")),
        h("div", { class: "stat" }, h("span", { class: "num" }, money(cost)), h("span", { class: "cap" }, usage?.unpriced_models?.length ? "known cost" : "cost")),
        h("div", { class: "stat" }, h("span", { class: "num" }, tokens(usage?.totals?.total_tokens)), h("span", { class: "cap" }, "tokens")),
        budgetMeter(run)),
    );
  }

  function paintAttention() {
    const run = store.run;
    clear(attention);
    if (run.error) attention.append(h("div", { class: "callout bad", role: "alert" }, h("strong", {}, "The run could not start or died. "), h("pre", { class: "mono wrap" }, run.error)));
    const items = run.attention || [];
    if (!items.length) return;
    attention.append(h("h2", { class: "attention-title" }, icon("alert"), `Needs attention (${items.length})`));
    for (const item of items) {
      const action = item.question_id ? button("Answer", { small: true, kind: "primary", onclick: () => ask(run.questions.find((q) => q.id === item.question_id)) }) : item.card_id ? button(`Open ${item.card_id}`, { small: true, onclick: () => openCard(item.card_id) }) : null;
      attention.append(h("div", { class: `callout ${item.severity === "bad" ? "bad" : "warn"} att` }, h("span", {}, h("strong", {}, { question: "Question. ", blocked: "Blocked. ", budget: "Budget. ", check: "Check. " }[item.kind] || ""), item.text), action));
    }
  }

  // The team's question, as a modal form (it is blocking someone).
  function ask(q) {
    if (!q || answering) return;
    const text = h("textarea", { id: "answer-text", rows: 4, required: false });
    const done = async (value) => {
      try {
        await post(`/runs/${runId}/answer`, { question_id: q.id, text: value });
        toast(value ? "Answer sent" : "Declined; the team will assume");
        close();
        store.refreshRun();
      } catch (error) {
        toast(error.message, "bad");
      }
    };
    const dialog = h("dialog", { "aria-labelledby": "ask-title" }, h("form", { method: "dialog", onsubmit: (e) => { e.preventDefault(); if (text.value.trim()) done(text.value); else text.focus(); } },
      h("h2", { id: "ask-title" }, `${q.agent || "The team"} asks`), h("p", {}, q.text), h("div", { class: "field" }, h("label", { for: "answer-text" }, "Your answer"), text),
      h("div", { class: "row" }, h("button", { class: "btn primary", type: "submit" }, "Send answer"), button("Let the team assume", { onclick: () => done("") }), button("Later", { kind: "ghost", onclick: () => close() }))));
    function close() {
      dialog.close();
      dialog.remove();
      answering = null;
    }
    answering = dialog;
    dialog.addEventListener("close", () => { dialog.remove(); answering = null; });
    document.body.append(dialog);
    dialog.showModal();
    text.focus();
  }

  const offs = [store.on("run", () => { paintHead(); paintAttention(); }), store.on("question", (q) => { announce("The team is asking a question"); ask(q); })];
  const tick = setInterval(() => { if (store.run?.manifest && !store.terminal) elapsed.textContent = duration(seconds(store.run.manifest.created)); }, 1000);
  paintHead();
  paintAttention();
  return { head, attention, destroy: () => { offs.forEach((f) => f()); clearInterval(tick); answering?.remove(); } };
}
