// The app shell: hash routes, the theme switch, the token prompt, and the demo banner.
// Each screen is a module exporting `mount(root, params)` that returns a cleanup function.

import { get, setToken } from "./api.js";
import { h, icon, clear } from "./dom.js";
import { announce, errorState, loading, button } from "./ui.js";

const ROUTES = [
  [/^\/?$/, "new", () => import("./pages/new_run.js"), "New run"],
  [/^\/runs\/?$/, "runs", () => import("./pages/history.js"), "Runs"],
  [/^\/runs\/([^/]+)\/results\/?$/, "runs", () => import("./pages/results.js"), "Results"],
  [/^\/runs\/([^/]+)\/?$/, "runs", () => import("./pages/run.js"), "Run"],
  [/^\/settings\/?$/, "settings", () => import("./pages/settings.js"), "Settings"],
];

const main = document.getElementById("main");
let cleanup = null;
let navigation = 0;

async function show() {
  const path = location.hash.replace(/^#/, "") || "/";
  const found = ROUTES.map(([pattern, nav, load, title]) => [pattern.exec(path), nav, load, title]).find(([m]) => m);
  const ticket = ++navigation;
  if (cleanup) {
    try {
      cleanup();
    } catch (error) {
      console.error(error);
    }
    cleanup = null;
  }
  clear(main).append(loading());
  if (!found) {
    clear(main).append(
      h("div", { class: "empty" }, h("h1", { tabindex: -1 }, "Nothing here"), h("p", {}, `There is no page at ${path}.`), button("Start a run", { kind: "primary", onclick: () => (location.hash = "#/") })),
    );
    return;
  }
  const [match, nav, load, title] = found;
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === nav) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  try {
    const page = await load();
    if (ticket !== navigation) return;
    clear(main);
    cleanup = (await page.mount(main, match.slice(1).map(decodeURIComponent))) || null;
    document.title = `${title} · engineering-team`;
    const heading = main.querySelector("h1");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
    window.scrollTo(0, 0);
    announce(`${title} page`);
  } catch (error) {
    if (ticket !== navigation) return;
    clear(main).append(errorState(error, show));
  }
}

// -- theme ------------------------------------------------------------------------------------

const toggle = document.getElementById("theme-toggle");
const dark = () => {
  const set = document.documentElement.dataset.theme;
  return set ? set === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};
function paintToggle() {
  clear(toggle).append(icon(dark() ? "sun" : "moon"));
  toggle.title = dark() ? "Switch to the light theme" : "Switch to the dark theme";
}
toggle.addEventListener("click", () => {
  const next = dark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("et-theme", next);
  } catch {
    // not persisted; fine
  }
  paintToggle();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintToggle);
paintToggle();

// -- token prompt (only when the server was started with --allow-remote) -------------------------

document.addEventListener("et-auth", (event) => {
  if (document.getElementById("auth-dialog")) return;
  const input = h("input", { type: "password", id: "token", autocomplete: "off", required: true });
  const dialog = h(
    "dialog",
    { id: "auth-dialog", "aria-labelledby": "auth-title" },
    h(
      "form",
      {
        method: "dialog",
        onsubmit: () => {
          setToken(input.value);
          dialog.close();
          dialog.remove();
          event.detail();
        },
      },
      h("h2", { id: "auth-title" }, "This server needs its token"),
      h("p", {}, "It was started with --allow-remote and printed a bearer token in the terminal. The page keeps it for this tab only."),
      h("div", { class: "field" }, h("label", { for: "token" }, "Bearer token"), input),
      h("div", { class: "row" }, h("button", { class: "btn primary", type: "submit" }, "Continue")),
    ),
  );
  document.body.append(dialog);
  dialog.showModal();
  input.focus();
});

// -- demo banner -------------------------------------------------------------------------------

get("/options")
  .then((options) => {
    if (!options.demo) return;
    document.getElementById("banner-slot").replaceChildren(
      h("div", { class: "banner demo", role: "note" }, h("strong", {}, "Demo mode. "), "Runs are scripted: no model is called and no key is needed. They only write under engineering_team_demo/ in the sample project."),
    );
  })
  .catch(() => {});

window.addEventListener("hashchange", show);
show();
