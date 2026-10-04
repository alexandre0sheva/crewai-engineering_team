// The run's data, in one place. The server decides everything (card state, progress, teammate
// state, what needs attention, budget); the store only fetches those snapshots, applies the
// whole-card payloads that `board.*` events carry, and tells the views to redraw.
//
// Topics views subscribe to: "run" (header, attention, checks), "view" (board and teammates, live
// or replayed), "timeline", "events" (activity feed), "replay".

import { get, streamEvents } from "../api.js";
import { announce } from "../ui.js";

const MAX_EVENTS = 20000;
const LABELS = { backlog: "Backlog", ready: "Ready", in_progress: "In progress", verifying: "Verifying", blocked: "Blocked", done: "Done", failed: "Failed", cancelled: "Cancelled" };
export const STATUS_LABEL = LABELS;

export class Store {
  constructor(runId) {
    this.runId = runId;
    this.run = null;
    this.cards = new Map(); // live board
    this.paused = false;
    this.agents = [];
    this.timeline = null;
    this.events = [];
    this.replay = null; // { index, seq, cards, agents, playing, speed }
    this.streamState = "connecting";
    this.over = false;
    this.abort = new AbortController();
    this.listeners = new Map();
    this.timers = new Map();
    this.moves = [];
    this.moveTimer = 0;
    this.seen = new Set(); // question ids already announced
    this.baseSeq = 0; // events up to here were already there when the page opened
  }

  on(topic, fn) {
    if (!this.listeners.has(topic)) this.listeners.set(topic, new Set());
    this.listeners.get(topic).add(fn);
    return () => this.listeners.get(topic).delete(fn);
  }

  emit(topic, detail) {
    for (const fn of this.listeners.get(topic) || []) fn(detail);
  }

  // What the board and teammate views show: the replayed moment, or now.
  get viewCards() {
    return this.replay?.cards ?? [...this.cards.values()].sort((a, b) => a.id.localeCompare(b.id));
  }

  get viewAgents() {
    return this.replay?.agents ?? this.agents;
  }

  get terminal() {
    return ["succeeded", "failed", "cancelled"].includes(this.run?.status);
  }

  async start() {
    this.run = await get(`/runs/${this.runId}`);
    this.baseSeq = this.run.last_seq;
    const quiet = (promise) => promise.catch(() => {}); // a run that is still starting has no board yet
    await Promise.all([quiet(this.loadBoard()), quiet(this.loadAgents()), quiet(this.loadTimeline())]);
    this.noticeQuestions();
    this.emit("run");
    this.stream();
  }

  stop() {
    this.abort.abort();
    for (const t of this.timers.values()) clearTimeout(t);
    clearTimeout(this.moveTimer);
  }

  async loadBoard() {
    const board = await get(`/runs/${this.runId}/board`);
    this.cards = new Map(board.cards.map((c) => [c.id, c]));
    this.paused = board.paused;
    this.emit("view");
  }

  async loadAgents() {
    this.agents = await get(`/runs/${this.runId}/agents`);
    this.emit("view");
  }

  async loadTimeline() {
    try {
      this.timeline = await get(`/runs/${this.runId}/timeline`);
    } catch {
      return; // no manifest yet
    }
    this.emit("timeline");
  }

  async refreshRun() {
    try {
      this.run = await get(`/runs/${this.runId}`);
    } catch {
      return;
    }
    this.noticeQuestions();
    this.emit("run");
  }

  noticeQuestions() {
    for (const q of this.run.questions) {
      if (!this.seen.has(q.id)) {
        this.seen.add(q.id);
        announce(`${q.agent || "The team"} is asking you a question`);
        this.emit("question", q);
      }
    }
  }

  // A run whose process has not written its manifest yet: look again until it has.
  async catchUp() {
    await this.refreshRun();
    if (!this.run.manifest) return;
    await Promise.all([this.loadBoard(), this.loadAgents(), this.loadTimeline()].map((p) => p.catch(() => {})));
  }

  // Run `fn` at most once per `ms`, and once more after the last request.
  later(key, ms, fn) {
    if (this.timers.has(key)) return;
    this.timers.set(
      key,
      setTimeout(() => {
        this.timers.delete(key);
        fn();
      }, ms),
    );
  }

  stream() {
    streamEvents(this.runId, {
      signal: this.abort.signal,
      onEvent: (event) => this.onEvent(event),
      onStatus: (state) => {
        this.streamState = state;
        this.emit("events");
      },
      onEnd: async () => {
        this.over = true;
        this.streamState = "ended";
        await Promise.all([this.refreshRun(), this.loadAgents(), this.loadTimeline(), this.loadBoard()]);
        this.emit("events");
      },
    });
  }

  onEvent(event) {
    this.events.push(event);
    if (this.events.length > MAX_EVENTS) this.events.splice(0, this.events.length - MAX_EVENTS);
    const type = event.type;
    const card = event.data?.card;
    if (card && type.startsWith("board.")) {
      this.cards.set(card.id, card);
      if (!this.replay) this.later("view", 120, () => this.emit("view"));
      if (type === "board.card_moved" && event.seq > this.baseSeq) this.noteMove(event.data.card_id, event.data.to_status);
    } else if (type === "board.paused" || type === "board.unpaused") {
      this.paused = type === "board.paused";
    }
    this.later("events", 150, () => this.emit("events"));
    if (type === "tool.call" || type.startsWith("board.") || type.startsWith("question")) this.later("agents", 900, () => this.loadAgents());
    if (/^(run|stage|check|budget|question|lane|note|team)/.test(type) || type === "board.card_moved") this.later("run", 700, () => this.refreshRun());
    if (/^(stage|lane|check|run)/.test(type) || type.includes("finding")) this.later("timeline", 1500, () => this.loadTimeline());
  }

  // A short message to the screen-reader live region for card moves, batched so a burst is one line.
  noteMove(id, to) {
    this.moves.push(`${id} moved to ${LABELS[to] || to}`);
    if (this.moveTimer) return;
    this.moveTimer = setTimeout(() => {
      announce(this.moves.slice(-3).join(". "));
      this.moves = [];
      this.moveTimer = 0;
    }, 700);
  }

  // -- replay ----------------------------------------------------------------------------------

  enterReplay() {
    if (!this.events.length) return;
    this.seekReplay(this.events.length - 1, { playing: false, speed: this.replay?.speed || 4 });
  }

  exitReplay() {
    this.replay = null;
    this.emit("replay");
    this.emit("view");
    this.emit("events");
  }

  seekReplay(index, { playing, speed } = {}) {
    const i = Math.max(0, Math.min(index, this.events.length - 1));
    const keep = this.replay || {};
    this.replay = { ...keep, index: i, seq: this.events[i].seq, playing: playing ?? keep.playing ?? false, speed: speed ?? keep.speed ?? 4 };
    this.emit("replay");
    this.emit("events");
    this.later("replayfetch", 120, () => this.fetchReplay());
  }

  async fetchReplay() {
    const at = this.replay?.seq;
    if (at === undefined) return;
    try {
      const [board, agents] = await Promise.all([get(`/runs/${this.runId}/board?at=${at}`), get(`/runs/${this.runId}/agents?at=${at}`)]);
      if (!this.replay || this.replay.seq !== at) return;
      this.replay.cards = board.cards;
      this.replay.agents = agents;
      this.emit("view");
    } catch {
      // keep the previous frame
    }
  }
}
