// A task-board card as a DOM element, created once per card id and updated in place so it can
// move between columns without being rebuilt (the board animates that move).

import { append, clear, duration, h, icon, seconds } from "../dom.js";
import { avatar, label, tint } from "./colors.js";
import { STATUS_LABEL } from "./store.js";

const KIND_ICON = { stage: "layers", work_package: "box", subtask: "list", repair: "wrench", finding: "flag", check: "shield", user_note: "message" };
const KIND_LABEL = { stage: "Stage", work_package: "Work package", subtask: "Subtask", repair: "Repair", finding: "Finding", check: "Check", user_note: "Note" };
export const TOOLTIP = "The controller moves cards, not people. Open the card to send the team a steering note.";

export function describe(card) {
  const who = card.assignee ? `, ${label(card.assignee)}` : "";
  const reason = card.status === "blocked" && card.blocked_reason ? `, blocked: ${card.blocked_reason}` : "";
  return `${card.id}, ${card.title}, ${STATUS_LABEL[card.status] || card.status}${who}${reason}`;
}

export function makeCard(card, open, at = null) {
  const el = h("article", { class: "kcard", role: "button", tabindex: "-1", title: TOOLTIP, dataset: { id: card.id }, onclick: () => open(card.id) });
  el.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open(card.id);
    }
  });
  el.parts = {
    head: h("div", { class: "kc-head" }),
    title: h("p", { class: "kc-title" }),
    note: h("p", { class: "kc-note" }),
    foot: h("div", { class: "kc-foot" }),
  };
  el.append(el.parts.head, el.parts.title, el.parts.note, el.parts.foot);
  updateCard(el, card, at);
  return el;
}

// ``at``: the replayed moment (an ISO time), so a running timer shows what it showed then.
export function updateCard(el, card, at = null) {
  const { head, title, note, foot } = el.parts;
  el.dataset.status = card.status;
  el.dataset.kind = card.kind;
  el.setAttribute("aria-label", describe(card));
  if (card.assignee) {
    tint(el, card.assignee);
    el.dataset.assignee = card.assignee;
  } else delete el.dataset.assignee;
  append(clear(head), [
    h("span", { class: "kc-kind", title: KIND_LABEL[card.kind] || card.kind }, icon(KIND_ICON[card.kind] || "circle"), h("span", { class: "sr-only" }, `${KIND_LABEL[card.kind] || card.kind} `)),
    h("span", { class: "kc-id mono" }, card.id),
    card.lane != null ? h("span", { class: "kc-lane", title: `Parallel lane ${card.lane}` }, `lane ${card.lane}`) : null,
    card.attempts > 1 ? h("span", { class: "kc-attempt", title: `Attempt ${card.attempts}` }, icon("refresh"), `×${card.attempts}`) : null,
  ]);
  title.textContent = card.title;
  clear(note);
  if (card.status === "blocked" && card.blocked_reason) {
    note.className = "kc-note blocked";
    note.append(icon("lock"), h("span", {}, card.blocked_reason));
  } else if (card.progress_note && ["in_progress", "verifying"].includes(card.status)) {
    note.className = "kc-note";
    note.textContent = card.progress_note;
  } else {
    note.className = "kc-note empty";
  }
  const timer = timerFor(card, at);
  append(clear(foot), [
    card.assignee ? avatar(card.assignee) : h("span", { class: "avatar none", "aria-hidden": "true" }, "·"),
    card.assignee ? h("span", { class: "kc-who" }, label(card.assignee)) : h("span", { class: "kc-who" }, "controller"),
    timer,
    ...card.criteria_ids.slice(0, 3).map((id) => h("span", { class: "tag" }, id)),
    card.criteria_ids.length > 3 ? h("span", { class: "tag" }, `+${card.criteria_ids.length - 3}`) : null,
  ]);
}

function timerFor(card, at) {
  if (card.status === "in_progress" && card.started) {
    const live = at ? {} : { since: card.started };
    return h("span", { class: "kc-time elapsed", dataset: live, title: "Time since this card started" }, icon("clock"), duration(seconds(card.started, at)));
  }
  if (card.status === "done" && card.started && card.finished) {
    return h("span", { class: "kc-time" }, icon("clock"), duration(seconds(card.started, card.finished)));
  }
  return null;
}

// Keep every running timer on the screen current (one interval for all of them).
export function tickTimers(root) {
  for (const el of root.querySelectorAll("[data-since]")) {
    const time = el.lastChild;
    if (time) time.textContent = duration(seconds(el.dataset.since));
  }
}
