import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { supabase, rupees } from '../lib/api';
import type { CaseRow, Stage } from '../lib/types';
import { StageBadge } from '../screens/Desk';
import { ClockText } from './ClockText';

interface Props { title: string; hint: string; stages?: Stage[]; showClock?: boolean; action?: React.ReactNode; empty?: string }

/** One worklist, filtered by stage. Used by Claims desk, Pre-auth, Queries, Discharge, Claim to cash. */
export default function Worklist({ title, hint, stages, showClock, action, empty }: Props) {
  const [rows, setRows] = useState<CaseRow[] | null>(null);
  const [clocks, setClocks] = useState<Record<string, string>>({});
  const [err, setErr] = useState('');
  const key = stages?.join(',') ?? 'all';

  useEffect(() => {
    async function load() {
      let q = supabase.from('cases').select('*, patients(full_name), payers(name)').order('updated_at', { ascending: false }).limit(300);
      if (stages) q = q.in('stage', stages);
      const { data, error } = await q;
      if (error) { setErr(error.message); return; }
      setRows(data as CaseRow[]);
      if (showClock) {
        const { data: k } = await supabase.from('clocks').select('case_id,due_at').is('stopped_at', null);
        setClocks(Object.fromEntries((k ?? []).map((x) => [x.case_id, x.due_at])));
      }
    }
    load();
    const ch = supabase.channel(`wl-${key}`)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'cases' }, load)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'clocks' }, load).subscribe();
    return () => { supabase.removeChannel(ch); };
  }, [key, showClock]);

  const sorted = showClock && rows
    ? [...rows].sort((a, b) => (clocks[a.id] ?? '9').localeCompare(clocks[b.id] ?? '9'))
    : rows;

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="font-serif text-[28px] leading-tight">{title}</h1><p className="text-ink-3">{hint}</p></div>
        {action}
      </div>
      {err && <p className="mb-4 rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[640px] text-left">
          <thead className="border-b border-line text-[12px] uppercase tracking-wide text-ink-3">
            <tr><th className="px-4 py-2">Case</th><th className="px-4 py-2">Patient</th><th className="px-4 py-2">Payer</th>
              <th className="px-4 py-2">Stage</th>{showClock && <th className="px-4 py-2">Clock</th>}
              <th className="px-4 py-2 text-right">Approved</th><th className="px-4 py-2 text-right">Billed</th></tr>
          </thead>
          <tbody>
            {sorted === null && <tr><td colSpan={7} className="px-4 py-8 text-center text-ink-3">Loading…</td></tr>}
            {sorted?.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-ink-3">{empty ?? 'Nothing here right now.'}</td></tr>}
            {sorted?.map((c) => (
              <tr key={c.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                <td className="px-4 py-2"><Link className="num text-accent hover:underline" to={`/case/${c.id}`}>{c.case_number}</Link></td>
                <td className="px-4 py-2">{c.patients?.full_name}</td>
                <td className="px-4 py-2 text-ink-2">{c.payers?.name}</td>
                <td className="px-4 py-2"><StageBadge stage={c.stage} /></td>
                {showClock && <td className="px-4 py-2">{clocks[c.id] ? <ClockText due={clocks[c.id]} stopped={null} /> : <span className="text-ink-3">—</span>}</td>}
                <td className="num px-4 py-2 text-right">{rupees(c.approved_amount)}</td>
                <td className="num px-4 py-2 text-right">{rupees(c.billed_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
