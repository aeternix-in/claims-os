import { useEffect, useState } from 'react';
import { engine, rupees } from '../lib/api';

interface Line { category: string; description: string; qty: number | string; rate: number | string; mou_rate: number | string | null; to_payer: boolean; snomed_code?: string | null }
export interface Audit {
  gross: number; family_direct: number; nonpayable_total: number; above_mou: number; discount: number; claimed: number;
  package_rate: number | null; room_cap: number | null; room_rule_label: string; room_rate: number; ratio: number;
  pd_applies: boolean; pd_note: string; pd: number; pd_room_excess: number; pd_by_category: Record<string, number>;
  admissible: number; copay: number; sub_limit_excess: number; si_excess: number; expected_payable: number;
  authorised: number; unauthorised_excess: number;
  family: { nonpayable: number; room_proportion: number; copay: number; total: number };
  credit?: { settled: number; tds: number; expected_credit: number; tds_note: string };
  room_advice: { cap: number | null; advice: string; best: string | null };
}
interface BillResp { lines: Line[]; audit: Audit; editable: boolean; locked?: boolean; categories: { id: string; label: string }[] }

const blank = (): Line => ({ category: 'misc', description: '', qty: 1, rate: 0, mou_rate: null, to_payer: true });

function Row({ k, v, minus, strong, hint }: { k: string; v: number; minus?: boolean; strong?: boolean; hint?: string }) {
  if (minus && !v) return null;
  return (
    <div className={`flex justify-between gap-3 py-1.5 ${strong ? 'border-t border-line font-medium' : ''}`}>
      <span>{k}{hint && <span className="block text-[12px] text-ink-3">{hint}</span>}</span>
      <span className={`num shrink-0 ${minus ? 'text-crit' : ''}`}>{minus ? '−' : ''}{rupees(v)}</span>
    </div>
  );
}

