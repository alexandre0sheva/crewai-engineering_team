// The card drawer: one card in full. Description, status history, the steering-note form, the
// card's files and screenshots, the tool calls its assignee made while it worked, and the checks
// that justify it. The card itself is the server's; this only displays it.

import { blob, get, post } from "../api.js";
import { bytes, clock, clear, h, money, tokens, when } from "../dom.js";
import { button, chip, statusChip, toast } from "../ui.js";
import { avatar, label } from "./colors.js";
import { STATUS_LABEL } from "./store.js";

const KIND_LABEL = { stage: "Stage", work_package: "Work package", subtask: "Subtask", repair: "Repair", finding: "Finding", check: "Check", user_note: "Note" };

export function createDrawer(store) {
  const dialog = h("dialog", { class: "drawer", "aria-labelledby": "drawer-title" });
  document.body.append(dialog);
  let current = null;
  let urls = [];
  let timer = 0;
  let lastKey = "";
  let fresh = false;

  dialog.addEventListener("close", () => {
    current = null;
    clearInterval(timer);
    for (const u of urls) URL.revokeObjectURL(u);
    urls = [];
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });

  const cardNow = () => store.viewCards.find((c) => c.id === current);

  async function open(cardId) {
    current = cardId;
    lastKey = "";
    fresh = true;
    paint(null);
    if (!dialog.open) dialog.showModal();
    await loadDetail();
    clearInterval(timer);
    timer = setInterval(() => {
      const c = cardNow();
      if (c && ["in_progress", "verifying"].includes(c.status) && !store.replay) loadDetail();
    }, 3000);
  }

  let detail = null;
  async function loadDetail() {
    if (!current) return;
    const id = current;
    try {
      const found = await get(`/runs/${store.runId}/cards/${id}`);
      if (id === current) {
        detail = found;
        paint(found);
      }
    } catch (error) {
      if (id === current) clear(dialog).append(h("div", { class: "panel-body" }, h("p", { class: "callout bad", role: "alert" }, error.message), button("Close", { onclick: () => dialog.close() })));
    }
  }

  function paint(found) {
    const card = cardNow() || found?.card;
    if (!card) return;
    const key = JSON.stringify([card, found?.trail.length]);
    if (key === lastKey || document.activeElement?.id === "steer-text") return; // nothing new / typing
    lastKey = key;
    const form = steerForm(card);
    const keepForm = dialog.querySelector(".steer textarea")?.value;
    clear(dialog).append(
      h("header", { class: "drawer-head" },
        h("div", { class: "row" }, h("span", { class: "chip mono" }, card.id), chip(KIND_LABEL[card.kind] || card.kind), statusChip(card.status, STATUS_LABEL[card.status])),
        h("h2", { id: "drawer-title" }, card.title),
        h("div", { class: "row" }, card.assignee ? [avatar(card.assignee, { large: true }), h("strong", {}, label(card.assignee))] : h("span", { class: "help" }, "Moved by the controller"), card.lane != null ? chip(`lane ${card.lane}`) : null, card.attempts > 1 ? chip(`attempt ${card.attempts}`, "warn", "refresh") : null),
        button("Close", { glyph: "close", kind: "ghost", small: true, onclick: () => dialog.close() })),
      h("div", { class: "drawer-body" },
        card.status === "blocked" && card.blocked_reason ? h("p", { class: "callout warn" }, h("strong", {}, "Blocked: "), card.blocked_reason) : null,
        card.description ? section("Description", h("p", { class: "pre-wrap" }, card.description)) : null,
        card.progress_note ? section("Latest status", h("p", {}, card.progress_note)) : null,
        facts(card),
        section("Status history", history(card)),
        section(`Comments (${card.comments.length})`, comments(card), form),
        found ? section(`Tool calls (${found.trail.length})`, trail(found), h("p", { class: "help" }, found.trail_note)) : section("Tool calls", h("div", { class: "skeleton" })),
        card.evidence.length ? section("Evidence", evidence(card)) : null,
        card.artifacts.length ? section("Files and screenshots", artifacts(card)) : null,
      ),
    );
    if (keepForm) dialog.querySelector(".steer textarea").value = keepForm;
    if (fresh) {
      fresh = false;
      dialog.querySelector(".drawer-head .btn")?.focus();
    }
  }

  const section = (title, ...body) => h("section", { class: "d-section" }, h("h3", {}, title), ...body);

  function facts(card) {
    const bits = [];
    if (card.started) bits.push(["Started", when(card.started)]);
    if (card.finished) bits.push(["Finished", when(card.finished)]);
    bits.push(["Tokens", tokens(card.tokens)], ["Cost", card.cost_usd == null ? "—" : money(card.cost_usd)]);
    if (card.criteria_ids.length) bits.push(["Criteria", card.criteria_ids.join(", ")]);
    if (card.owned_paths.length) bits.push(["Owns", card.owned_paths.join(", ")]);
    if (card.depends_on.length) bits.push(["Waits for", card.depends_on.join(", ")]);
    return h("dl", { class: "kv d-facts" }, bits.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  }

  const history = (card) => h("ol", { class: "d-history" }, card.history.map((m) => h("li", {}, h("span", { class: "mono" }, clock(m.ts)), " ", h("strong", {}, m.from_status ? `${STATUS_LABEL[m.from_status]} → ${STATUS_LABEL[m.to_status]}` : `Created as ${STATUS_LABEL[m.to_status]}`), h("span", { class: "help" }, ` by ${m.actor}${m.note ? `: ${m.note}` : ""}`))));

  const comments = (card) => (card.comments.length ? h("ul", { class: "d-comments" }, card.comments.map((c) => h("li", { class: c.author === "user" ? "mine" : "" }, h("div", { class: "help" }, `${c.author === "user" ? "You" : label(c.author)} · ${clock(c.ts)}${c.delivered_to?.length ? ` · delivered to ${c.delivered_to.map(label).join(", ")}` : c.author === "user" ? " · not delivered yet" : ""}`), h("div", { class: "pre-wrap" }, c.text)))) : h("p", { class: "help" }, "No comments yet."));

  function steerForm(card) {
    const text = h("textarea", { id: "steer-text", rows: 3, maxlength: 4000, placeholder: "Tell the assignee something, e.g. “use the existing logger”", disabled: store.terminal || !!store.replay });
    const send = button("Send steering note", { kind: "primary", small: true, disabled: store.terminal || !!store.replay, onclick: async () => {
      if (!text.value.trim()) return text.focus();
      try {
        await post(`/runs/${store.runId}/cards/${card.id}/comments`, { text: text.value });
        text.value = "";
        toast("Sent. The assignee reads it at the start of its next step.");
        setTimeout(loadDetail, 1200);
      } catch (error) {
        toast(error.message, "bad");
      }
    } });
    return h("div", { class: "steer field" }, h("label", { for: "steer-text" }, "Send a steering note"), text, h("div", { class: "row" }, send, h("span", { class: "help" }, store.terminal ? "The run is over." : "Delivered once, at the assignee's next step.")));
  }

  function trail(found) {
    if (!found.trail.length) return h("p", { class: "help" }, "No tool calls recorded for this card.");
    return h("div", { class: "table-wrap trail" }, h("table", {}, h("thead", {}, h("tr", {}, ["Time", "Tool", "Arguments", "Took", "Result"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", {}, found.trail.map((t) => h("tr", {}, h("td", { class: "mono-cell" }, clock(t.ts)), h("td", {}, t.tool), h("td", { class: "mono-cell args" }, t.args), h("td", { class: "num" }, `${t.duration.toFixed(2)}s`), h("td", {}, t.ok ? chip("ok", "good", "check") : chip("failed", "bad", "x")))))));
  }

  function evidence(card) {
    const checks = store.run?.checks || [];
    return h("ul", { class: "d-evidence" }, card.evidence.map((id) => {
      const c = checks.find((x) => x.id === id);
      return h("li", {}, statusChip(c?.status || "unverified", c?.status), h("strong", {}, ` ${c?.name || id}`), c?.summary ? h("span", { class: "help" }, ` ${c.summary}`) : null);
    }));
  }

  function artifacts(card) {
    const view = h("pre", { class: "viewer panel", hidden: true });
    const items = card.artifacts.map((path) => {
      const isImage = /\.(png|jpe?g|gif|webp)$/i.test(path);
      const open = async () => {
        view.hidden = false;
        view.textContent = "Loading…";
        try {
          if (isImage) {
            const url = URL.createObjectURL(await blob(`/runs/${store.runId}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`));
            urls.push(url);
            view.replaceChildren(h("img", { src: url, alt: path, class: "shot" }));
          } else {
            const file = await get(`/runs/${store.runId}/files/${path.split("/").map(encodeURIComponent).join("/")}`);
            view.textContent = file.content + (file.truncated ? `\n… truncated (${bytes(file.size)})` : "");
          }
        } catch (error) {
          view.textContent = error.message;
        }
      };
      return h("li", {}, button(path, { glyph: isImage ? "file" : "file", kind: "ghost", small: true, onclick: open }));
    });
    return h("div", { class: "stack" }, h("ul", { class: "d-files" }, items), view);
  }

  store.on("view", () => {
    if (dialog.open && current) paint(detail?.card.id === current ? detail : null);
  });

  return { open, element: dialog, close: () => dialog.open && dialog.close(), destroy: () => dialog.remove() };
}

