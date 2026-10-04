// The three "look at what was made" views of the results screen: the diff, the project's files,
// and the screenshots the browser tools took. Run output is only ever inserted as text.

import { blob, get } from "../api.js";
import { bytes, clear, h, icon } from "../dom.js";
import { chip, emptyState, errorState, loading } from "../ui.js";

export function diffView(report) {
  const diff = report.diff;
  if (!diff) return emptyState("No diff for this run", report.diff_note || "Only runs on an existing project (add feature, fix bug, maintain) record a starting point to compare against.");
  const files = diff.files.map((file, index) =>
    h(
      "details",
      { class: "diff-file", open: index < 3 },
      h("summary", {}, icon("file"), h("span", { class: "grow" }, file.path), chip(file.status, file.status === "added" ? "good" : file.status === "deleted" ? "bad" : ""), file.binary ? chip("binary") : [h("span", { class: "plus" }, `+${file.added}`), h("span", { class: "minus" }, `-${file.removed}`)]),
      file.binary ? h("p", { class: "help pad" }, "Binary file; no preview.") : h("div", { class: "diff-lines", role: "region", "aria-label": `Diff of ${file.path}`, tabindex: 0 }, file.lines.map((line) => h("div", { class: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "" }, line || " ")), file.omitted ? h("div", { class: "hunk" }, `… ${file.omitted} more line(s) not shown; export the patch for the full diff`) : null),
    ),
  );
  return h("div", {}, h("p", { class: "help" }, `${diff.files.length} file(s) changed, `, h("span", { class: "plus" }, `+${diff.added}`), " ", h("span", { class: "minus" }, `-${diff.removed}`), diff.truncated ? ". The diff is shortened here; export the patch for all of it." : ""), files);
}

export function filesView(runId) {
  const viewer = h("pre", { class: "viewer panel", tabindex: 0, "aria-label": "File contents" }, "Pick a file to read it.");
  const tree = h("ul", { class: "tree panel", role: "tree", "aria-label": "Project files" });
  const open = async (entry, button) => {
    for (const b of tree.querySelectorAll("[aria-selected=true]")) b.setAttribute("aria-selected", "false");
    button.setAttribute("aria-selected", "true");
    viewer.textContent = "Loading…";
    try {
      const file = await get(`/runs/${runId}/files/${entry.path.split("/").map(encodeURIComponent).join("/")}`);
      viewer.textContent = file.content + (file.truncated ? `\n\n… truncated (${bytes(file.size)} in all)` : "");
    } catch (error) {
      viewer.textContent = error.message;
    }
  };
  async function fill(list, path) {
    list.append(loading(1));
    try {
      const entries = await get(`/runs/${runId}/files?path=${encodeURIComponent(path)}`);
      clear(list);
      if (!entries.length) list.append(h("li", { class: "help" }, "(empty)"));
      for (const entry of entries) list.append(node(entry));
    } catch (error) {
      clear(list).append(errorState(error));
    }
  }
  function node(entry) {
    if (entry.type === "file") {
      const button = h("button", { type: "button", role: "treeitem", "aria-selected": "false", onclick: () => open(entry, button) }, icon("file"), entry.name, h("span", { class: "counter" }, bytes(entry.size)));
      return h("li", { role: "none" }, button);
    }
    const sub = h("ul", { role: "group" });
    let loaded = false;
    const button = h("button", { type: "button", role: "treeitem", "aria-expanded": "false", onclick: async () => {
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      sub.hidden = expanded;
      if (!loaded && !expanded) {
        loaded = true;
        await fill(sub, entry.path);
      }
    } }, icon("folder"), entry.name);
    sub.hidden = true;
    return h("li", { role: "none" }, button, sub);
  }
  fill(tree, ".");
  return h("div", { class: "explorer" }, tree, viewer);
}

export function screenshotsView(runId, shots) {
  if (!shots.length) return emptyState("No screenshots", "Runs that use the browser tools save their screenshots here.");
  const urls = [];
  const gallery = h("div", { class: "gallery" });
  for (const shot of shots.slice(0, 60)) {
    const img = h("img", { alt: `${shot.name}${shot.url ? ` of ${shot.url}` : ""}`, loading: "lazy" });
    blob(`/runs/${runId}/artifacts/${shot.path.split("/").map(encodeURIComponent).join("/")}`).then((data) => {
      const url = URL.createObjectURL(data);
      urls.push(url);
      img.src = url;
    }).catch(() => img.setAttribute("alt", `${shot.name} (could not be loaded)`));
    gallery.append(h("figure", {}, img, h("figcaption", {}, shot.name, shot.agent ? ` · ${shot.agent}` : "", shot.url ? ` · ${shot.url}` : "")));
  }
  gallery.dataset.urls = "blob";
  gallery.cleanup = () => urls.forEach((u) => URL.revokeObjectURL(u));
  return gallery;
}
