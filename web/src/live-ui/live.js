/*
 * Live adapter for the CTO's UI: login, load from the engine, save changed claims, pick up other users' changes.
 * The UI (src/demo-ui/app.js) calls LIVE.load / LIVE.save / LIVE.signOut through window.__AETERNIX_LIVE__.
 */
import { createClient } from "@supabase/supabase-js";

const ENGINE = import.meta.env.VITE_ENGINE_URL;
export const supabase = createClient(import.meta.env.VITE_SUPABASE_URL, import.meta.env.VITE_SUPABASE_ANON_KEY);

const VOLATILE = new Set(["lastActiveAt", "clockOffset", "claims", "order"]);   // never trigger a hospital save
// settings that belong to the person and device, not the hospital
const PERSONAL = ["role", "theme", "tour"];
const sharedSettings = (st) => { const o = { ...(st || {}) }; for (const k of PERSONAL) delete o[k]; return JSON.stringify(o); };
const THEME_KEY = "aeternix-theme";
const myTheme = () => { try { return localStorage.getItem(THEME_KEY) || "system"; } catch { return "system"; } };
const ROLE = { desk: "desk", doctor: "doctor", accounts: "accounts", owner: "owner", admin: "owner" };

async function call(path, init = {}) {
  const { data } = await supabase.auth.getSession();
  const res = await fetch(`${ENGINE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${data.session?.access_token ?? ""}`, ...init.headers },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail;
    const err = new Error(d?.message ?? (typeof d === "string" ? d : res.statusText));
    err.status = res.status;
    err.code = d?.code;
    throw err;
  }
  return body;
}

function orderOf(claims) {
  return Object.values(claims).sort((a, b) => (b.seq ?? 0) - (a.seq ?? 0) || b.admittedAt - a.admittedAt).map((c) => c.id);
}

// A claim record must have the arrays the UI walks over; anything else (a malformed or test row) is ignored.
const isClaim = (d) =>
  d && d.patient && d.policy && ["auths", "queries", "events", "bill", "docs", "disputes", "payments", "awaiting"].every((k) => Array.isArray(d[k]));

