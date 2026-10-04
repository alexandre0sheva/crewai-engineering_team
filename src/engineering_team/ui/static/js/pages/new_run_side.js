// The right-hand column of the new-run form: team, model, budget, and advanced options. Each
// builder returns { element, collect(into) } where collect writes its fields into the start
// request, so the form needs no knowledge of their inner workings.

import { get } from "../api.js";
import { h, clear, money, tokens } from "../dom.js";
import { chip, panel, tabs } from "../ui.js";

const field = (label, control, help, id) =>
  h("div", { class: "field" }, h("label", { for: id }, label), control, help && h("span", { class: "help", id: `${id}-help` }, help));

const number = (id, placeholder, step = "1") =>
  h("input", { type: "number", id, min: "0", step, placeholder, inputmode: "decimal", "aria-describedby": `${id}-help` });

export function teamPanel(team, defaults) {
  const off = new Set(team.filter((m) => !m.enabled).map((m) => m.key));
  const initial = new Set(off);
  let preset = defaults.team_profile;
  const switcher = tabs(
    [
      { key: "full", label: "Full team" },
      { key: "minimal", label: "Minimal" },
    ],
    { selected: preset, label: "Team preset", onSelect: (key) => (preset = key) },
  );
  const note = h("p", { class: "help counter" }, "Minimal skips the review, DevOps and docs stages.");
  const list = h("ul", { class: "team-list" });
  for (const member of team) {
    const box = h("input", { type: "checkbox", id: `tm-${member.key}`, checked: member.enabled, onchange: () => (box.checked ? off.delete(member.key) : off.add(member.key)) });
    const groups = member.tool_groups;
    list.append(
      h(
        "li",
        {},
        h("label", { class: "check", for: `tm-${member.key}` }, box, h("span", { title: member.role }, h("strong", {}, member.key.replace(/_/g, " ")), h("span", { class: "desc" }, `${member.tier} tier · ${(member.role || "").replace(/ for .*$/, "")}`))),
        h("div", { class: "tags", "aria-label": `Tool groups of ${member.key}` }, groups.slice(0, 6).map((g) => h("span", { class: "tag" }, g)), groups.length > 6 && h("span", { class: "tag" }, `+${groups.length - 6}`)),
      ),
    );
  }
  const missing = team.some((m) => m.notes?.length);
  const body = h("div", { class: "stack" }, switcher.element, note, missing && h("p", { class: "callout info" }, "Browser tools are not installed, so teammates that would use them work without."), list);
  return {
    element: panel("Team", body, { hint: "who may work" }),
    collect(options) {
      if (preset !== defaults.team_profile) options.team_profile = preset;
      const changed = [...off].filter((k) => !initial.has(k));
      if (changed.length) options.disabled_teammates = changed;
    },
  };
}

export function modelPanel(opts) {
  const { defaults } = opts;
  const provider = h("select", { id: "provider" }, opts.providers.map((p) => h("option", { value: p, selected: p === defaults.provider }, p)));
  const profile = h("select", { id: "profile" }, opts.profiles.map((p) => h("option", { value: p, selected: p === defaults.profile }, p)));
  const models = h("div", { "aria-live": "polite" });
  let current = opts.models;
  const paint = () => {
    clear(models);
    for (const m of current) {
      models.append(
        h(
          "div",
          { class: "model-row" },
          h("span", {}, h("strong", {}, m.slot), " · ", m.model || m.error),
          h("span", { class: "mono" }, m.input_per_million == null ? "price unknown" : `$${m.input_per_million} / $${m.output_per_million}`),
        ),
      );
    }
    models.append(h("p", { class: "help mt" }, "USD per million input / output tokens."));
    document.dispatchEvent(new CustomEvent("et-models", { detail: current }));
  };
  const refresh = async () => {
    try {
      current = (await get(`/options?provider=${provider.value}&profile=${profile.value}`)).models;
    } catch (error) {
      current = [{ slot: "error", model: String(error.message) }];
    }
    paint();
  };
  provider.addEventListener("change", refresh);
  profile.addEventListener("change", refresh);
  paint();
  return {
    element: panel(
      "Models",
      h("div", { class: "stack" }, h("div", { class: "grid-2" }, field("Provider", provider, null, "provider"), field("Profile", profile, "smoke is the cheapest.", "profile")), models),
    ),
    collect(options) {
      if (provider.value !== defaults.provider) options.provider = provider.value;
      if (profile.value !== defaults.profile) options.profile = profile.value;
    },
  };
}

