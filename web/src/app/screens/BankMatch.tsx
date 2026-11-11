import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { engine, engineUpload, rupees, supabase } from '../lib/api';
import { useSession } from '../lib/session';

interface Line { case_id: string; case_number: string; amount: number; expected: number }
interface Proposal { credit_id: string; confidence: 'exact' | 'likely' | 'split'; why: string; lines: Line[]; shortfall: number }
interface Credit {
  id: string; value_date: string; amount: number; narration: string; utr: string | null; payer: string | null;
  status: 'unmatched' | 'matched' | 'ignored'; ignored_note: string | null; is_sample: boolean; proposal: Proposal | null;
  matches: { case_id: string; case_number: string; amount: number; confidence: string; why: string }[];
}
interface OpenCase { id: string; case_number: string; payer: string; patient: string; expected: number; settled: number; payer_ref: string | null }
interface Data {
  credits: Credit[]; open_cases: OpenCase[];
  stats: { unmatched_count: number; unmatched_amount: number; exact: number; likely: number; no_clue: number; awaiting_amount: number; awaiting_count: number };
}

const CONF: Record<string, [string, string]> = {
  exact: ['Certain', 'bg-good-soft text-good'], likely: ['Likely', 'bg-accent-soft text-accent'],
  split: ['Split', 'bg-accent-soft text-accent'], manual: ['By hand', 'bg-surface-3 text-ink-2'],
};
const Badge = ({ c }: { c: string }) => <span className={`whitespace-nowrap rounded px-2 py-0.5 text-[12px] font-medium ${CONF[c][1]}`}>{CONF[c][0]}</span>;
const day = (d: string) => new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="card p-4"><div className="label">{label}</div><div className="num text-[24px] leading-8">{value}</div>{sub && <div className="text-[12px] text-ink-3">{sub}</div>}</div>;
}

