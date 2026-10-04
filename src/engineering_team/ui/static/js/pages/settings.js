// Screen 5: the effective configuration (secrets masked by the server), the environment checks,
// and which optional tools this machine has.

import { get } from "../api.js";
import { h, clear } from "../dom.js";
import { chip, errorState, loading, panel, statusChip, table } from "../ui.js";

export async function mount(root) {
  const configBox = h("div", {}, loading(4));
  const checksBox = h("div", {}, loading(4));
  const toolsBox = h("div");
  const filter = h("input", { type: "search", id: "cfg-filter", placeholder: "Filter settings", "aria-label": "Filter settings" });
  root.append(
    h(
      "div",
      { class: "reveal" },
      h("div", { class: "page-head" }, h("p", { class: "eyebrow" }, "Machine and configuration"), h("h1", {}, "Settings & doctor"), h("p", { class: "lede" }, "What the team would use right now, and whether this machine can run it. Values that look like secrets are masked. Change them in the config file or the environment, not here.")),
      h("div", { class: "stack" }, panel("Tools", toolsBox), panel("Environment checks", checksBox), panel("Configuration", h("div", { class: "stack" }, filter, configBox))),
    ),
  );

  const [doctor, config, options] = await Promise.allSettled([get("/doctor"), get("/config"), get("/options")]);

  if (doctor.status === "fulfilled") {
    const { checks, ok, ui_missing: missing } = doctor.value;
    clear(checksBox).append(
      h("div", { class: "stack" }, h("div", { class: `callout ${ok ? "good" : "bad"}`, role: "status" }, ok ? "Nothing is blocking a run." : "Something blocks a run; see the failing checks."), missing.length ? h("div", { class: "callout warn" }, `Missing for the UI: ${missing.join(", ")}`) : null, table(["Check", "Status", "Detail"], checks.map((c) => [h("strong", {}, c.name), statusChip(c.status), h("span", {}, c.detail || c.message || "", c.hint ? h("span", { class: "sub" }, c.hint) : null)]))),
    );
  } else clear(checksBox).append(errorState(doctor.reason));

  if (options.status === "fulfilled") {
    const t = options.value.tools;
    clear(toolsBox).append(
      h("div", { class: "stack" }, h("div", { class: "row" }, chip(`browser: ${t.browser.installed ? "installed" : "not installed"}`, t.browser.installed ? "good" : "warn", t.browser.installed ? "check" : "alert"), chip(`docker: ${t.docker.installed ? "found" : "not found"}`, t.docker.installed ? "good" : "warn", t.docker.installed ? "check" : "alert"), chip(`web tools: ${t.web.enabled ? "on" : "off"}`, t.web.enabled ? "good" : "")), h("p", { class: "help" }, t.web.search_providers.length ? `Search keys present for: ${t.web.search_providers.join(", ")}.` : "No web search key present (Fetch URL and Package Info need none).", " Browser tools need `pip install 'engineering_team[browser]'` and `playwright install chromium`.")),
    );
  } else clear(toolsBox).append(errorState(options.reason));

  if (config.status === "fulfilled") {
    const { settings, notes } = config.value;
    const paint = () => {
      const q = filter.value.trim().toLowerCase();
      const rows = settings.filter((r) => !q || `${r.key} ${r.value} ${r.source}`.toLowerCase().includes(q));
      clear(configBox).append(h("div", { class: "stack" }, notes.length ? h("div", { class: "callout warn" }, notes.map((n) => h("div", {}, n))) : null, rows.length ? table(["Setting", "Value", "Source"], rows.map((r) => [h("code", {}, r.key), h("span", { class: "mono-cell" }, String(r.value)), h("span", { class: "sub" }, r.source)])) : h("p", { class: "help" }, "No setting matches.")));
    };
    filter.addEventListener("input", paint);
    paint();
  } else clear(configBox).append(errorState(config.reason));
}