export function budgetPanel(opts) {
  const { budget } = opts.defaults;
  const cost = number("b-cost", budget.max_cost_usd ?? "no limit", "0.5");
  const toks = number("b-tokens", budget.max_tokens ?? "no limit", "10000");
  const wall = number("b-wall", budget.max_wall_seconds ? Math.round(budget.max_wall_seconds / 60) : "no limit");
  const calls = number("b-calls", budget.max_tool_calls ?? "no limit");
  const hint = h("p", { class: "callout info", "aria-live": "polite" });
  let models = opts.models;
  document.addEventListener("et-models", (event) => {
    models = event.detail;
    paint();
  });
  function paint() {
    const lead = models.find((m) => m.slot === "lead");
    const dollars = parseFloat(cost.value || budget.max_cost_usd || "");
    if (!lead || lead.input_per_million == null) {
      hint.textContent = "No price is known for this model, so a dollar limit cannot be enforced; use a token limit.";
    } else if (dollars > 0) {
      const blended = (3 * lead.input_per_million + lead.output_per_million) / 4;
      const amount = (dollars / blended) * lead.tokens_per_price_unit;
      hint.textContent = `${money(dollars)} buys about ${tokens(Math.round(amount))} tokens on ${lead.model} (3 input : 1 output). The run stops at the limit.`;
    } else {
      hint.textContent = "No dollar limit set. The run is bounded only by its stage and repair limits; set one to cap spending.";
    }
  }
  cost.addEventListener("input", paint);
  paint();
  return {
    element: panel("Budget", h("div", { class: "stack" }, h("div", { class: "grid-2" }, field("Max cost (USD)", cost, null, "b-cost"), field("Max tokens", toks, null, "b-tokens"), field("Max minutes", wall, null, "b-wall"), field("Max tool calls", calls, null, "b-calls")), hint), { hint: "optional" }),
    collect(options) {
      const limits = {};
      if (cost.value) limits.max_cost_usd = parseFloat(cost.value);
      if (toks.value) limits.max_tokens = parseInt(toks.value, 10);
      if (wall.value) limits.max_wall_seconds = Math.round(parseFloat(wall.value) * 60);
      if (calls.value) limits.max_tool_calls = parseInt(calls.value, 10);
      if (Object.keys(limits).length) options.budget = limits;
    },
  };
}

export function advancedPanel(opts) {
  const { defaults, tools } = opts;
  const parallel = h("input", { type: "number", id: "parallel", min: "1", max: "16", value: defaults.max_parallel_agents });
  const sandbox = h(
    "select",
    { id: "sandbox" },
    opts.sandboxes.map((s) => h("option", { value: s, selected: s === defaults.sandbox, disabled: s === "docker" && !tools.docker.installed }, s === "docker" && !tools.docker.installed ? "docker (not installed)" : s)),
  );
  const strategy = h("select", { id: "strategy" }, h("option", { value: "" }, `default (${defaults.strategy})`), opts.strategies.map((s) => h("option", { value: s }, s)));
  const strategyField = field("Strategy", strategy, "Build new only.", "strategy");
  const isolation = h("select", { id: "isolation" }, h("option", { value: "auto" }, "Automatic (recommended)"), h("option", { value: "worktree" }, "Separate worktree"));
  const dirty = h("input", { type: "checkbox", id: "dirty" });
  const squash = h("input", { type: "checkbox", id: "squash" });
  const noGit = h("input", { type: "checkbox", id: "nogit" });
  const web = h("input", { type: "checkbox", id: "web" });
  const ask = h("input", { type: "checkbox", id: "ask", checked: true });
  const webNote = tools.web.search_providers.length ? `Search provider keys found: ${tools.web.search_providers.join(", ")}.` : "No search provider key is set, so only Fetch URL and Package Info will work.";
  const check = (box, label, desc) => h("label", { class: "check", for: box.id }, box, h("span", {}, label, h("span", { class: "desc" }, desc)));
  const repoOnly = h("div", { class: "stack" }, field("Isolation", isolation, "Where the team works in your repository.", "isolation"), check(dirty, "Allow uncommitted changes", "Start from a dirty tree."), check(squash, "Squash stage commits", "One commit on the team's branch."));
  const newOnly = h("div", { class: "stack" }, strategyField, check(noGit, "No Git repository", "Skip the repository and stage commits."));
  const availability = h("div", { class: "row" }, chip(`browser tools ${tools.browser.installed ? "installed" : "not installed"}`, tools.browser.installed ? "good" : "", tools.browser.installed ? "check" : "alert"), chip(`docker ${tools.docker.installed ? "found" : "not found"}`, tools.docker.installed ? "good" : "", tools.docker.installed ? "check" : "alert"));
  const body = h("div", { class: "stack" }, h("div", { class: "grid-2" }, field("Parallel agents", parallel, null, "parallel"), field("Command sandbox", sandbox, null, "sandbox")), newOnly, repoOnly, check(ask, "Let the team ask me questions", "A question blocks its card until you answer; you are asked on the run page."), check(web, "Allow web tools", webNote), availability);
  const element = h("details", { class: "panel" }, h("summary", {}, "Advanced"), h("div", { class: "panel-body" }, body));
  return {
    element,
    setMode(mode) {
      const repo = mode !== "new";
      newOnly.hidden = repo;
      repoOnly.hidden = !repo || mode === "review";
    },
    collect(options, spec) {
      if (parseInt(parallel.value, 10) !== defaults.max_parallel_agents && parallel.value) options.max_parallel_agents = parseInt(parallel.value, 10);
      if (sandbox.value !== defaults.sandbox) options.sandbox = sandbox.value;
      if (web.checked) options.allow_web = true;
      if (!ask.checked) spec.interactive = false;
      if (spec.mode === "new") {
        if (strategy.value) options.strategy = strategy.value;
        if (noGit.checked) spec.no_git = true;
      } else if (spec.mode !== "review") {
        if (isolation.value === "worktree") spec.worktree = true;
        if (dirty.checked) spec.allow_dirty = true;
        if (squash.checked) spec.squash = true;
      }
    },
  };
}
