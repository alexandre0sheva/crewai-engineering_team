// Replay of a finished run: a slider over its events and play/pause at 1× to 16× real time. The
// board and teammates are fetched from the server for the event the slider is on
// (GET /board?at=SEQ), so what is shown is exactly what the run looked like then.

import { clock, duration, h, seconds } from "../dom.js";
import { button } from "../ui.js";

const SPEEDS = [1, 2, 4, 8, 16];

export function createReplay(store) {
  const box = h("section", { class: "replay panel", "aria-label": "Replay" });
  let timer = 0;
  let clockMs = 0; // the replayed moment, in ms since the first event
  let shown = {};

  const startedAt = () => new Date(store.events[0].ts).getTime();
  const moment = (i) => new Date(store.events[i].ts).getTime() - startedAt();

  function indexAt(ms) {
    let lo = 0;
    let hi = store.events.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (moment(mid) <= ms) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  function paint() {
    const r = store.replay;
    box.hidden = !store.terminal || !store.events.length;
    if (box.hidden) return;
    shown = { on: !!r, playing: r?.playing, speed: r?.speed, count: store.events.length };
    box.replaceChildren();
    if (!r) {
      box.append(h("div", { class: "row spread" }, h("p", { class: "help" }, `This run is over. Replay it to watch the board and the team as they were, from the first event to the last (${store.events.length.toLocaleString()} events).`), button("Replay this run", { glyph: "rewind", kind: "primary", small: true, onclick: () => { clockMs = 0; store.seekReplay(0, { playing: false }); } })));
      return;
    }
    const slider = h("input", { type: "range", id: "replay-slider", min: 0, max: store.events.length - 1, value: r.index, "aria-label": "Replay position", oninput: () => { pause(); clockMs = moment(Number(slider.value)); store.seekReplay(Number(slider.value), { playing: false }); } });
    slider.setAttribute("aria-valuetext", `event ${r.index + 1} of ${store.events.length}, ${clock(store.events[r.index].ts)}`);
    const speed = h("select", { id: "replay-speed", "aria-label": "Speed", onchange: () => store.seekReplay(r.index, { speed: Number(speed.value) }) }, SPEEDS.map((s) => h("option", { value: s, selected: s === r.speed }, `${s}×`)));
    const elapsed = seconds(store.events[0].ts, store.events[r.index].ts);
    box.append(h("div", { class: "replay-row" },
      button(r.playing ? "Pause" : "Play", { glyph: r.playing ? "pause" : "play", small: true, kind: "primary", onclick: () => (r.playing ? pause() : play()) }),
      slider, h("span", { class: "mono replay-time" }, `${duration(elapsed)} · ${clock(store.events[r.index].ts)}`), speed,
      button("Back to the end", { small: true, onclick: () => { pause(); store.exitReplay(); } })));
  }

  function play() {
    const r = store.replay;
    if (!r) return;
    if (r.index >= store.events.length - 1) {
      clockMs = 0;
      store.seekReplay(0, { playing: true });
    } else {
      clockMs = moment(r.index);
      r.playing = true;
    }
    clearInterval(timer);
    timer = setInterval(() => {
      const cur = store.replay;
      if (!cur) return pause();
      clockMs += 100 * cur.speed;
      const i = indexAt(clockMs);
      if (i >= store.events.length - 1) {
        store.seekReplay(store.events.length - 1, { playing: false });
        return pause();
      }
      store.seekReplay(i, { playing: true });
    }, 100);
    paint();
  }

  function pause() {
    clearInterval(timer);
    if (store.replay) store.replay.playing = false;
    paint();
  }

  // Redraw the controls when the position changes, but not while the slider is being dragged.
  // A new position only moves the slider and the time, so keyboard focus on the buttons stays.
  function follow() {
    const r = store.replay;
    const slider = box.querySelector("#replay-slider");
    const same = r && slider && shown.on && shown.playing === r.playing && shown.speed === r.speed && shown.count === store.events.length;
    if (!same) return paint();
    if (document.activeElement !== slider) slider.value = r.index;
    slider.setAttribute("aria-valuetext", `event ${r.index + 1} of ${store.events.length}, ${clock(store.events[r.index].ts)}`);
    box.querySelector(".replay-time").textContent = `${duration(seconds(store.events[0].ts, store.events[r.index].ts))} · ${clock(store.events[r.index].ts)}`;
  }
  const offs = [store.on("replay", follow), store.on("run", paint)];
  paint();
  return { element: box, destroy: () => { clearInterval(timer); offs.forEach((f) => f()); } };
}
