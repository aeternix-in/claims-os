import { useEffect, useState } from 'react';
import { engine } from '../lib/api';
import { useSession } from '../lib/session';

interface Hospital { name: string; city: string | null; beds: number | null; address: string | null; letter_signatory: string | null; hfr_id: string | null;
  nhcx_participant_code: string | null; settings: { rooms?: Record<string, number>; differentialBilling?: boolean; preauthSlaMin?: number; dischargeSlaMin?: number } }
interface NhcxStatus { enabled: boolean; demo_mode: boolean; envelope_mode: string; auth_mode: string; gateway: string; hospital_ready: boolean }
const ROOMS: [string, string][] = [['general', 'General ward'], ['twin', 'Twin sharing'], ['single', 'Single room'], ['deluxe', 'Deluxe room'], ['icu', 'ICU']];

export default function Setup() {
  const { profile } = useSession();
  const can = ['owner', 'admin'].includes(profile?.role ?? '');
  const [h, setH] = useState<Hospital | null>(null);
  const [n, setN] = useState<NhcxStatus | null>(null);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  useEffect(() => { engine<Hospital>('/hospital').then(setH); engine<NhcxStatus>('/nhcx/status').then(setN).catch(() => {}); }, []);
  if (!h) return <p className="text-ink-3">Loading…</p>;
  const set = (k: keyof Hospital, v: unknown) => setH({ ...h, [k]: v });
  const setS = (k: string, v: unknown) => setH({ ...h, settings: { ...h.settings, [k]: v } });
  async function save() {
    setMsg(null);
    try {
      const r = await engine<Hospital>('/hospital', { method: 'PUT', body: JSON.stringify({
        name: h!.name, city: h!.city, beds: h!.beds, address: h!.address, letter_signatory: h!.letter_signatory, hfr_id: h!.hfr_id,
        nhcx_participant_code: h!.nhcx_participant_code || null, rooms: h!.settings.rooms, differential_billing: h!.settings.differentialBilling,
        preauth_sla_min: h!.settings.preauthSlaMin, discharge_sla_min: h!.settings.dischargeSlaMin }) });
      setH(r); setMsg({ text: 'Saved' });
    } catch (e) { setMsg({ text: (e as Error).message, bad: true }); }
  }
  const input = (label: string, value: string | number | null, on: (v: string) => void, type = 'text') => (
    <div><label className="label">{label}</label><input className="input" type={type} disabled={!can} value={value ?? ''} onChange={(e) => on(e.target.value)} /></div>
  );
  return (
    <div className="max-w-4xl space-y-6">
      <div><h1 className="font-serif text-[28px] leading-tight">Setup</h1><p className="text-ink-3">Hospital details, room tariff, clocks and NHCX identity</p></div>
      <section className="card grid gap-4 p-5 sm:grid-cols-2">
        <h2 className="font-medium sm:col-span-2">Hospital</h2>
        {input('Name (appears on every letter)', h.name, (v) => set('name', v))}
        {input('City', h.city, (v) => set('city', v))}
        {input('Beds', h.beds, (v) => set('beds', v ? Number(v) : null), 'number')}
        {input('Letters signed by', h.letter_signatory, (v) => set('letter_signatory', v))}
        <div className="sm:col-span-2"><label className="label">Address (letterhead)</label><textarea className="input h-16" disabled={!can} value={h.address ?? ''} onChange={(e) => set('address', e.target.value)} /></div>
      </section>
      <section className="card p-5">
        <h2 className="mb-3 font-medium">Room tariff (per day)</h2>
        <div className="grid gap-3 sm:grid-cols-5">{ROOMS.map(([k, label]) => input(label, h.settings.rooms?.[k] ?? '', (v) => setS('rooms', { ...h.settings.rooms, [k]: Number(v) }), 'number'))}</div>
        <label className="mt-3 flex items-center gap-2"><input type="checkbox" disabled={!can} className="accent-[var(--accent)]" checked={h.settings.differentialBilling ?? true} onChange={(e) => setS('differentialBilling', e.target.checked)} />
          Charges differ by room category (needed for proportionate deduction to apply)</label>
      </section>
      <section className="card grid gap-4 p-5 sm:grid-cols-2">
        <h2 className="font-medium sm:col-span-2">IRDAI clocks (minutes)</h2>
        {input('Pre-auth decision', h.settings.preauthSlaMin ?? 60, (v) => setS('preauthSlaMin', Number(v)), 'number')}
        {input('Final discharge approval', h.settings.dischargeSlaMin ?? 180, (v) => setS('dischargeSlaMin', Number(v)), 'number')}
      </section>
      <section className="card grid gap-4 p-5 sm:grid-cols-2">
        <div className="sm:col-span-2"><h2 className="font-medium">NHCX identity</h2>
          <p className="text-[13px] text-ink-3">From ABDM onboarding. Needed on every pre-auth and claim sent over the exchange.</p></div>
        {input('Health Facility Registry (HFR) ID', h.hfr_id, (v) => set('hfr_id', v))}
        {input('NHCX participant code (ends in @hcx)', h.nhcx_participant_code, (v) => set('nhcx_participant_code', v))}
        {n && <div className="text-[13px] text-ink-2 sm:col-span-2">
          Exchange: {n.enabled ? <span className="text-good">on</span> : <span className="text-ink-3">off for this deployment</span>}
          {n.enabled && <> · {n.envelope_mode === 'jwe' ? 'encrypted (JWE)' : 'plain envelopes'} · {n.auth_mode === 'session' ? 'ABDM sandbox session' : 'static token'} · {n.gateway}
            {n.demo_mode && <span className="ml-2 rounded bg-warn-soft px-2 py-0.5 text-[11px] text-warn">demo mode: mock insurer</span>}</>}
        </div>}
      </section>
      {can ? <div className="flex items-center gap-3"><button className="btn-primary" onClick={save}>Save setup</button>{msg && <span className={msg.bad ? 'text-crit' : 'text-good'}>{msg.text}</span>}</div>
        : <p className="text-ink-3">Only the owner or an admin can change setup.</p>}
    </div>
  );
}
