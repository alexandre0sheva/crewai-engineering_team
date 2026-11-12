// Screen 1: one box. Say what you want; the page shows what `do` would make of it (the triage
// preview) before anything starts. Overrides are optional; the per-mode form (#/classic) is for
// the presets that have fields of their own.

import { ApiError, get, post, postForm } from "../api.js";
import { bytes, clear, debounce, h } from "../dom.js";
import { announce, button, chip, errorState, kv, loading, panel, toast } from "../ui.js";
import { advancedPanel, budgetPanel, modelPanel, teamPanel } from "./new_run_side.js";
import { triageCard } from "./triage_card.js";

const MAX_FILES = 20;
const FILE_TYPES = [".md", ".markdown", ".txt", ".rst"];
const STRATEGIES = [["", "Let the team decide"], ["auto", "auto: start small, escalate on evidence"], ["team", "team: a lead with teammates"], ["single", "single: one agent"], ["pipeline", "pipeline: every stage"]];

export async function mount(root) {
  const [options, team] = await Promise.all([get("/options"), get("/team").catch(() => [])]);
  const state = { files: [], repoOk: null };
  const side = { team: teamPanel(team, options.defaults), models: modelPanel(options), budget: budgetPanel(options), advanced: advancedPanel(options) };
  side.advanced.setMode("feature"); // the isolation choices; strategy lives in the overrides below

  // -- the one box ---------------------------------------------------------------------------
  const text = h("textarea", { id: "request", class: "big", rows: 9, spellcheck: "true", "aria-describedby": "request-count", placeholder: "Add a --json flag to the export command.\nWhy does the nightly build fail on Windows?\nBuild a CLI that stores and lists notes.\n\nOne or two sentences are enough; acceptance criteria help." });
  const count = h("span", { class: "counter", id: "request-count" });
  const filesList = h("ul", { class: "files", "aria-label": "Attached request files" });
  const picker = h("input", { type: "file", id: "picker", multiple: true, accept: FILE_TYPES.join(","), class: "sr-only", onchange: () => addFiles([...picker.files]) });
  const drop = h("div", { class: "drop", id: "drop" }, h("span", {}, "Drop .md or .txt files here, or"), h("label", { class: "btn small", for: "picker" }, "Choose files"), picker);
  const paintCount = () => {
    count.textContent = `${text.value.length.toLocaleString()} characters`;
    count.classList.toggle("over", text.value.length > options.limits.max_request_chars);
  };

  function paintFiles() {
    clear(filesList);
    state.files.forEach((file, index) => {
      const remove = () => {
        state.files.splice(index, 1);
        paintFiles();
        announce(`${file.name} removed`);
      };
      filesList.append(h("li", {}, h("span", { class: "name" }, file.name), h("span", { class: "counter" }, bytes(file.size)), button("Remove", { kind: "ghost", small: true, onclick: remove })));
    });
  }
  async function addFiles(list) {
    for (const file of list) {
      if (!FILE_TYPES.some((ext) => file.name.toLowerCase().endsWith(ext))) toast(`${file.name}: only ${FILE_TYPES.join(", ")} files are read.`, "bad");
      else if (state.files.length >= MAX_FILES) toast(`At most ${MAX_FILES} files.`, "bad");
      else if (!state.files.some((f) => f.name === file.name && f.size === file.size)) state.files.push(file);
    }
    picker.value = "";
    paintFiles();
    replan();
  }
  for (const name of ["dragenter", "dragover"]) drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const name of ["dragleave", "drop"]) drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files]));
  const requestPanel = panel("What do you want done?", h("div", { class: "stack" }, h("div", { class: "field" }, h("label", { for: "request" }, "Describe it in your own words"), text, h("div", { class: "row spread" }, h("span", { class: "help" }, "A feature, a bug, a question, a review, a whole project: the team works out which."), count)), drop, filesList));

  // -- the project -----------------------------------------------------------------------------
  const repo = h("input", { type: "text", id: "repo", placeholder: "/absolute/path/to/your/project (empty: a new project)", autocomplete: "off", spellcheck: "false", "aria-describedby": "repo-status" });
  const repoStatus = h("div", { id: "repo-status", class: "repo-card", "aria-live": "polite" });
  const inspect = debounce(async () => {
    const path = repo.value.trim();
    state.repoOk = null;
    repo.removeAttribute("aria-invalid");
    clear(repoStatus);
    if (!path) return replan();
    repoStatus.append(loading(2));
    try {
      const found = await get(`/repo/inspect?path=${encodeURIComponent(path)}`);
      if (path !== repo.value.trim()) return;
      state.repoOk = found;
      clear(repoStatus).append(repoCard(found));
    } catch (error) {
      if (path !== repo.value.trim()) return;
      state.repoOk = false;
      repo.setAttribute("aria-invalid", "true");
      clear(repoStatus).append(h("p", { class: "error", role: "alert" }, error.message));
    }
    replan();
  }, 350);
  repo.addEventListener("input", inspect);
  const repoBox = h("div", { class: "field" }, h("label", { for: "repo" }, "Project folder (optional)"), repo, h("span", { class: "help" }, "Leave it empty for a new project. The team never writes to your checkout directly: it works on a branch, a worktree or a copy."), repoStatus);
  if (options.demo) {
    repoBox.append(button("Use the demo sample project", { small: true, onclick: () => { repo.value = options.demo.repo; inspect(); } }));
  }

  // -- overrides -----------------------------------------------------------------------------------
  const intent = h("select", { id: "intent" }, h("option", { value: "" }, "Let the team decide"), options.intents.map((i) => h("option", { value: i.key }, i.label)));
  const strategy = h("select", { id: "strategy-choice" }, STRATEGIES.map(([value, label]) => h("option", { value }, label)));
  const attempts = h("input", { type: "number", id: "attempts", min: "1", max: "8", placeholder: "1" });
  const overrides = h("details", { class: "panel" }, h("summary", {}, "Override what the team decides"), h("div", { class: "panel-body stack" },
    h("div", { class: "field" }, h("label", { for: "intent" }, "Treat it as"), intent, h("span", { class: "help" }, "Forces the kind of work instead of triaging it.")),
    h("div", { class: "field" }, h("label", { for: "strategy-choice" }, "How the team is orchestrated"), strategy),
    h("div", { class: "field" }, h("label", { for: "attempts" }, "Best of N attempts"), attempts, h("span", { class: "help" }, "N verified attempts in separate copies, one chosen blind. Costs about N times more."))));
  for (const control of [intent, strategy]) control.addEventListener("change", () => replan());
  attempts.addEventListener("input", () => replan());

  // -- the live plan ---------------------------------------------------------------------------------
  const card = triageCard({ onRefine: () => replan(true) });
  let ticket = 0;
  let controller = null;
  const planBody = () => {
    const body = { request: text.value.trim() };
    if (repo.value.trim() && state.repoOk) body.repo = repo.value.trim();
    if (intent.value) body.intent = intent.value;
    if (strategy.value) body.strategy = strategy.value;
    if (attempts.value) body.attempts = parseInt(attempts.value, 10);
    const optionsNow = {};
    side.models.collect(optionsNow);
    Object.assign(body, optionsNow.provider ? { provider: optionsNow.provider } : {}, optionsNow.profile ? { profile: optionsNow.profile } : {});
    return body;
  };
  async function plan(refine = false) {
    const mine = ++ticket;
    controller?.abort();
    controller = new AbortController();
    if (!text.value.trim() && !state.files.length) return card.idle();
    if (!text.value.trim()) return card.failed("The plan reads the text box; attached files are read when the run starts.");
    if (state.repoOk === false) return card.failed("That project folder is not usable; see the message under it.");
    card.busy();
    try {
      const body = { ...planBody(), refine };
      const answer = await post("/triage", body, controller.signal);
      if (mine === ticket) card.show(answer, { canRefine: !options.demo });
    } catch (error) {
      if (error.name !== "AbortError" && mine === ticket) card.failed(error.message);
    }
  }
  const replan = debounce((refine) => plan(refine === true), 700);
  text.addEventListener("input", () => { paintCount(); replan(); });

  // -- the form ---------------------------------------------------------------------------------------
  const errorSlot = h("div", { "aria-live": "assertive" });
  const start = button("Start run", { kind: "primary", big: true, type: "submit" });
  const fail = (message, focusOn) => { clear(errorSlot).append(errorState(message)); announce(message); focusOn?.focus(); };

  async function submit(event) {
    event.preventDefault();
    clear(errorSlot);
    const requestText = text.value.trim();
    if (!requestText && !state.files.length) return fail("Write what you want done, or attach a file.", text);
    if (requestText.length > options.limits.max_request_chars) return fail("The request is too long; attach it as a file instead.", text);
    if (repo.value.trim() && state.repoOk === false) return fail("That folder is not usable; see the message under it.", repo);
    const spec = { mode: "do", options: {} };
    if (requestText) spec.request = requestText;
    if (repo.value.trim()) spec.repo = repo.value.trim();
    if (intent.value) spec.intent = intent.value;
    if (strategy.value) spec.options.strategy = strategy.value;
    if (attempts.value) spec.options.attempts = parseInt(attempts.value, 10);
    for (const part of Object.values(side)) part.collect(spec.options, spec);
    start.disabled = true;
    try {
      let answer;
      if (state.files.length) {
        const form = new FormData();
        form.set("spec", JSON.stringify(spec));
        for (const file of state.files) form.append("request_files", file, file.name);
        answer = await postForm("/runs", form);
      } else {
        answer = await post("/runs", spec);
      }
      location.hash = `#/runs/${answer.run_id}`;
    } catch (error) {
      start.disabled = false;
      fail(error instanceof ApiError ? error.message : String(error));
    }
  }

  const columns = h("div", { class: "layout-2" }, h("div", { class: "stack" }, requestPanel, panel("Project", repoBox)), h("div", { class: "stack" }, card.element, overrides, side.team.element, side.models.element, side.budget.element, side.advanced.element));
  const actions = h("div", { class: "sticky-actions" }, errorSlot, h("div", { class: "row" }, start, h("span", { class: "help" }, "The run starts in its own process; closing this page does not stop it.")));
  root.append(h("div", { class: "reveal" },
    h("div", { class: "page-head" }, h("p", { class: "eyebrow" }, "Brief the team"), h("h1", {}, "New run"), h("p", { class: "lede" }, "Say what you want. The plan below is what the team would do; start it when it looks right. ", h("a", { href: "#/classic" }, "Need a reproduction command, a trace or a maintenance task? Use the per-mode form."))),
    h("form", { novalidate: true, onsubmit: submit }, columns, actions)));
  paintCount();
  return () => { inspect.cancel(); replan.cancel(); controller?.abort(); };
}

function repoCard(found) {
  const p = found.profile;
  const langs = p.languages.slice(0, 3).map((l) => `${l.language} ${l.lines.toLocaleString()} lines`).join(" · ");
  const stack = p.stacks.map((s) => [s.language, s.manager, s.test].filter(Boolean).join(" / ")).join("; ");
  const git = p.git;
  const gitText = !git.available ? "Git is not installed" : git.is_repo ? `branch ${git.branch}${git.dirty ? `, ${git.changed_files + git.untracked_files} uncommitted` : ", clean"}` : "not a Git repository";
  return h("div", { class: "callout" }, h("div", { class: "row" }, chip(p.name, "good", "check"), chip(gitText, git.dirty || !git.is_repo ? "warn" : "", "branch")), h("div", { class: "mt" }, kv([["Languages", langs || "none recognised"], ["Stack", stack || "none detected"], ["Files", `${p.files.toLocaleString()}${p.truncated ? "+" : ""}`], ["Tests", p.test_files ? `${p.test_files} test file(s)` : "none found"], ["Isolation", `${found.isolation.mode}: ${found.isolation.why}`]])));
}
