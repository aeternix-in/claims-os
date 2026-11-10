import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { engine, supabase } from '../lib/api';
import QueryEditor, { CODES, QueryStatus, type Query } from '../components/QueryEditor';

/** Every payer query across cases, oldest open first, with the reply editor beside it. */
export default function QueryDesk() {
  const [rows, setRows] = useState<Query[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [showSent, setShowSent] = useState(false);
  const [err, setErr] = useState('');

  const load = () => engine<{ queries: Query[] }>('/queries').then((r) => setRows(r.queries)).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
    const ch = supabase.channel('querydesk').on('postgres_changes', { event: '*', schema: 'public', table: 'queries' }, load).subscribe();
    return () => { supabase.removeChannel(ch); };
  }, []);

  const list = rows?.filter((q) => showSent || q.status !== 'sent') ?? [];
  const cur = rows?.find((q) => q.id === sel) ?? list[0];

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="font-serif text-[28px] leading-tight">Queries</h1><p className="text-ink-3">Payer questions with replies drafted from the record</p></div>
        <label className="flex items-center gap-2 text-ink-2"><input type="checkbox" className="accent-[var(--accent)]" checked={showSent} onChange={(e) => setShowSent(e.target.checked)} />Show answered</label>
      </div>
      {err && <p className="mb-4 rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}
      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <ul className="card h-fit divide-y divide-line">
          {rows === null && <li className="p-4 text-ink-3">Loading…</li>}
          {rows && list.length === 0 && <li className="p-4 text-ink-3">No open queries.</li>}
          {list.map((q) => (
            <li key={q.id}>
              <button onClick={() => setSel(q.id)} className={`w-full px-4 py-3 text-left ${cur?.id === q.id ? 'bg-accent-soft' : 'hover:bg-surface-2'}`}>
                <div className="flex items-center justify-between gap-2"><span className="num text-accent">{q.case_number}</span><QueryStatus s={q.status} /></div>
                <div className="text-[13px]">{q.patient} · {q.payer}</div>
                <div className="truncate text-[12px] text-ink-3">{q.code} {CODES[q.code]}: {q.query_text}</div>
              </button>
            </li>
          ))}
        </ul>
        {cur && (
          <div className="card p-5">
            <div className="mb-3 flex items-center justify-between">
              <Link to={`/case/${cur.case_id}`} className="num text-accent hover:underline">{cur.case_number} · {cur.patient}</Link>
              <span className="text-[12px] text-ink-3">Received {new Date(cur.received_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</span>
            </div>
            <QueryEditor key={cur.id} q={cur} onChange={load} />
          </div>
        )}
      </div>
    </div>
  );
}
