// Screen 4: every run, newest first, with a search box, a status filter, and a mini progress bar.

import { get } from "../api.js";
import { h, clear, duration, money, seconds, when, debounce } from "../dom.js";
import { button, emptyState, errorState, loading, meter, statusChip } from "../ui.js";

const FILTERS = [
  ["all", "All"],
  ["active", "Active"],
  ["succeeded", "Succeeded"],
  ["failed", "Failed"],
  ["cancelled", "Cancelled"],
];
const ACTIVE = ["running", "starting", "pending"];
const MODE_LABEL = { new: "Build new", build: "Build new", feature: "Add feature", fix: "Fix bug", maintain: "Maintain", review: "Review" };

export async function mount(root) {
  let runs = [];
  let filter = "all";
  let query = "";
  const out = h("div", { "aria-live": "polite" });
  const search = h("input", { type: "search", id: "q", placeholder: "Search id, project, mode", "aria-label": "Search runs" });
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Filter by status" });
  const count = h("p", { class: "help", role: "status" });

  function paintFilters() {
    clear(seg).append(
      ...FILTERS.map(([key, label]) => {
        const radio = h("input", { type: "radio", name: "status", value: key, checked: key === filter, onchange: () => { filter = key; paint(); } });
        return h("label", {}, radio, h("span", {}, label));
      }),
    );
  }

  const matches = (run) => {
    if (filter === "active" ? !ACTIVE.includes(run.status) : filter !== "all" && run.status !== filter) return false;
    const hay = `${run.run_id} ${run.project} ${run.manifest?.mode || ""}`.toLowerCase();
    return !query || hay.includes(query.toLowerCase());
  };

  function paint() {
    const shown = runs.filter(matches);
    count.textContent = `${shown.length} of ${runs.length} run${runs.length === 1 ? "" : "s"}`;
    clear(out);
    if (!runs.length) {
      out.append(emptyState("No runs yet", "Start one and it will show up here, with its progress and cost.", h("a", { class: "btn primary", href: "#/" }, "Start a run")));
      return;
    }
    if (!shown.length) {
      out.append(emptyState("Nothing matches", "Try another search or filter.", button("Clear filters", { onclick: () => { filter = "all"; query = ""; search.value = ""; paintFilters(); paint(); } })));
      return;
    }
    out.append(
      h("div", { class: "panel table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Run", "Mode", "Status", "Progress", "Cost", "Started", "Took"].map((t, i) => h("th", { scope: "col", class: i === 4 ? "num" : "" }, t)))), h("tbody", {}, shown.map(row)))),
    );
  }

  function row(run) {
    const p = run.progress;
    const cost = run.usage?.estimated_cost_usd ?? (run.usage?.known_cost_usd || null);
    const m = run.manifest;
    return h(
      "tr",
      {},
      h("td", {}, h("a", { class: "run-link", href: `#/runs/${run.run_id}` }, run.project || "(starting)"), h("span", { class: "sub mono" }, run.run_id)),
      h("td", {}, MODE_LABEL[m?.mode] || m?.mode || "—"),
      h("td", {}, statusChip(run.status)),
      h("td", { class: "mini" }, meter(p?.overall_percent ?? (run.status === "succeeded" ? 100 : 0), { thin: true, label: `${run.run_id} progress`, tone: run.status === "failed" ? "bad" : run.status === "succeeded" ? "good" : "" }), h("span", { class: "sub" }, p ? `${p.cards_done}/${p.cards_total} cards` : "no board")),
      h("td", { class: "num" }, money(cost)),
      h("td", {}, when(m?.created)),
      h("td", {}, m ? duration(seconds(m.created, m.finished)) : "—"),
    );
  }

  async function load() {
    try {
      runs = (await get("/runs?limit=500")).sort((a, b) => b.run_id.localeCompare(a.run_id));
      paint();
    } catch (error) {
      clear(out).append(errorState(error, load));
    }
  }

  search.addEventListener("input", debounce(() => { query = search.value.trim(); paint(); }, 150));
  paintFilters();
  root.append(h("div", { class: "reveal" }, h("div", { class: "page-head" }, h("p", { class: "eyebrow" }, "Logbook"), h("h1", {}, "Runs"), h("p", { class: "lede" }, "Every run this workspace has seen, including ones started from the terminal.")), h("div", { class: "toolbar" }, search, seg, count, button("Refresh", { glyph: "refresh", small: true, onclick: load })), out));
  out.append(loading(4));
  await load();
  const timer = setInterval(() => runs.some((r) => ACTIVE.includes(r.status)) && load(), 4000);
  return () => clearInterval(timer);
}
