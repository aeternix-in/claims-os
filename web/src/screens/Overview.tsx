import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { engine, rupees, supabase } from '../lib/api';
import { STAGE_LABEL, type Stage } from '../lib/types';
import { ClockText } from '../components/ClockText';

interface OverviewData {
  in_house: number;
  needs_action: { preauth_pending: number; queries: number; discharge_pending: number; to_submit: number };
  buckets: { id: string; label: string; amount: number; count: number }[];
  open_clocks: { id: string; kind: string; due_at: string; case_id: string; case_number: string; stage: Stage; patient: string }[];
  clock_record: { kind: string; total: number; breached: number }[];
}

function Tile({ label, value, to, tone }: { label: string; value: number | string; to: string; tone?: 'warn' }) {
  return (
    <Link to={to} className="card block p-4 hover:border-accent-line">
      <div className="label">{label}</div>
      <div className={`num text-[26px] leading-8 ${tone === 'warn' && value ? 'text-warn' : ''}`}>{value}</div>
    </Link>
  );
}

export default function Overview() {
  const [d, setD] = useState<OverviewData | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    const load = () => engine<OverviewData>('/overview').then(setD).catch((e) => setErr(e.message));
    load();
    const ch = supabase.channel('overview')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'cases' }, load).subscribe();
    return () => { supabase.removeChannel(ch); };
  }, []);

  if (!d) return <p className="text-ink-3">{err || 'Loading…'}</p>;
  const maxBucket = Math.max(1, ...d.buckets.map((b) => b.amount));

  return (
    <div className="space-y-6">
      <div><h1 className="font-serif text-[28px] leading-tight">Overview</h1><p className="text-ink-3">Money, clocks and what needs action</p></div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Tile label="Patients in house" value={d.in_house} to="/desk" />
        <Tile label="Pre-auth with payer" value={d.needs_action.preauth_pending} to="/preauth" />
        <Tile label="Open queries" value={d.needs_action.queries} to="/queries" tone="warn" />
        <Tile label="Final approval pending" value={d.needs_action.discharge_pending} to="/discharge" />
        <Tile label="Claims to submit" value={d.needs_action.to_submit} to="/cash" tone="warn" />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <section className="card p-5">
          <h2 className="mb-1 font-medium">Where the money is</h2>
          <p className="mb-4 text-ink-3">Approved amounts by where each claim stands</p>
          <div className="space-y-3">
            {d.buckets.map((b) => (
              <div key={b.id}>
                <div className="flex justify-between gap-3">
                  <span><span className="num mr-2 text-ink-3">{b.id}</span>{b.label}</span>
                  <span className="num">{rupees(b.amount)} <span className="text-ink-3">· {b.count}</span></span>
                </div>
                <div className="mt-1 h-2 rounded bg-surface-2">
                  <div className="h-2 rounded" style={{ width: `${(b.amount / maxBucket) * 100}%`,
                    background: b.id === 'D' ? 'var(--viz-lost)' : b.id === 'F' ? 'var(--viz-settled)' : 'var(--viz-outstanding)' }} />
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="card p-5">
          <h2 className="mb-1 font-medium">IRDAI clocks running</h2>
          <p className="mb-3 text-ink-3">1 hour for pre-auth, 3 hours for final approval</p>
          {d.open_clocks.length === 0 && <p className="text-ink-3">No clocks running.</p>}
          <ul className="divide-y divide-line">
            {d.open_clocks.map((k) => (
              <li key={k.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <Link to={`/case/${k.case_id}`} className="num text-accent hover:underline">{k.case_number}</Link>
                  <div className="truncate text-[12px] text-ink-3">{k.patient} · {STAGE_LABEL[k.stage]}</div>
                </div>
                <ClockText due={k.due_at} stopped={null} />
              </li>
            ))}
          </ul>
          {d.clock_record.length > 0 && (
            <div className="mt-4 border-t border-line pt-3 text-[13px] text-ink-2">
              {d.clock_record.map((r) => (
                <div key={r.kind} className="flex justify-between"><span className="capitalize">{r.kind} answered late</span>
                  <span className="num">{r.breached} of {r.total}</span></div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