export function makeLive(ui) {
  // ui: { setState, getState, toast } from the UI module, supplied after import
  const versions = { state: 0, claims: {} };
  let saved = null;            // the data object last known to match the server
  let since = null;
  let queue = Promise.resolve();
  let pending = 0;

  const live = {
    me: { id: null, role: "desk", name: "" },

    async load({ emptyDesk }) {
      const r = await call("/ui/state");
      live.me = r.me;
      since = r.server_time;
      let data;
      if (r.state) {
        data = { ...r.state };
        versions.state = r.version;
      } else {
        // first use for this hospital: an empty desk in the demo's own shape, then saved
        data = emptyDesk(Date.now());
        Object.assign(data.hospital, { name: r.hospital.name, city: r.hospital.city || "", beds: r.hospital.beds || data.hospital.beds });
        if (r.hospital.settings?.rooms) data.hospital.rooms = { ...data.hospital.rooms, ...r.hospital.settings.rooms };
        data.settings.autopilot = false;
        data.settings.admissionsPerDay = 0;
        data.world.nextArrivalAt = Date.now() + 3650 * 864e5;
        data.claims = {};
        data.order = [];
        const { claims, order, ...doc } = data;
        versions.state = (await call("/ui/state", { method: "PUT", body: JSON.stringify({ doc, base_version: 0 }) })).version;
      }
      data.claims = {};
      for (const c of r.claims) {
        if (!isClaim(c.doc)) continue; // skip anything that is not a full claim record
        data.claims[c.id] = c.doc;
        versions.claims[c.id] = c.version;
      }
      data.order = orderOf(data.claims);
      data.clockOffset = 0;
      data.settings = { ...data.settings, autopilot: false, role: ROLE[r.me.role] || "desk", theme: myTheme() };
      saved = data;
      setInterval(() => live.pull(), 5000);
      return data;
    },

    // true while a change is waiting to be sent or is on its way
    busy: () => live.dirty || pending > 0,
    flush() {
      if (live.dirty) { live.dirty = false; live.save(ui.getState().data); }
    },

    save(data) {
      // serialise saves; each compares against the last state known to be on the server
      pending++;
      queue = queue.then(() => live._save(data)).catch((e) => ui.toast({ tone: "crit", title: "Not saved", body: e.message })).finally(() => pending--);
      return queue;
    },

    async _save(data) {
      const prev = saved;
      saved = data;
      for (const id of data.order) {
        const doc = data.claims[id];
        if (prev && prev.claims[id] === doc) continue;
        try {
          versions.claims[id] = (await call(`/ui/claims/${encodeURIComponent(id)}`, {
            method: "PUT", body: JSON.stringify({ doc, base_version: versions.claims[id] ?? 0 }),
          })).version;
        } catch (e) {
          if (e.status === 409 || e.code === "history_changed") await live.reloadClaim(id, e.message);
          else throw e;
        }
      }
      if (prev && prev.settings !== data.settings && data.settings.theme !== prev.settings.theme) {
        try { localStorage.setItem(THEME_KEY, data.settings.theme); } catch {}
      }
      const changed = Object.keys(data).some((k) => !VOLATILE.has(k) && (!prev || prev[k] !== data[k]) &&
        (k !== "settings" || !prev || sharedSettings(prev.settings) !== sharedSettings(data.settings)));
      if (changed) {
        const { claims, order, lastActiveAt, clockOffset, ...doc } = data;
        try {
          versions.state = (await call("/ui/state", { method: "PUT", body: JSON.stringify({ doc, base_version: versions.state }) })).version;
        } catch (e) {
          if (e.status === 403) { await live.reloadState(); throw e; }
          if (e.status !== 409) throw e;
          await live.pull(true);
          ui.toast({ tone: "warn", title: "Updated by someone else", body: "Hospital settings were reloaded. Please redo your last change." });
        }
      }
    },

    // put the hospital record back to what the server holds (after a refused change)
    async reloadState() {
      const r = await call("/ui/state");
      if (!r.state) return;
      const st = ui.getState();
      const data = { ...st.data, ...r.state, claims: st.data.claims, order: st.data.order,
        settings: { ...r.state.settings, autopilot: false, role: st.data.settings.role, theme: st.data.settings.theme, tour: st.data.settings.tour } };
      versions.state = r.version;
      saved = data;
      ui.syncMasters?.(data);
      ui.setState({ data });
    },

    async reloadClaim(id, why) {
      const r = await call("/ui/state");
      const c = r.claims.find((x) => x.id === id);
      const st = ui.getState();
      const claims = { ...st.data.claims };
      if (c) { claims[id] = c.doc; versions.claims[id] = c.version; } else delete claims[id];
      const data = { ...st.data, claims, order: orderOf(claims) };
      saved = data;
      ui.setState({ data });
      ui.toast({ tone: "warn", title: "Claim reloaded", body: why || "Someone else changed this claim. Please redo your last change." });
    },

    async pull(force = false) {
      if (!since || (pending && !force) || document.hidden) return;
      let r;
      try { r = await call(`/ui/changes?since=${encodeURIComponent(since)}`); } catch { return; }
      since = r.server_time;
      if (!r.claims.length && !r.state) return;
      const st = ui.getState();
      let data = st.data;
      if (r.claims.length) {
        const claims = { ...data.claims };
        const flash = { ...st.flash };
        for (const c of r.claims) {
          if ((versions.claims[c.id] ?? 0) >= c.version || !isClaim(c.doc)) continue;
          claims[c.id] = c.doc;
          versions.claims[c.id] = c.version;
          flash[c.id] = Date.now();
        }
        data = { ...data, claims, order: orderOf(claims) };
        ui.setState({ flash });
      }
      if (r.state && r.state.version > versions.state) {
        data = { ...data, ...r.state.doc, claims: data.claims, order: data.order, settings: { ...r.state.doc.settings, autopilot: false, role: data.settings.role, theme: data.settings.theme, tour: data.settings.tour } };
        versions.state = r.state.version;
      }
      saved = data;
      ui.syncMasters?.(data);
      ui.setState({ data });
    },

    // files on the Documents tab: stored privately by the engine, text and key fields read on upload
    files: {
      async upload(claimId, docId, file) {
        const { data } = await supabase.auth.getSession();
        const form = new FormData();
        form.append("claim_id", claimId);
        form.append("doc_id", docId);
        form.append("file", file, file.name);
        const res = await fetch(`${ENGINE}/ui/files`, { method: "POST", body: form, headers: { Authorization: `Bearer ${data.session?.access_token ?? ""}` } });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.message || res.statusText);
        return body;
      },
      async open(docId) {
        const { data } = await supabase.auth.getSession();
        const res = await fetch(`${ENGINE}/ui/files/by-doc/${encodeURIComponent(docId)}`, { headers: { Authorization: `Bearer ${data.session?.access_token ?? ""}` } });
        if (!res.ok) return null;
        if ((res.headers.get("content-type") || "").includes("application/json")) {
          const j = await res.json();
          const f = await fetch(j.url);
          return f.ok ? URL.createObjectURL(await f.blob()) : null;
        }
        return URL.createObjectURL(await res.blob());
      },
      async remove(docId) {
        try { await call(`/ui/files/by-doc/${encodeURIComponent(docId)}`, { method: "DELETE" }); } catch {}
      },
    },

    async signOut() {
      await supabase.auth.signOut();
      location.reload();
    },
  };
  return live;
}
