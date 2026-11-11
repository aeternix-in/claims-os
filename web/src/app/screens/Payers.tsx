import { useEffect, useState } from 'react';
import { engine, supabase } from '../lib/api';
import { useSession } from '../lib/session';
import { Scorecards } from './Reports';

interface Rules {
  basis: 'itemised' | 'package'; mou_discount_pct: number; discount_excludes: string[]; applies_proportionate: boolean;
  package_rates: Record<string, number>; tds_pct: number; payment_term_days: number; claim_file_window_days: number;
}
interface Payer { id: string; name: string; kind: string; via_tpa: string | null; rules: Rules; nhcx_participant_code: string | null; nhcx_processing_code: string | null }
const EXCL = ['pharmacy', 'implants', 'consumables', 'investigations'];

function Editor({ p, canEdit, onSaved }: { p: Payer; canEdit: boolean; onSaved: () => void }) {
  const [r, setR] = useState<Rules>(p.rules);
  const [pkg, setPkg] = useState(Object.entries(p.rules.package_rates).map(([k, v]) => `${k}=${v}`).join('\n'));
  const [msg, setMsg] = useState('');
  const [identMsg, setIdentMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const n = (k: keyof Rules, label: string, hint: string) => (
    <div><label className="label">{label}</label>
      <input className="input" type="number" min={0} step={0.5} disabled={!canEdit} value={r[k] as number} onChange={(e) => setR({ ...r, [k]: Number(e.target.value) })} />
      <span className="text-[12px] text-ink-3">{hint}</span></div>
  );
  const [ident, setIdent] = useState({ name: p.name, via_tpa: p.via_tpa ?? '', nhcx_participant_code: p.nhcx_participant_code ?? '', nhcx_processing_code: p.nhcx_processing_code ?? '' });
  async function saveIdent() {
    setIdentMsg(null);
    try { await engine(`/payers/${p.id}`, { method: 'PUT', body: JSON.stringify({ ...ident, via_tpa: ident.via_tpa || null,
      nhcx_participant_code: ident.nhcx_participant_code || null, nhcx_processing_code: ident.nhcx_processing_code || null }) }); setIdentMsg({ text: 'Saved' }); onSaved(); }
    catch (e) { setIdentMsg({ text: (e as Error).message, bad: true }); }
  }
  async function save() {
    setMsg('');
    const package_rates = Object.fromEntries(pkg.split('\n').map((l) => l.split('=').map((s) => s.trim())).filter(([k, v]) => k && v && !isNaN(Number(v))).map(([k, v]) => [k, Number(v)]));
    try { await engine(`/payers/${p.id}/rules`, { method: 'PUT', body: JSON.stringify({ ...r, package_rates }) }); setMsg('Saved'); onSaved(); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="card space-y-4 p-5">
      <div><h2 className="font-serif text-[22px]">{p.name}</h2><p className="text-ink-3 capitalize">{p.kind}{p.via_tpa ? ` · via ${p.via_tpa}` : ''}</p></div>
      <div className="grid gap-4 border-b border-line pb-4 sm:grid-cols-4">
        {([['name', 'Name'], ['via_tpa', 'TPA'], ['nhcx_participant_code', 'NHCX code (…@hcx)'], ['nhcx_processing_code', 'NHCX processing code (TPA)']] as const).map(([k, label]) => (
          <div key={k}><label className="label">{label}</label><input className="input" disabled={!canEdit} value={ident[k]} onChange={(e) => setIdent({ ...ident, [k]: e.target.value })} /></div>
        ))}
        {canEdit && <div className="flex items-center gap-3 sm:col-span-4"><button className="btn-ghost w-fit" onClick={saveIdent}>Save payer</button>
          {identMsg && <span className={identMsg.bad ? 'text-crit' : 'text-good'}>{identMsg.text}</span>}</div>}
        {!canEdit && <p className="text-[13px] text-ink-3 sm:col-span-4">Only the owner, an admin or accounts can change payers.</p>}
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div><label className="label">How bills are paid</label>
          <select className="input" disabled={!canEdit} value={r.basis} onChange={(e) => setR({ ...r, basis: e.target.value as Rules['basis'] })}>
            <option value="itemised">Itemised, less agreed discount</option><option value="package">Package rates</option></select></div>
        {n('mou_discount_pct', 'Agreed discount (%)', 'On itemised bills')}
        {n('tds_pct', 'TDS (%)', 'Section 194J')}
        {n('payment_term_days', 'Payment term (days)', 'From a complete claim file')}
        {n('claim_file_window_days', 'Filing window (days)', 'From discharge')}
        <label className="flex items-center gap-2 self-end pb-2"><input type="checkbox" disabled={!canEdit} className="accent-[var(--accent)]"
          checked={r.applies_proportionate} onChange={(e) => setR({ ...r, applies_proportionate: e.target.checked })} />Applies proportionate deduction</label>
      </div>
      <fieldset><legend className="label">Discount does not apply to</legend>
        <div className="flex flex-wrap gap-4">{EXCL.map((c) => (
          <label key={c} className="flex items-center gap-2 capitalize"><input type="checkbox" disabled={!canEdit} className="accent-[var(--accent)]"
            checked={r.discount_excludes.includes(c)} onChange={(e) => setR({ ...r, discount_excludes: e.target.checked ? [...r.discount_excludes, c] : r.discount_excludes.filter((x) => x !== c) })} />{c}</label>))}
        </div></fieldset>
      <div><label className="label">Package rates (one per line: procedure=amount)</label>
        <textarea className="input num h-28" disabled={!canEdit} value={pkg} onChange={(e) => setPkg(e.target.value)} placeholder={'cataract=28000\nlapchole=60000'} /></div>
      {canEdit && <div className="flex items-center gap-3"><button className="btn-primary" onClick={save}>Save rulebook</button><span className="text-ink-3">{msg}</span></div>}
    </div>
  );
}

export default function Payers() {
  const { profile } = useSession();
  const [payers, setPayers] = useState<Payer[]>([]);
  const [sel, setSel] = useState<string>('');
  const load = () => supabase.from('payers').select('*').order('name').then(({ data }) => { setPayers((data ?? []) as Payer[]); if (!sel && data?.[0]) setSel(data[0].id); });
  useEffect(() => { load(); }, []);
  const p = payers.find((x) => x.id === sel);
  return (
    <div>
      <h1 className="font-serif text-[28px] leading-tight">Payers</h1>
      <p className="mb-5 text-ink-3">Scorecards, and the rulebook of MoU terms the bill audit uses</p>
      <div className="mb-6"><Scorecards /></div>
      <h2 className="mb-2 font-medium">Rulebook</h2>
      <div className="grid gap-6 md:grid-cols-[220px_1fr]">
        <ul className="card h-fit divide-y divide-line">
          {payers.map((x) => <li key={x.id}><button onClick={() => setSel(x.id)}
            className={`w-full px-4 py-2 text-left ${x.id === sel ? 'bg-accent-soft text-accent' : 'hover:bg-surface-2'}`}>{x.name}</button></li>)}
        </ul>
        {p && <Editor key={p.id} p={p} canEdit={['owner', 'admin', 'accounts'].includes(profile?.role ?? '')} onSaved={load} />}
      </div>
    </div>
  );
}
