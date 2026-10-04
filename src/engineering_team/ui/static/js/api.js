// The only module that talks to the server. Every call carries the header the API requires of
// mutating requests, and the bearer token when the server was started with --allow-remote.

export const BASE = "/api/v1";
let token = "";
try {
  token = sessionStorage.getItem("et-token") || "";
} catch {
  // Session storage can be blocked; the token then lasts until the page is reloaded.
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function setToken(value) {
  token = value.trim();
  try {
    sessionStorage.setItem("et-token", token);
  } catch {
    // see above
  }
}

export const needsToken = () => new Promise((resolve) => document.dispatchEvent(new CustomEvent("et-auth", { detail: resolve })));

function headers(extra) {
  const all = { "X-Engineering-Team": "1", ...extra };
  if (token) all.Authorization = `Bearer ${token}`;
  return all;
}

async function request(path, { method = "GET", json, form, signal } = {}) {
  const init = { method, headers: headers(json !== undefined ? { "Content-Type": "application/json" } : {}), signal };
  if (json !== undefined) init.body = JSON.stringify(json);
  if (form) init.body = form;
  let response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, "Cannot reach the server. Is `engineering-team ui` still running?");
  }
  if (response.status === 401) {
    await needsToken();
    return request(path, { method, json, form, signal });
  }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.error) message = typeof body.error === "string" ? body.error : JSON.stringify(body.error);
    } catch {
      // not JSON; keep the status line
    }
    throw new ApiError(response.status, message);
  }
  return response;
}

export const get = async (path, options) => (await request(path, options)).json();
export const post = async (path, json) => (await request(path, { method: "POST", json: json ?? {} })).json();
export const postForm = async (path, form) => (await request(path, { method: "POST", form })).json();
export const blob = async (path) => (await request(path)).blob();

// Server-Sent Events over fetch (so the Authorization header works, which EventSource cannot
// send). Reconnects with the last seen id; stops at the server's "end" event or on abort.
export async function streamEvents(runId, { after = 0, onEvent, onEnd, onStatus, signal }) {
  let last = after;
  let delay = 500;
  while (!signal.aborted) {
    try {
      onStatus?.("live");
      const response = await request(`/runs/${runId}/events?after=${last}`, { signal });
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      delay = 500;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        let cut;
        while ((cut = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const fields = {};
          for (const line of frame.split("\n")) {
            const at = line.indexOf(": ");
            if (at > 0) fields[line.slice(0, at)] = line.slice(at + 2);
          }
          if (fields.event === "end") return onEnd?.();
          if (fields.data) {
            const event = JSON.parse(fields.data);
            last = Number(fields.id) || event.seq || last;
            onEvent(event);
          }
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    } catch (error) {
      if (signal.aborted || error.name === "AbortError") return;
      onStatus?.("reconnecting");
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 2, 8000);
    }
  }
}
