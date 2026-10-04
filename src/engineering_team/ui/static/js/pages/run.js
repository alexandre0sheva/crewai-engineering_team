// Screen 2: one run, live. Header with status, progress and cost; the stage list; what needs
// attention (questions, blocked cards); the raw event stream; cancel, pause and resume. The live
// dashboard (board, roster, feed) builds on this same route.

import { get, post, streamEvents } from "../api.js";
import { h, clear, clock, duration, icon, money, seconds, tokens } from "../dom.js";
import { announce, button, chip, meter, panel, statusChip, toast } from "../ui.js";

const TERMINAL = ["succeeded", "failed", "cancelled"];
const RESUMABLE = ["failed", "cancelled", "interrupted"];
const MAX_LINES = 1500;
const MODE_LABEL = { new: "Build new", feature: "Add feature", fix: "Fix bug", maintain: "Maintain", review: "Review", build: "Build new" };

export async function mount(root, [runId]) {
  const abort = new AbortController();
  let view = await get(`/runs/${runId}`);
  const shell = h("div", { class: "stack" });
  root.append(shell);
  const timers = [];

  const head = h("div", { class: "run-head reveal" });
  const attention = h("div", { class: "stack", "aria-live": "polite" });
  const stages = h("div");
  const stats = h("div");
  const log = h("div", { class: "log", tabindex: 0, role: "log", "aria-label": "Run events", "aria-live": "off" });
  const jump = button("Jump to latest", { small: true, glyph: "chevron", onclick: () => { log.scrollTop = log.scrollHeight; } });
  jump.hidden = true;
  const streamState = h("span", { class: "chip" }, "connecting");
  shell.append(head, attention, h("div", { class: "layout-2" }, h("div", { class: "stack" }, panel("Events", h("div", { class: "stack" }, log, h("div", { class: "row spread" }, streamState, jump)), { hint: "raw stream" })), h("div", { class: "stack" }, panel("Progress", stats), panel("Stages", stages))));

  let lastStatus = view.status;
  function paint() {
    paintHead();
    paintAttention();
    paintStats();
    paintStages();
    if (view.status !== lastStatus) {
      announce(`Run ${view.status}`);
      lastStatus = view.status;
    }
  }

  const act = (label, fn, ok) => async () => {
    try {
      const answer = await fn();
      toast(ok || answer.message || `${label} sent`);
      await refresh();
    } catch (error) {
      toast(error.message, "bad");
    }
  };

  function paintHead() {
    const running = ["running", "starting", "pending"].includes(view.status);
    const controls = [];
    if (running) {
      controls.push(view.paused ? button("Unpause", { glyph: "play", onclick: act("Unpause", () => post(`/runs/${runId}/unpause`)) }) : button("Pause", { glyph: "pause", onclick: act("Pause", () => post(`/runs/${runId}/pause`)) }));
      controls.push(button("Cancel run", { kind: "danger", glyph: "stop", onclick: () => confirm("Stop this run? Work done so far is kept and the run can be resumed.") && act("Cancel", () => post(`/runs/${runId}/cancel`))() }));
    }
    if (RESUMABLE.includes(view.status)) controls.push(button("Resume", { kind: "primary", glyph: "play", onclick: act("Resume", () => post(`/runs/${runId}/resume`), "Resuming") }));
    if (view.manifest && view.status !== "starting") controls.push(h("a", { class: `btn${TERMINAL.includes(view.status) ? " primary" : ""}`, href: `#/runs/${runId}/results` }, "Results"));
    const mode = view.manifest?.mode;
    clear(head).append(
      h("p", { class: "crumbs" }, h("a", { href: "#/runs" }, "Runs"), " / ", view.project || "starting", " / ", runId),
      h("div", { class: "title-row" }, h("h1", {}, `${view.project || "New run"}${mode ? ` · ${MODE_LABEL[mode] || mode}` : ""}`), h("div", { class: "row" }, view.paused ? statusChip("paused") : statusChip(view.status), view.cancel_requested && !TERMINAL.includes(view.status) ? chip("cancelling", "warn", "alert") : null)),
      h("div", { class: "row" }, controls),
    );
  }

  function paintAttention() {
    clear(attention);
    if (view.error) attention.append(h("div", { class: "callout bad attention bad", role: "alert" }, h("strong", {}, "The run could not start or died. "), h("pre", { class: "mono wrap" }, view.error)));
    for (const q of view.questions || []) attention.append(questionCard(q));
    for (const b of view.progress?.blocked || []) attention.append(h("div", { class: "callout warn attention" }, h("strong", {}, `${b.id} is blocked. `), `${b.title}${b.reason ? `: ${b.reason}` : ""}`));
    const budget = view.manifest?.summary?.budget_status;
    if (budget && ["warning", "exceeded"].includes(budget.state)) attention.append(h("div", { class: `callout ${budget.state === "exceeded" ? "bad" : "warn"} attention` }, h("strong", {}, `Budget ${budget.state}. `), budget.exceeded || budget.notes.join(" ")));
  }

  function questionCard(q) {
    const input = h("textarea", { id: `answer-${q.id}`, rows: 3, "aria-label": `Answer to ${q.id}` });
    const send = (text) => async () => {
      try {
        await post(`/runs/${runId}/answer`, { question_id: q.id, text });
        toast(text ? "Answer sent" : "Declined; the team will assume");
        await refresh();
      } catch (error) {
        toast(error.message, "bad");
      }
    };
    return h("section", { class: "panel attention" }, h("div", { class: "panel-body stack" }, h("h2", {}, `${q.agent || "The team"} asks`), h("p", {}, q.text), input, h("div", { class: "row" }, button("Send answer", { kind: "primary", onclick: () => (input.value.trim() ? send(input.value)() : input.focus()) }), button("Let the team assume", { onclick: send("") }))));
  }

  const tick = h("span");
  function paintStats() {
    const p = view.progress;
    const usage = view.usage;
    const cost = usage?.estimated_cost_usd ?? (usage?.known_cost_usd || null);
    const end = view.manifest?.finished;
    tick.textContent = view.manifest ? duration(seconds(view.manifest.created, end)) : "—";
    clear(stats).append(
      h("div", { class: "stack" }, p ? [meter(p.overall_percent, { label: "Run progress", tone: view.status === "failed" ? "bad" : view.status === "succeeded" ? "good" : "" }), h("p", { class: "help" }, `${Math.round(p.overall_percent)}% · ${p.cards_done} of ${p.cards_total} cards done`)] : h("p", { class: "help" }, "No task board yet."), h("div", { class: "stats" }, h("div", { class: "stat" }, h("span", { class: "num" }, tick), h("span", { class: "cap" }, "elapsed")), h("div", { class: "stat" }, h("span", { class: "num" }, money(cost)), h("span", { class: "cap" }, usage?.unpriced_models?.length ? "known cost" : "cost")), h("div", { class: "stat" }, h("span", { class: "num" }, tokens(usage?.totals?.total_tokens)), h("span", { class: "cap" }, "tokens")), h("div", { class: "stat" }, h("span", { class: "num" }, usage?.tool_calls ?? 0), h("span", { class: "cap" }, "tool calls")))),
    );
  }

  function paintStages() {
    const list = view.manifest?.stages || [];
    if (!list.length) return void clear(stages).append(h("p", { class: "help" }, view.status === "starting" ? "Starting the process…" : "No stages recorded yet."));
    const rows = list.map((s) => {
      const tone = s.status === "succeeded" ? "done" : s.status === "running" ? "run" : ["failed", "cancelled", "interrupted"].includes(s.status) ? "bad" : "";
      const glyph = { succeeded: "check", running: "spinner", failed: "x", cancelled: "ban", interrupted: "alert" }[s.status] || "circle";
      const sub = [s.status, s.attempts > 1 ? `${s.attempts} attempts` : "", s.started ? duration(seconds(s.started, s.finished)) : "", s.detail].filter(Boolean).join(" · ");
      return h("li", { class: tone }, h("span", { class: "dot" }, icon(glyph)), h("span", {}, h("span", { class: "name" }, s.name), h("span", { class: "sub" }, ` ${sub}`)));
    });
    clear(stages).append(h("ul", { class: "stage-list" }, rows));
  }

  // -- the raw event stream --------------------------------------------------------------------------
  const queue = [];
  let frame = 0;
  let stick = true;
  log.addEventListener("scroll", () => {
    stick = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
    jump.hidden = stick;
  });
  function flush() {
    frame = 0;
    const lines = queue.splice(0);
    log.append(...lines);
    while (log.childNodes.length > MAX_LINES) log.firstChild.remove();
    if (stick) log.scrollTop = log.scrollHeight;
  }
  function line(event) {
    const d = event.data || {};
    let text;
    let tone = "";
    if (event.type === "tool.call") {
      text = `${event.agent || "?"} ${d.tool}${d.args ? ` ${typeof d.args === "string" ? d.args : JSON.stringify(d.args)}` : ""} ${d.ok === false ? "FAILED" : "ok"}`;
      tone = d.ok === false ? "bad" : "";
    } else {
      const rest = Object.entries(d).filter(([k]) => k !== "card").map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ");
      text = `${event.type}${event.stage ? ` [${event.stage}]` : ""} ${rest}`;
      if (/fail|error/.test(event.type) || d.status === "failed") tone = "bad";
      else if (/finished|succeeded|done/.test(event.type) && d.status !== "failed") tone = "good";
    }
    const row = h("div", {}, h("span", { class: "t" }, `${clock(event.ts)} `), h("span", { class: tone || "ev" }, text.slice(0, 400)));
    queue.push(row);
    frame ||= requestAnimationFrame(flush);
  }

  // -- keeping the summary current -------------------------------------------------------------------
  let refreshing = null;
  let again = false;
  async function refresh() {
    if (refreshing) {
      again = true;
      return refreshing;
    }
    refreshing = (async () => {
      try {
        view = await get(`/runs/${runId}`);
        paint();
      } catch (error) {
        toast(error.message, "bad");
      }
    })();
    await refreshing;
    refreshing = null;
    if (again) {
      again = false;
      await refresh();
    }
  }
  let pending = 0;
  const soon = () => {
    pending ||= setTimeout(() => {
      pending = 0;
      refresh();
    }, 900);
  };

  paint();
  timers.push(setInterval(() => view.manifest && !view.manifest.finished && paintStats(), 1000));
  timers.push(setInterval(() => !TERMINAL.includes(view.status) && refresh(), 4000));
  streamEvents(runId, {
    signal: abort.signal,
    onEvent: (event) => {
      line(event);
      if (/^(stage|run|board|question|note|budget|team|check)/.test(event.type) || event.type.includes("moved")) soon();
    },
    onStatus: (state) => {
      streamState.textContent = state === "live" ? "streaming" : "reconnecting…";
    },
    onEnd: async () => {
      streamState.textContent = "run is over";
      await refresh();
    },
  });
  return () => {
    abort.abort();
    timers.forEach(clearInterval);
    clearTimeout(pending);
  };
}
