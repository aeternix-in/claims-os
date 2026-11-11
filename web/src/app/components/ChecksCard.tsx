import { useEffect, useState } from 'react';
import { engine } from '../lib/api';
import { useSession } from '../lib/session';

export interface CheckResult {
  rule_id: string; level: 'red' | 'amber' | 'green'; title: string; detail: string; source: string; fix: string;
  clinical: boolean; cleared: boolean; clearance: { note: string; cleared_name: string; cleared_role: string } | null;
}
export interface Summary { stage: string; status: 'red' | 'amber' | 'green'; red: number; amber_open: number; amber_cleared: number; green: number; blocked: boolean; results: CheckResult[] }

const STAGE: Record<string, string> = { preauth: 'pre-auth', discharge: 'final approval', claim: 'claim file' };
const DOT = { red: 'bg-crit', amber: 'bg-[var(--ring-warn)]', green: 'bg-good' };

function Item({ r, stage, caseId, onDone }: { r: CheckResult; stage: string; caseId: string; onDone: (s: Summary) => void }) {
  const { profile } = useSession();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const canClear = r.level === 'amber' && !r.cleared && (!r.clinical || profile?.role === 'doctor' || profile?.role === 'admin');
  async function clear() {
    setErr('');
    try { onDone(await engine<Summary>(`/cases/${caseId}/checks/${encodeURIComponent(r.rule_id)}/clear`, { method: 'POST', body: JSON.stringify({ stage, note }) })); setOpen(false); }
    catch (e) { setErr((e as Error).message); }
  }
  return (
    <li className="py-2">
      <div className="flex gap-2">
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${r.cleared ? 'bg-line-strong' : DOT[r.level]}`} />
        <div className="min-w-0 flex-1">
          <div className={r.cleared ? 'text-ink-3 line-through' : ''}>{r.title}</div>
          {r.detail && <div className="text-[12px] text-ink-3">{r.detail}</div>}
          {r.clinical && !r.cleared && r.level === 'amber' && <div className="text-[12px] text-warn">Clinical: the treating doctor clears this</div>}
          {r.cleared && r.clearance && <div className="text-[12px] text-ink-3">Cleared by {r.clearance.cleared_name} ({r.clearance.cleared_role}): {r.clearance.note}</div>}
          {!r.cleared && r.fix && <div className="text-[12px] text-ink-2">Fix: {r.fix}</div>}
          <div className="text-[11px] text-ink-3" title={r.source}>Source: {r.source.length > 70 ? r.source.slice(0, 70) + '…' : r.source}</div>
          {canClear && !open && <button className="mt-1 text-[12px] text-accent hover:underline" onClick={() => setOpen(true)}>Clear with a note</button>}
          {open && (
            <div className="mt-2 space-y-2">
              <textarea className="input h-16 text-[13px]" placeholder="Why this is acceptable (named, kept on record)" value={note} onChange={(e) => setNote(e.target.value)} />
              <div className="flex gap-2"><button className="btn-primary py-1" disabled={note.trim().length < 5} onClick={clear}>Clear</button>
                <button className="btn-ghost py-1" onClick={() => setOpen(false)}>Cancel</button></div>
              {err && <div className="text-[12px] text-crit">{err}</div>}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/** Pre-flight check for the next submission: red blocks, amber needs a named note. */
export default function ChecksCard({ caseId, refreshKey, initial }: { caseId: string; refreshKey: number; initial?: Summary | null }) {
  const [s, setS] = useState<Summary | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [showGreen, setShowGreen] = useState(false);

  async function run() {
    setBusy(true); setErr('');
    try { setS(await engine<Summary>(`/cases/${caseId}/checks`, { method: 'POST' })); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }
  useEffect(() => { run(); }, [caseId, refreshKey]);
  useEffect(() => { if (initial) setS(initial); }, [initial]);

  const tone = s?.status === 'red' ? 'bg-crit-soft text-crit' : s?.status === 'amber' ? 'bg-warn-soft text-warn' : 'bg-good-soft text-good';
  const items = s?.results.filter((r) => showGreen || r.level !== 'green') ?? [];

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between"><div className="label">Pre-flight check</div>
        <button className="text-[12px] text-accent hover:underline" disabled={busy} onClick={run}>{busy ? 'Checking…' : 'Run again'}</button></div>
      {err && <p className="text-crit">{err}</p>}
      {s && <>
        <div className={`mt-1 rounded-md px-3 py-2 ${tone}`}>
          {s.status === 'green' ? `Ready to submit the ${STAGE[s.stage]}`
            : `${s.red ? `${s.red} to fix` : ''}${s.red && s.amber_open ? ', ' : ''}${s.amber_open ? `${s.amber_open} to review` : ''} before the ${STAGE[s.stage]} can go`}
        </div>
        <ul className="mt-2 divide-y divide-line">{items.map((r) => <Item key={r.rule_id} r={r} stage={s.stage} caseId={caseId} onDone={setS} />)}</ul>
        {s.green > 0 && <button className="mt-1 text-[12px] text-ink-3 hover:text-ink" onClick={() => setShowGreen(!showGreen)}>
          {showGreen ? 'Hide' : 'Show'} {s.green} passed</button>}
      </>}
    </div>
  );
}