/** Itemised bill + what the payer will pay, computed by the engine. */
export default function BillPanel({ caseId, onChange }: { caseId: string; onChange?: () => void }) {
  const [d, setD] = useState<BillResp | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => engine<BillResp>(`/cases/${caseId}/bill`).then((r) => { setD(r); setLines(r.lines); setDirty(false); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [caseId]);

  const set = (i: number, patch: Partial<Line>) => { setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l))); setDirty(true); };

  async function save() {
    setBusy(true); setErr('');
    try {
      const body = { lines: lines.filter((l) => l.description.trim()).map((l) => ({
        ...l, qty: Number(l.qty), rate: Number(l.rate), mou_rate: l.mou_rate === '' || l.mou_rate == null ? null : Number(l.mou_rate), snomed_code: l.snomed_code || null })) };
      const r = await engine<{ lines: Line[]; audit: Audit }>(`/cases/${caseId}/bill`, { method: 'PUT', body: JSON.stringify(body) });
      setD((prev) => prev && { ...prev, lines: r.lines, audit: r.audit }); setLines(r.lines); setDirty(false); onChange?.();
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }

  if (!d) return <p className="text-ink-3">{err || 'Loading bill…'}</p>;
  const a = d.audit;
  const ed = d.editable;

  return (
    <div className="space-y-4">
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[680px] text-left">
          <thead className="border-b border-line text-[12px] uppercase tracking-wide text-ink-3">
            <tr><th className="px-3 py-2">Head</th><th className="px-3 py-2">Description</th><th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Rate</th><th className="px-3 py-2 text-right">MoU rate</th>
              <th className="px-3 py-2" title="SNOMED CT code, needed to send over NHCX">SNOMED</th>
              <th className="px-3 py-2 text-center">To payer</th><th className="px-3 py-2 text-right">Amount</th>{ed && <th />}</tr>
          </thead>
          <tbody>
            {lines.length === 0 && <tr><td colSpan={9} className="px-3 py-6 text-center text-ink-3">No bill lines yet.</td></tr>}
            {lines.map((l, i) => (
              <tr key={i} className="border-b border-line last:border-0">
                <td className="px-3 py-1.5">{ed ? (
                  <select className="input py-1" value={l.category} onChange={(e) => set(i, { category: e.target.value })}>
                    {d.categories.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                  </select>) : d.categories.find((c) => c.id === l.category)?.label}</td>
                <td className="px-3 py-1.5">{ed ? <input className="input py-1" value={l.description} onChange={(e) => set(i, { description: e.target.value })} /> : l.description}</td>
                {(['qty', 'rate', 'mou_rate'] as const).map((k) => (
                  <td key={k} className="num px-3 py-1.5 text-right">{ed
                    ? <input className="input w-24 py-1 text-right" type="number" min={0} value={l[k] ?? ''} onChange={(e) => set(i, { [k]: e.target.value })} />
                    : l[k] == null ? '—' : Number(l[k]).toLocaleString('en-IN')}</td>
                ))}
                <td className="px-3 py-1.5">{ed ? <input className="input num w-28 py-1" placeholder="optional" value={l.snomed_code ?? ''} onChange={(e) => set(i, { snomed_code: e.target.value })} aria-label="SNOMED code" />
                  : <span className="num text-ink-3">{l.snomed_code ?? '—'}</span>}</td>
                <td className="px-3 py-1.5 text-center"><input type="checkbox" disabled={!ed} checked={l.to_payer} onChange={(e) => set(i, { to_payer: e.target.checked })} className="accent-[var(--accent)]" /></td>
                <td className="num px-3 py-1.5 text-right">{rupees(Math.round(Number(l.qty) * Number(l.rate)))}</td>
                {ed && <td className="px-2"><button className="text-ink-3 hover:text-crit" aria-label="Remove line" onClick={() => { setLines(lines.filter((_, j) => j !== i)); setDirty(true); }}>×</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
        {!ed && (
          <p className="border-t border-line px-3 py-2 text-[13px] text-ink-3">
            {d.locked ? 'The claim file has been submitted, so the bill is locked.' : 'Only the insurance desk or accounts can edit the bill.'}
          </p>
        )}
        {ed && (
          <div className="flex gap-2 border-t border-line p-3">
            <button className="btn-ghost" onClick={() => { setLines([...lines, blank()]); setDirty(true); }}>Add a line</button>
            <button className="btn-primary" disabled={!dirty || busy} onClick={save}>{busy ? 'Checking…' : 'Save and check'}</button>
          </div>
        )}
      </div>
      {err && <p className="rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}

      <div className="grid gap-4 md:grid-cols-2">
        <section className="card p-4">
          <h3 className="mb-2 font-medium">What the payer will pay</h3>
          <Row k="Gross bill" v={a.gross} />
          <Row k="Paid by family directly" v={a.family_direct} minus />
          <Row k={a.package_rate != null ? 'Above the package rate' : 'Agreed discount'} v={a.discount} minus />
          <Row k="Non-medical items" v={a.nonpayable_total - a.family_direct > 0 ? a.nonpayable_total : 0} minus hint="IRDAI non-payable list" />
          <Row k="Charged above MoU rate" v={a.above_mou} minus />
          <Row k="Room-rent proportionate cut" v={a.pd} minus hint={a.pd_applies ? a.pd_note : undefined} />
          <Row k="Admissible" v={a.admissible} strong />
          <Row k="Co-pay" v={a.copay} minus />
          <Row k="Above sub-limit" v={a.sub_limit_excess} minus />
          <Row k="Above sum insured left" v={a.si_excess} minus />
          <Row k="Expected from payer" v={a.expected_payable} strong />
          {a.unauthorised_excess > 0 && (
            <p className="mt-2 rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
              {rupees(a.unauthorised_excess)} above what is approved so far. Raise an enhancement before discharge.
            </p>
          )}
          {a.credit && <>
            <Row k="Settled" v={a.credit.settled} strong />
            <Row k="TDS under section 194J" v={a.credit.tds} minus hint={a.credit.tds_note} />
            <Row k="Expected bank credit" v={a.credit.expected_credit} strong />
          </>}
        </section>
        <section className="card p-4">
          <h3 className="mb-2 font-medium">The family's likely share</h3>
          <Row k="Non-medical items" v={a.family.nonpayable} />
          <Row k="Room-rent proportionate cut" v={a.family.room_proportion} />
          <Row k="Co-pay, sub-limit, sum insured" v={a.family.copay} />
          <Row k="Total" v={a.family.total} strong />
          <div className="mt-3 rounded-md bg-surface-2 px-3 py-2 text-[13px] text-ink-2">
            <div className="font-medium text-ink">Room</div>
            <div>{a.room_rule_label}. {!a.pd_applies && a.pd_note}</div>
            <div className="mt-1">{a.room_advice.advice}</div>
          </div>
        </section>
      </div>
    </div>
  );
}
