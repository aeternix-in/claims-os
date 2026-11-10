import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { engine, rupees, supabase } from '../lib/api';

interface Letter {
  id: string; case_id: string; case_number: string; patient: string; payer: string; kind: string; subject: string; body: string;
  reason: string; sources: string[]; amount: number | null; status: 'draft' | 'approved' | 'sent'; placeholders: string[]; created_at: string;
}
const KIND: Record<string, string> = { reminder: 'Reminder', grievance: 'Grievance', dispute: 'Short-payment dispute', appeal: 'Appeal', condonation: 'Condonation' };

async function openPdf(id: string) {
  const { data } = await supabase.auth.getSession();
  const r = await fetch(`${import.meta.env.VITE_ENGINE_URL}/letters/${id}/pdf`, { headers: { Authorization: `Bearer ${data.session?.access_token ?? ''}` } });
  if (r.ok) window.open(URL.createObjectURL(await r.blob()), '_blank', 'noopener');
}

function LetterCard({ l, onChange }: { l: Letter; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState(l.body);
  const [err, setErr] = useState('');
  useEffect(() => setBody(l.body), [l.body]);
  const act = async (path: string, init?: RequestInit) => {
    setErr('');
    try { await engine(`/letters/${l.id}${path}`, init ?? { method: 'POST' }); onChange(); } catch (e) { setErr((e as Error).message); }
  };
  const left = body.match(/\[[^\]\n]{3,}\]/g) ?? [];
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium">{KIND[l.kind]} · <Link to={`/case/${l.case_id}`} className="num text-accent hover:underline">{l.case_number}</Link></div>
          <div className="text-[13px] text-ink-3">{l.payer} · {l.reason}{l.amount ? ` · ${rupees(l.amount)}` : ''}</div>
        </div>
        <div className="flex gap-2">
          <button className="btn-ghost py-1" onClick={() => setOpen(!open)}>{open ? 'Close' : 'Open'}</button>
          {l.status === 'draft' && <button className="btn-primary py-1" disabled={left.length > 0 || body !== l.body} onClick={() => act('/approve')}>Approve</button>}
          {l.status === 'approved' && <button className="btn-primary py-1" onClick={() => act('/sent')}>Mark sent</button>}
          {l.status === 'sent' && <span className="text-[13px] text-good">Sent</span>}
        </div>
      </div>
      {open && (
        <div className="mt-3 space-y-2">
          <textarea className="input num h-64 text-[13px] leading-5" readOnly={l.status === 'sent'} value={body} onChange={(e) => setBody(e.target.value)} aria-label="Letter" />
          {left.length > 0 && <p className="text-[13px] text-warn">{left.length} bracketed item(s) to write before approval.</p>}
          <div className="text-[12px] text-ink-3">Sources: {l.sources.join(' · ')}</div>
          <div className="flex flex-wrap gap-2">
            {l.status !== 'sent' && <button className="btn-ghost py-1" disabled={body === l.body} onClick={() => act('', { method: 'PUT', body: JSON.stringify({ body }) })}>Save</button>}
            <button className="btn-ghost py-1" onClick={() => openPdf(l.id)}>PDF</button>
            {l.status !== 'sent' && <button className="btn-ghost py-1 text-crit" onClick={() => act('/drop')}>Drop</button>}
          </div>
        </div>
      )}
      {err && <p className="mt-2 text-[13px] text-crit">{err}</p>}
    </li>
  );
}

/** Follow-up letters drafted by the ageing sweep. Nothing goes out until a person approves it. */
export default function LettersPanel() {
  const [rows, setRows] = useState<Letter[] | null>(null);
  const [msg, setMsg] = useState('');
  const load = () => engine<{ letters: Letter[] }>('/letters').then((r) => setRows(r.letters)).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  async function sweep() {
    setMsg('');
    try { const r = await engine<{ drafted: unknown[] }>('/letters/sweep', { method: 'POST' }); setMsg(`${r.drafted.length} new letter(s) drafted`); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  const pending = rows?.filter((l) => l.status !== 'sent') ?? [];
  return (
    <section className="card mb-6 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="font-medium">Letters to send</h2><p className="text-ink-3">Reminders, grievances, disputes, appeals and condonation requests from the ageing sweep</p></div>
        <div className="flex items-center gap-3"><span className="text-[13px] text-ink-3">{msg}</span><button className="btn-ghost" onClick={sweep}>Run the sweep</button></div>
      </div>
      {rows && pending.length === 0 && <p className="mt-3 text-ink-3">Nothing due.</p>}
      <ul className="divide-y divide-line">{pending.map((l) => <LetterCard key={l.id} l={l} onChange={load} />)}</ul>
    </section>
  );
}
