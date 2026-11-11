/*
 * Live mode: the CTO's UI on real data. Sign in with Supabase, then the UI loads and saves through the engine.
 */
import "../demo-ui/app.css";
import wordmark from "../demo-ui/aeternix-wordmark-white.png";
import { makeLive, supabase } from "./live.js";

const root = document.getElementById("root");

function loginScreen(message = "") {
  root.innerHTML = `
  <div style="min-height:100dvh;display:grid;place-items:center;background:var(--rail);padding:16px;font-family:'IBM Plex Sans',system-ui,sans-serif">
    <form id="login" style="width:100%;max-width:380px;background:var(--surface);border-radius:8px;overflow:hidden">
      <div style="background:var(--rail);padding:20px 24px 16px"><img src="${wordmark}" alt="Aeternix" style="height:10px"></div>
      <div style="padding:24px;display:flex;flex-direction:column;gap:12px">
        <h1 style="font-family:'EB Garamond',serif;font-size:26px;font-weight:500;color:var(--ink);margin:0">Sign in to Claims OS</h1>
        <label style="font-size:13px;color:var(--ink-2)">Work email<input class="field" style="margin-top:4px" type="email" name="email" required autocomplete="username"></label>
        <label style="font-size:13px;color:var(--ink-2)">Password<input class="field" style="margin-top:4px" type="password" name="password" required autocomplete="current-password"></label>
        <p id="err" style="display:${message ? "block" : "none"};margin:0;padding:8px 10px;border-radius:6px;background:var(--crit-soft);color:var(--crit);font-size:13px">${message}</p>
        <button style="height:36px;border:0;border-radius:6px;background:var(--accent);color:var(--accent-ink);font-weight:500;font-size:14px;cursor:pointer">Sign in</button>
      </div>
    </form>
  </div>`;
  root.querySelector("#login").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const btn = ev.target.querySelector("button");
    btn.disabled = true; btn.textContent = "Signing in…";
    const { error } = await supabase.auth.signInWithPassword({ email: f.get("email"), password: f.get("password") });
    if (error) return loginScreen(error.message);
    start();
  });
}

function notice(text) {
  root.innerHTML = `<div style="min-height:100dvh;display:grid;place-items:center;padding:16px;font-family:'IBM Plex Sans',system-ui,sans-serif;color:var(--ink-2);text-align:center">${text}</div>`;
}

async function start() {
  const { data } = await supabase.auth.getSession();
  if (!data.session) return loginScreen();
  const ui = {};
  const live = makeLive(ui);
  window.__AETERNIX_LIVE__ = live;
  // send a waiting change straight away if the tab is hidden, and warn before closing with one unsent
  document.addEventListener("visibilitychange", () => document.hidden && live.flush());
  window.addEventListener("beforeunload", (e) => {
    live.flush();
    if (live.busy()) { e.preventDefault(); e.returnValue = ""; }
  });
  try {
    await live.preflight?.();
  } catch {}
  const app = await import("../demo-ui/app.js");
  Object.assign(ui, { setState: app.__setState, getState: app.__getState, toast: app.__toast, syncMasters: app.__syncMasters });
  root.innerHTML = "";
  try {
    app.startClaimsOS();
  } catch (e) {
    notice(`Could not start: ${e.message}`);
  }
}

window.addEventListener("unhandledrejection", (e) => {
  const m = e.reason?.message || "";
  if (e.reason?.status === 403) notice("Your account isn't linked to a hospital yet. Ask an admin to add you.");
  else if (e.reason?.status === 401) loginScreen("Your session expired. Please sign in again.");
  else if (/Failed to fetch/.test(m)) notice("Can't reach the claims engine. Check that it is running.");
});

start();
