// Screen 1: describe the work and start a run.

import { get, post, postForm, ApiError } from "../api.js";
import { h, clear, bytes, debounce } from "../dom.js";
import { announce, button, chip, errorState, kv, loading, panel, tabs, toast } from "../ui.js";
import { advancedPanel, budgetPanel, modelPanel, teamPanel } from "./new_run_side.js";

const MAX_FILES = 20;
const FILE_TYPES = [".md", ".markdown", ".txt", ".rst"];
const MAINTAIN_TASKS = [
  ["add-tests", "Add tests"],
  ["refactor", "Refactor"],
  ["upgrade-deps", "Upgrade dependencies"],
  ["docs", "Write docs"],
  ["security-audit", "Security audit"],
  ["custom", "Custom (describe it below)"],
];
const COPY = {
  new: { label: "What should the team build?", help: "Requirements, in your own words. Markdown works: headings, lists and acceptance criteria are all read." },
  feature: { label: "Describe the feature", help: "What should change and how you will know it works." },
  fix: { label: "Describe the bug", help: "What happens, what should happen, and how to see it. A stack trace helps." },
  maintain: { label: "Goal (optional)", help: "What the maintenance task should achieve, if the preset needs it." },
};

export async function mount(root) {
  const [options, team] = await Promise.all([get("/options"), get("/team").catch(() => [])]);
  const state = { mode: "new", files: [] };
  const side = {
    team: teamPanel(team, options.defaults),
    models: modelPanel(options),
    budget: budgetPanel(options),
    advanced: advancedPanel(options),
  };

  // -- request ---------------------------------------------------------------------------------
  const text = h("textarea", {
    id: "request",
    class: "big",
    rows: 12,
    spellcheck: "true",
    "aria-describedby": "request-help request-count",
    placeholder: "# My app\n\nA small tool that…\n\n## Acceptance criteria\n- …",
  });
  const count = h("span", { class: "counter", id: "request-count" });
  const requestHelp = h("span", { class: "help", id: "request-help" });
  const filesList = h("ul", { class: "files", "aria-label": "Attached request files" });
  const picker = h("input", { type: "file", id: "picker", multiple: true, accept: FILE_TYPES.join(","), class: "sr-only", onchange: () => addFiles([...picker.files]) });
  const drop = h("div", { class: "drop", id: "drop" }, h("span", {}, "Drop .md or .txt files here, or"), h("label", { class: "btn small", for: "picker" }, "Choose files"), picker);

  const paintCount = () => {
    count.textContent = `${text.value.length.toLocaleString()} characters`;
    count.classList.toggle("over", text.value.length > options.limits.max_request_chars);
  };
  text.addEventListener("input", paintCount);

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
  function addFiles(list) {
    for (const file of list) {
      if (!FILE_TYPES.some((ext) => file.name.toLowerCase().endsWith(ext))) {
        toast(`${file.name}: only ${FILE_TYPES.join(", ")} files are read.`, "bad");
      } else if (state.files.length >= MAX_FILES) {
        toast(`At most ${MAX_FILES} files.`, "bad");
      } else if (!state.files.some((f) => f.name === file.name && f.size === file.size)) {
        state.files.push(file);
      }
    }
    picker.value = "";
    paintFiles();
  }
  for (const name of ["dragenter", "dragover"]) {
    drop.addEventListener(name, (e) => {
      e.preventDefault();
      drop.classList.add("over");
    });
  }
  for (const name of ["dragleave", "drop"]) {
    drop.addEventListener(name, (e) => {
      e.preventDefault();
      drop.classList.remove("over");
    });
  }
  drop.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files]));

  const template = button("Insert a request template", {
    small: true,
    onclick: () => {
      if (text.value.trim() && !confirm("Replace what you have written with the template?")) return;
      text.value = options.templates[state.mode] || "";
      paintCount();
      text.focus();
    },
  });
  const requestLabel = h("label", { for: "request" });
  const requestBox = h("div", { class: "stack" }, h("div", { class: "field" }, requestLabel, text, h("div", { class: "row spread" }, requestHelp, count)), drop, filesList, h("div", { class: "row" }, template));
  const requestPanel = panel("Request", requestBox);

  // -- repository ------------------------------------------------------------------------------
  const repo = h("input", { type: "text", id: "repo", placeholder: "/absolute/path/to/your/project", autocomplete: "off", spellcheck: "false", "aria-describedby": "repo-status" });
  const repoStatus = h("div", { id: "repo-status", class: "repo-card", "aria-live": "polite" });
  let repoOk = null;
  const inspect = debounce(async () => {
    const path = repo.value.trim();
    repoOk = null;
    repo.removeAttribute("aria-invalid");
    clear(repoStatus);
    if (!path) return;
    repoStatus.append(loading(2));
    try {
      const found = await get(`/repo/inspect?path=${encodeURIComponent(path)}`);
      if (path !== repo.value.trim()) return;
      repoOk = found;
      clear(repoStatus).append(repoCard(found));
    } catch (error) {
      if (path !== repo.value.trim()) return;
      repoOk = false;
      repo.setAttribute("aria-invalid", "true");
      clear(repoStatus).append(h("p", { class: "error", role: "alert" }, error.message));
    }
  }, 350);
  repo.addEventListener("input", inspect);
  const repoBox = h("div", { class: "field" }, h("label", { for: "repo" }, "Project folder"), repo, h("span", { class: "help" }, "The folder of the project on this machine. The team never writes to your checkout directly: it works on a branch, a worktree or a copy."), repoStatus);
  if (options.demo) {
    repoBox.append(
      button("Use the demo sample project", {
        small: true,
        onclick: () => {
          repo.value = options.demo.repo;
          inspect();
        },
      }),
    );
  }

  // -- mode-specific fields ----------------------------------------------------------------------
  const name = h("input", { type: "text", id: "project", placeholder: "mvp-app", autocomplete: "off" });
  const nameBox = h("div", { class: "field" }, h("label", { for: "project" }, "Project name"), name, h("span", { class: "help" }, "Names the workspace folder. Leave blank for the default."));
  const repro = h("input", { type: "text", id: "repro", placeholder: "pytest tests/test_x.py::test_y", autocomplete: "off" });
  const trace = h("textarea", { id: "trace", rows: 5, placeholder: "Paste a stack trace or log excerpt" });
  const unrepro = h("input", { type: "checkbox", id: "unrepro" });
  const fixBox = h(
    "div",
    { class: "stack" },
    h("div", { class: "field" }, h("label", { for: "repro" }, "Command that shows the bug (optional)"), repro),
    h("div", { class: "field" }, h("label", { for: "trace" }, "Stack trace or log (optional)"), trace),
    h("label", { class: "check", for: "unrepro" }, unrepro, h("span", {}, "Fix even if the bug cannot be reproduced", h("span", { class: "desc" }, "Otherwise the team stops and asks questions."))),
  );
  const task = h("select", { id: "task" }, MAINTAIN_TASKS.map(([key, label]) => h("option", { value: key }, label)));
  get("/recipes")
    .then((list) => {
      const own = list.filter((r) => !["bundled", "builtin"].includes(r.source) && !MAINTAIN_TASKS.some(([k]) => k === r.name));
      for (const r of own) task.append(h("option", { value: r.name }, `${r.name} (${r.source})`));
    })
    .catch(() => {});
  const fixFindings = h("input", { type: "checkbox", id: "fixf" });
  const maintainBox = h(
    "div",
    { class: "stack" },
    h("div", { class: "field" }, h("label", { for: "task" }, "Task"), task),
    h("label", { class: "check", for: "fixf" }, fixFindings, h("span", {}, "Also fix what a security audit finds", h("span", { class: "desc" }, "Only applies to security-audit."))),
  );
  const base = h("input", { type: "text", id: "base", placeholder: "main (default: the working tree)", autocomplete: "off" });
  const focus = h("input", { type: "text", id: "focus", placeholder: "security, error handling…", autocomplete: "off" });
  const reviewBox = h("div", { class: "grid-2" }, h("div", { class: "field" }, h("label", { for: "base" }, "Compare against"), base), h("div", { class: "field" }, h("label", { for: "focus" }, "Focus"), focus));
  const modeBody = h("div", { class: "stack", id: "mode-panel", role: "tabpanel" }, repoBox, fixBox, maintainBox, reviewBox, nameBox);

  // -- the form --------------------------------------------------------------------------------------
  const errorSlot = h("div", { "aria-live": "assertive" });
  const start = button("Start run", { kind: "primary", big: true, type: "submit" });
  const modes = tabs(
    options.modes.map((m) => ({ key: m.key, label: m.label, controls: "mode-panel" })),
    { selected: "new", label: "What kind of run", onSelect: setMode },
  );

  function setMode(mode) {
    state.mode = mode;
    repoBox.hidden = mode === "new";
    fixBox.hidden = mode !== "fix";
    maintainBox.hidden = mode !== "maintain";
    reviewBox.hidden = mode !== "review";
    nameBox.hidden = mode === "review";
    requestPanel.hidden = mode === "review";
    modeBody.setAttribute("aria-labelledby", `tab-${mode}`);
    const copy = COPY[mode];
    if (copy) {
      requestLabel.textContent = copy.label;
      requestHelp.textContent = copy.help;
      template.hidden = !options.templates[mode];
    }
    side.advanced.setMode(mode);
    clear(errorSlot);
  }
  setMode("new");

  function fail(message, focusOn) {
    clear(errorSlot).append(errorState(message));
    announce(message);
    focusOn?.focus();
  }

  async function submit(event) {
    event.preventDefault();
    clear(errorSlot);
    const spec = { mode: state.mode, options: {} };
    const requestText = text.value.trim();
    if (state.mode !== "review") {
      if (requestText) spec.request = requestText;
      if (!requestText && !state.files.length && state.mode !== "maintain") return fail("Write a request or attach a file first.", text);
      if (requestText.length > options.limits.max_request_chars) return fail("The request is too long; attach it as a file instead.", text);
    }
    if (state.mode !== "new") {
      if (!repo.value.trim()) return fail("Give the project folder.", repo);
      if (repoOk === false) return fail("That folder is not usable; see the message under it.", repo);
      spec.repo = repo.value.trim();
    }
    if (name.value.trim() && state.mode !== "review") spec.project_name = name.value.trim();
    if (state.mode === "fix") {
      if (repro.value.trim()) spec.repro = repro.value.trim();
      if (trace.value.trim()) spec.trace = trace.value;
      if (unrepro.checked) spec.allow_unreproduced = true;
    }
    if (state.mode === "maintain") {
      spec.task = task.value;
      if (fixFindings.checked) spec.fix_findings = true;
      if (requestText) spec.goal = requestText;
    }
    if (state.mode === "review") {
      if (base.value.trim()) spec.base = base.value.trim();
      if (focus.value.trim()) spec.focus = focus.value.trim();
    }
    for (const part of Object.values(side)) part.collect(spec.options, spec);
    start.disabled = true;
    try {
      let answer;
      if (state.files.length && state.mode !== "review") {
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

  const columns = h(
    "div",
    { class: "layout-2" },
    h("div", { class: "stack" }, requestPanel, panel("Details", modeBody)),
    h("div", { class: "stack" }, side.team.element, side.models.element, side.budget.element, side.advanced.element),
  );
  const actions = h("div", { class: "sticky-actions" }, errorSlot, h("div", { class: "row" }, start, h("span", { class: "help" }, "The run starts in its own process; closing this page does not stop it.")));
  root.append(
    h(
      "div",
      { class: "reveal" },
      h("div", { class: "page-head" }, h("p", { class: "eyebrow" }, "Brief the team"), h("h1", {}, "New run"), h("p", { class: "lede" }, "Describe the work, point at a project if there is one, and start. Everything else has a sensible default.")),
      h("div", { class: "stack" }, modes.element),
      h("form", { novalidate: true, onsubmit: submit }, columns, actions),
    ),
  );
  paintCount();
  return () => inspect.cancel();
}

function repoCard(found) {
  const p = found.profile;
  const langs = p.languages.slice(0, 3).map((l) => `${l.language} ${l.lines.toLocaleString()} lines`).join(" · ");
  const stack = p.stacks.map((s) => [s.language, s.manager, s.test].filter(Boolean).join(" / ")).join("; ");
  const git = p.git;
  const gitText = !git.available ? "Git is not installed" : git.is_repo ? `branch ${git.branch}${git.dirty ? `, ${git.changed_files + git.untracked_files} uncommitted` : ", clean"}` : "not a Git repository";
  return h(
    "div",
    { class: "callout" },
    h("div", { class: "row" }, chip(p.name, "good", "check"), chip(gitText, git.dirty || !git.is_repo ? "warn" : "", "branch")),
    h("div", { class: "mt" }, kv([["Languages", langs || "none recognised"], ["Stack", stack || "none detected"], ["Files", `${p.files.toLocaleString()}${p.truncated ? "+" : ""}`], ["Tests", p.test_files ? `${p.test_files} test file(s)` : "none found"], ["Isolation", `${found.isolation.mode}: ${found.isolation.why}`]])),
  );
}
