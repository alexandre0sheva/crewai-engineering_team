// Screen 5: what the team has learned. The experience library (lessons, with the ones waiting for
// approval first) and, for a project folder, its repository memory.

import { get } from "../api.js";
import { clear, debounce, h } from "../dom.js";
import { errorState, loading } from "../ui.js";
import { lessonsSection, memorySection } from "./learning_widgets.js";

export async function mount(root) {
  const options = await get("/options");
  const query = new URLSearchParams(location.hash.split("?")[1] || "");
  const repo = h("input", { type: "text", id: "learn-repo", value: query.get("repo") || "", placeholder: "/absolute/path/to/a project (empty: your lessons only)", autocomplete: "off", spellcheck: "false" });
  const body = h("div", { class: "stack", "aria-live": "polite" });

  async function load() {
    const path = repo.value.trim();
    clear(body).append(loading(4));
    try {
      const suffix = path ? `?repo=${encodeURIComponent(path)}` : "";
      const [lessons, memory] = await Promise.all([get(`/lessons${suffix}`), path ? get(`/memory?repo=${encodeURIComponent(path)}`) : null]);
      clear(body).append(lessonsSection(lessons, { repo: path || null, reload: load }), memory ? memorySection(memory, { reload: load }) : h("p", { class: "help" }, "Give a project folder to see what the team remembers about it."));
    } catch (error) {
      clear(body).append(errorState(error, load));
    }
  }
  const again = debounce(load, 500);
  repo.addEventListener("input", again);
  const demo = options.demo ? h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => { repo.value = options.demo.repo; load(); } }, "Use the demo sample project")) : null;
  root.append(h("div", { class: "reveal" },
    h("div", { class: "page-head" }, h("p", { class: "eyebrow" }, "What the team keeps"), h("h1", {}, "Learning"), h("p", { class: "lede" }, "Lessons distilled from runs the controller verified, and the facts it remembers about a project. Both are advice and context for the team, never permissions or verdicts: remove anything you do not trust.")),
    h("div", { class: "stack" }, h("div", { class: "field" }, h("label", { for: "learn-repo" }, "Project folder (optional)"), repo, h("span", { class: "help" }, "Adds that project's own lessons and its memory."), demo), body)));
  await load();
  return () => again.cancel();
}