/** Match by hand: pick claims; amounts default to what each claim expects, and must add up to the credit. */
function ManualMatch({ cr, cases, onDone }: { cr: Credit; cases: OpenCase[]; onDone: () => void }) {
  const [pick, setPick] = useState<Record<string, string>>({});
  const [note, setNote] = useState('');
  const [mode, setMode] = useState<'match' | 'ignore'>('match');
  const [err, setErr] = useState('');
  const total = Object.values(pick).reduce((a, b) => a + (Number(b) || 0), 0);
  const ok = Object.keys(pick).length > 0 && Math.abs(total - cr.amount) <= Object.keys(pick).length;
  const list = [...cases].sort((a, b) => Math.abs(a.expected - cr.amount) - Math.abs(b.expected - cr.amount));
  async function go() {
    setErr('');
    try {
      if (mode === 'ignore') await engine(`/bank/credits/${cr.id}/ignore`, { method: 'POST', body: JSON.stringify({ note }) });
      else await engine('/bank/confirm', { method: 'POST', body: JSON.stringify({ credit_id: cr.id, confidence: 'manual', why: note || 'Matched by hand',
        lines: Object.entries(pick).map(([case_id, a]) => ({ case_id, amount: Number(a) })) }) });
      onDone();
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="mt-2 space-y-2 rounded-md border border-line bg-surface-2 p-3">
      <div className="flex gap-3 text-[13px]">
        <label className="flex items-center gap-1"><input type="radio" checked={mode === 'match'} onChange={() => setMode('match')} className="accent-[var(--accent)]" />Match to claims</label>
        <label className="flex items-center gap-1"><input type="radio" checked={mode === 'ignore'} onChange={() => setMode('ignore')} className="accent-[var(--accent)]" />Not a claim payment</label>
      </div>
      {mode === 'match' && (
        <div className="max-h-56 overflow-y-auto">
          {list.length === 0 && <p className="text-ink-3">No settled claims are waiting for money.</p>}
          {list.map((c) => (
            <label key={c.id} className="flex items-center gap-2 py-1 text-[13px]">
              <input type="checkbox" className="accent-[var(--accent)]" checked={c.id in pick}
                onChange={(e) => { const p = { ...pick }; if (e.target.checked) p[c.id] = String(Math.min(c.expected, Math.max(0, cr.amount - total))); else delete p[c.id]; setPick(p); }} />
              <span className="num w-28">{c.case_number}</span><span className="flex-1 truncate text-ink-2">{c.payer} · {c.patient}</span>
              <span className="num text-ink-3">expects {rupees(c.expected)}</span>
              {c.id in pick && <input className="input num w-28 py-0.5 text-right" type="number" value={pick[c.id]} onChange={(e) => setPick({ ...pick, [c.id]: e.target.value })} aria-label={`Amount for ${c.case_number}`} />}
            </label>
          ))}
        </div>
      )}
      <input className="input py-1 text-[13px]" placeholder={mode === 'ignore' ? 'What is it? e.g. patient deposit, refund, interest' : 'Note (optional)'} value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="flex items-center gap-3">
        <button className="btn-primary py-1" disabled={mode === 'match' ? !ok : note.trim().length < 3} onClick={go}>{mode === 'match' ? 'Confirm match' : 'Set aside'}</button>
        {mode === 'match' && <span className={`num text-[13px] ${ok ? 'text-good' : 'text-ink-3'}`}>{rupees(total)} of {rupees(cr.amount)}</span>}
      </div>
      {err && <p className="text-[13px] text-crit">{err}</p>}
    </div>
  );
}

export default function BankMatch() {
  const { profile } = useSession();
  const can = ['accounts', 'owner', 'admin'].includes(profile?.role ?? '');
  const [d, setD] = useState<Data | null>(null);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  const load = () => engine<Data>('/bank').then(setD).catch((e) => setMsg({ text: e.message, bad: true }));
  useEffect(() => {
    load();
    const ch = supabase.channel('bank').on('postgres_changes', { event: '*', schema: 'public', table: 'bank_credits' }, load).subscribe();
    return () => { supabase.removeChannel(ch); };
  }, []);

  async function run(fn: () => Promise<string>) {
    setBusy(true); setMsg(null);
    try { setMsg({ text: await fn() }); } catch (e) { setMsg({ text: (e as Error).message, bad: true }); }
    setBusy(false); load();
  }
  const imported = (r: { credits_new: number; duplicates: number; skipped: number }) =>
    `${r.credits_new} new credit(s)` + (r.duplicates ? `, ${r.duplicates} already imported` : '') + (r.skipped ? `, ${r.skipped} debit/unreadable row(s) skipped` : '');
  const upload = (f: File) => run(async () => { const fd = new FormData(); fd.append('file', f); return imported(await engineUpload('/bank/import', fd)); });
  const sample = () => run(async () => imported(await engine('/bank/sample-statement', { method: 'POST' })));
  const bulk = (levels: string[]) => run(async () => {
    const r = await engine<{ confirmed: number; shortfalls: { case_number: string; shortfall: number }[] }>('/bank/confirm-proposals', { method: 'POST', body: JSON.stringify({ levels }) });
    return `${r.confirmed} credit(s) matched` + (r.shortfalls.length ? `. Short-paid: ${r.shortfalls.map((s) => `${s.case_number} (${rupees(s.shortfall)})`).join(', ')}; the letter sweep will draft disputes.` : '');
  });
  const confirmOne = (p: Proposal) => run(async () => {
    await engine('/bank/confirm', { method: 'POST', body: JSON.stringify({ credit_id: p.credit_id, confidence: p.confidence, why: p.why, lines: p.lines.map((l) => ({ case_id: l.case_id, amount: l.amount })) }) });
    return 'Matched';
  });

  if (!d) return <p className="text-ink-3">{msg?.text ?? 'Loading…'}</p>;
  const s = d.stats;
  const unmatched = d.credits.filter((c) => c.status === 'unmatched');
  const done = d.credits.filter((c) => c.status !== 'unmatched');

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="font-serif text-[28px] leading-tight">Bank match</h1><p className="text-ink-3">Match each credit to its claims. Only a matched credit closes a claim.</p></div>
        {can && (
          <div className="flex flex-wrap gap-2">
            <input ref={file} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <button className="btn-primary" disabled={busy} onClick={() => file.current?.click()}>Import a statement (CSV)</button>
            <button className="btn-ghost" disabled={busy} onClick={sample} title="Demo only: builds a statement from settled claims">Use a sample statement</button>
          </div>
        )}
      </div>
      {msg && <p className={`rounded-md px-3 py-2 ${msg.bad ? 'bg-crit-soft text-crit' : 'bg-good-soft text-good'}`}>{msg.text}</p>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="In the bank, not matched" value={rupees(s.unmatched_amount)} sub={`${s.unmatched_count} credit(s)`} />
        <Tile label="Certain matches" value={String(s.exact)} sub="Payer reference in the narration" />
        <Tile label="Likely or split" value={String(s.likely)} sub={`${s.no_clue} with no clue`} />
        <Tile label="Settled, awaiting money" value={rupees(s.awaiting_amount)} sub={`${s.awaiting_count} claim(s), after TDS`} />
      </div>

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="font-medium">Credits to match</h2>
          {can && <div className="flex gap-2">
            <button className="btn-primary py-1" disabled={busy || !s.exact} onClick={() => bulk(['exact'])}>Match certain ({s.exact})</button>
            <button className="btn-ghost py-1" disabled={busy || !s.likely} onClick={() => bulk(['likely', 'split'])}>Match likely ({s.likely})</button>
          </div>}
        </div>
        {unmatched.length === 0 && <p className="px-4 py-6 text-center text-ink-3">Nothing to match. Import a statement.</p>}
        <ul className="divide-y divide-line">
          {unmatched.map((cr) => (
            <li key={cr.id} className="px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2"><span className="num font-medium">{rupees(cr.amount)}</span><span className="text-[12px] text-ink-3">{day(cr.value_date)}</span>
                    {cr.payer && <span className="text-[12px] text-ink-2">{cr.payer}</span>}{cr.is_sample && <span className="text-[11px] text-warn">sample</span>}</div>
                  <div className="num truncate text-[12px] text-ink-3" title={cr.narration}>{cr.narration}</div>
                  {cr.proposal && (
                    <div className="mt-1 text-[13px]">
                      <Badge c={cr.proposal.confidence} /> → {cr.proposal.lines.map((l) => <span key={l.case_id} className="num mr-2"><Link to={`/case/${l.case_id}`} className="text-accent hover:underline">{l.case_number}</Link> {cr.proposal!.lines.length > 1 && rupees(l.amount)}</span>)}
                      <div className="text-[12px] text-ink-3">{cr.proposal.why}</div>
                      {cr.proposal.shortfall > 0 && <div className="text-[12px] text-warn">Short by {rupees(cr.proposal.shortfall)}: a dispute letter will be drafted after matching</div>}
                    </div>
                  )}
                </div>
                {can && <div className="flex gap-2">
                  {cr.proposal && <button className="btn-primary py-1" disabled={busy} onClick={() => confirmOne(cr.proposal!)}>Confirm</button>}
                  <button className="btn-ghost py-1" onClick={() => setOpen(open === cr.id ? null : cr.id)}>{cr.proposal ? 'Other' : 'Match by hand'}</button>
                </div>}
              </div>
              {open === cr.id && <ManualMatch cr={cr} cases={d.open_cases} onDone={() => { setOpen(null); load(); }} />}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <button className="flex w-full items-center justify-between px-4 py-3 text-left" onClick={() => setShowDone(!showDone)}>
          <h2 className="font-medium">Matched and set aside ({done.length})</h2><span className="text-ink-3">{showDone ? 'Hide' : 'Show'}</span>
        </button>
        {showDone && <ul className="divide-y divide-line border-t border-line">
          {done.map((cr) => (
            <li key={cr.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-[13px]">
              <span><span className="num">{rupees(cr.amount)}</span> <span className="text-ink-3">{day(cr.value_date)} · {cr.narration.slice(0, 50)}</span></span>
              {cr.status === 'ignored' ? <span className="text-ink-3">Set aside: {cr.ignored_note}</span>
                : <span>{cr.matches.map((m) => <span key={m.case_id} className="mr-2"><Badge c={m.confidence} /> <Link to={`/case/${m.case_id}`} className="num text-accent hover:underline">{m.case_number}</Link></span>)}</span>}
            </li>
          ))}
        </ul>}
      </section>
    </div>
  );
}
