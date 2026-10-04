// Screen 2: one run, live. A header with status, progress, cost against the budget and the things
// that need you; the task board (also by teammate), the timeline and the activity feed; the
// teammates and the checks beside them; a card drawer for steering; and replay once it is over.
// Every number and state comes from the server's snapshots and events (dash/store.js).

import { h } from "../dom.js";
import { errorState, panel, tabs } from "../ui.js";
import { createBoard } from "../dash/board.js";
import { tickTimers } from "../dash/cards.js";
import { createDrawer } from "../dash/drawer.js";
import { createFeed } from "../dash/feed.js";
import { createHead } from "../dash/head.js";
import { createChecks } from "../dash/side.js";
import { createReplay } from "../dash/replay.js";
import { createRoster } from "../dash/roster.js";
import { Store } from "../dash/store.js";
import { createTimeline } from "../dash/timeline.js";

const VIEWS = [
  ["board", "Board", "grid"],
  ["lanes", "Swimlanes", "rows"],
  ["timeline", "Timeline", "gantt"],
  ["feed", "Activity", "activity"],
];

export async function mount(root, [runId]) {
  const store = new Store(runId);
  try {
    await store.start();
  } catch (error) {
    root.append(errorState(error));
    return () => store.stop();
  }
  const drawer = createDrawer(store);
  const open = (id) => drawer.open(id);
  const head = createHead(store, open);
  const replay = createReplay(store);
  const roster = createRoster(store, open);
  const checks = createChecks(store);
  const parts = [head, drawer, replay, roster, checks];
  const built = {};
  const factories = {
    board: () => createBoard(store, open),
    lanes: () => createBoard(store, open, { lanes: true }),
    timeline: () => createTimeline(store),
    feed: () => createFeed(store, open),
  };

  const stage = h("div", { id: "view-panel", role: "tabpanel", class: "view-panel" });
  let view = "board";
  try {
    view = sessionStorage.getItem("et-view") || view;
  } catch {
    // not remembered
  }
  if (!factories[view]) view = "board";
  const strip = tabs(VIEWS.map(([key, text]) => ({ key, label: text, controls: "view-panel" })), {
    selected: view,
    label: "How to look at the run",
    onSelect: (key) => {
      view = key;
      stage.setAttribute("aria-labelledby", `tab-${key}`);
      try {
        sessionStorage.setItem("et-view", key);
      } catch {
        // not remembered
      }
      try {
        built[key] ||= factories[key]();
      } catch (error) {
        console.error(error);
        stage.replaceChildren(errorState(error));
        return;
      }
      stage.replaceChildren(built[key].element);
      if (key === "board" || key === "lanes") built[key].update();
      if (key === "feed") built[key].refresh();
    },
  });

  root.append(
    h("div", { class: "dash reveal" }, head.head, head.attention, replay.element,
      h("div", { class: "dash-grid" },
        h("div", { class: "dash-main" }, strip.element, stage),
        h("aside", { class: "dash-side", "aria-label": "Teammates and checks" }, panel("Team", roster.element), panel("Checks and findings", checks.element)))),
  );

  const clock = setInterval(() => tickTimers(root), 1000);
  const catching = setInterval(() => !store.run?.manifest && store.catchUp(), 2000);
  const stopReplayHint = store.on("replay", () => root.classList.toggle("replaying", !!store.replay));
  return () => {
    clearInterval(clock);
    clearInterval(catching);
    stopReplayHint();
    for (const v of Object.values(built)) v.destroy?.();
    for (const p of parts) p.destroy?.();
    store.stop();
  };
}
