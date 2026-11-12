// The learning tab of a run: the harness it resolved (packs, skills, which skills it loaded), the
// lessons it was given, and its project's memory. GET /runs/ID/learning.

import { get } from "../api.js";
import { clear, h } from "../dom.js";
import { chip, emptyState, errorState, kv, loading, panel, table } from "../ui.js";
import { lessonItem, memorySection } from "./learning_widgets.js";

export function learningView(runId) {
  const box = h("div", { class: "stack" }, loading(4));
  async function load() {
    try {
      const view = await get(`/runs/${runId}/learning`);
      clear(box).append(harnessPanel(view.harness), lessonsGiven(view), memorySection(view.memory, { reload: load }), h("p", { class: "help" }, h("a", { href: `#/learning?repo=${encodeURIComponent(view.project)}` }, "Everything the team has learned about this project")));
    } catch (error) {
      clear(box).append(errorState(error, load));
    }
  }
  load();
  return box;
}

function harnessPanel(hn) {
  if (!hn.recorded) return panel("Harness", emptyState("No harness recorded", hn.note));
  const skills = hn.skills.length
    ? table(["Skill", "Used", "What it is for", "From"], hn.skills.map((s) => [h("code", {}, s.name), s.loaded ? chip("loaded", "good", "check") : chip("not needed", ""), s.description, [s.pack, s.source].filter(Boolean).join(" · ")]))
    : h("p", { class: "help" }, "No skills were listed for this run.");
  return panel("Harness", h("div", { class: "stack" },
    hn.note ? h("div", { class: "callout warn" }, hn.note) : null,
    kv([["Domain", h("code", {}, hn.domain)], ["Packs", hn.packs.map((p) => `${p.name} (${p.source}, ${p.sha256})`).join("; ")], ["Skills loaded", hn.loaded.join(", ") || "none"]]),
    skills), { hint: "prompts and skills the team worked with" });
}

function lessonsGiven(view) {
  const given = view.lessons.given;
  const body = given.length
    ? h("ul", { class: "learned" }, given.map((l) => (l.gone ? h("li", { class: "lesson" }, h("code", {}, l.id), " — no longer in the library.") : lessonItem(l, null, null))))
    : emptyState("No lessons were given", "None matched this request, or the library had none for this kind of work.");
  return panel("Lessons it was given", h("div", { class: "stack" }, h("p", { class: "help" }, view.lessons.note), body));
}
