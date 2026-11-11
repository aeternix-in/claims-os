import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { engine, rupees } from '../lib/api';

interface Code { code: string; name: string; stage: string; owner: string; avoidable: string; family: boolean; contestable: boolean; amount: number; claims: number }
interface Row { id: string; case_number: string; patient: string; payer: string; stage: string; billed: number; leak: { codes: Record<string, number>; hospital: number; family: number; contestable: number; notes: string[] } }
interface Data { by_code: Code[]; where_it_went: Record<string, number>; hospital_leakage: number; family_share: number; contestable: number; claims: Row[] }

const WENT: [string, string, string][] = [
  ['received', 'Received in the bank', 'var(--viz-settled)'], ['outstanding', 'Still with payers', 'var(--viz-outstanding)'],
  ['family', 'Family share', 'var(--viz-family)'], ['leakage', 'Hospital leakage', 'var(--viz-lost)'],
  ['tds', 'TDS (tax credit)', 'var(--viz-age-1)'], ['discount', 'Agreed discount', 'var(--viz-discount)'],
];

/** Where each billed rupee went, and what was lost by cause (L1–L7). */
export default function Leakage() {
  const [d, setD] = useState<Data | null>(null);
  const [frm, setFrm] = useState('');
  const [to, setTo] = useState('');
  const [code, setCode] = useState<string | null>(null);
  const [msg, setMsg] = useState('');

  const load = () => {
    const q = new URLSearchParams(); if (frm) q.set('frm', frm); if (to) q.set('to', to);
    engine<Data>(`/reports/leakage?${q}`).then(setD).catch((e) => setMsg(e.message));
  };
  useEffect(() => { load(); }, [frm, to]);

  async function disputeAll() {
    setMsg('');
    try { const r = await engine<{ drafted: unknown[] }>('/letters/sweep', { method: 'POST' }); setMsg(`${r.drafted.length} letter(s) drafted. Review them on Claim to cash.`); }
    catch (e) { setMsg((e as Error).message); }
  }

  if (!d) return <p className="text-ink-3">{msg || 'Loading…'}</p>;
  const g = d.where_it_went.gross || 0;
  const max = Math.max(1, ...d.by_code.map((c) => c.amount));
  const rows = code ? d.claims.filter((r) => r.leak.codes[code] > 0) : d.claims;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="font-serif text-[28px] leading-tight">Leakage</h1><p className="text-ink-3">Where the money was lost, by cause</p></div>
        <div className="flex flex-wrap items-end gap-2">
          <div><label className="label" htmlFor="frm">Admitted from</label><input id="frm" type="date" className="input py-1" value={frm} onChange={(e) => setFrm(e.target.value)} /></div>
          <div><label className="label" htmlFor="to">to</label><input id="to" type="date" className="input py-1" value={to} onChange={(e) => setTo(e.target.value)} /></div>
        </div>
      </div>
      {msg && <p className="rounded-md bg-accent-soft px-3 py-2 text-accent">{msg}</p>}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="card p-4"><div className="label">Hospital leakage</div><div className="num text-[26px] text-crit">{rupees(d.hospital_leakage)}</div><div className="text-[12px] text-ink-3">Lost by the hospital</div></div>
        <div className="card p-4"><div className="label">Contestable now</div><div className="num text-[26px]">{rupees(d.contestable)}</div>
          <button className="mt-1 text-[12px] text-accent hover:underline" onClick={disputeAll}>Dispute all: draft the letters</button></div>
        <div className="card p-4"><div className="label">Family share</div><div className="num text-[26px]">{rupees(d.family_share)}</div><div className="text-[12px] text-ink-3">Collectable from families, not lost</div></div>
      </div>

      {g > 0 && (
        <section className="card p-5">
          <h2 className="mb-1 font-medium">Where each rupee billed went</h2>
          <p className="mb-3 text-ink-3">{rupees(g)} billed on claims with an itemised bill</p>
          <div className="flex h-6 overflow-hidden rounded" role="img" aria-label="Split of the billed amount">
            {WENT.map(([k, , col]) => d.where_it_went[k] > 0 && <div key={k} style={{ width: `${(d.where_it_went[k] / g) * 100}%`, background: col }} title={`${k}: ${rupees(d.where_it_went[k])}`} />)}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
            {WENT.map(([k, label, col]) => (
              <div key={k} className="flex items-center justify-between gap-2 text-[13px]">
                <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: col }} />{label}</span>
                <span className="num">{rupees(d.where_it_went[k])} <span className="text-ink-3">{Math.round((d.where_it_went[k] / g) * 100)}%</span></span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="card overflow-x-auto">
        <table className="w-full min-w-[720px] text-left">
          <thead className="border-b border-line text-[12px] uppercase tracking-wide text-ink-3">
            <tr><th className="px-4 py-2">Cause</th><th className="px-4 py-2">Stage · owner</th><th className="px-4 py-2">Avoidable</th><th className="px-4 py-2 w-1/4" /><th className="px-4 py-2 text-right">Amount</th><th className="px-4 py-2 text-right">Claims</th></tr>
          </thead>
          <tbody>
            {d.by_code.map((c) => (
              <tr key={c.code} onClick={() => setCode(code === c.code ? null : c.code)}
                className={`cursor-pointer border-b border-line last:border-0 ${code === c.code ? 'bg-accent-soft' : 'hover:bg-surface-2'} ${c.amount ? '' : 'text-ink-3'}`}>
                <td className="px-4 py-2"><span className="num mr-2 text-ink-3">{c.code}</span>{c.name}{c.family && <span className="ml-2 text-[11px] text-ink-3">family</span>}</td>
                <td className="px-4 py-2 text-[13px] text-ink-2">{c.stage} · {c.owner}</td>
                <td className="px-4 py-2 text-[13px] text-ink-2">{c.avoidable}</td>
                <td className="px-4 py-2"><div className="h-2 rounded bg-surface-2"><div className="h-2 rounded" style={{ width: `${(c.amount / max) * 100}%`, background: c.family ? 'var(--viz-family)' : 'var(--viz-lost)' }} /></div></td>
                <td className="num px-4 py-2 text-right">{rupees(c.amount)}</td>
                <td className="num px-4 py-2 text-right">{c.claims}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2 className="border-b border-line px-4 py-3 font-medium">{code ? `Claims with ${code}` : 'Claims with any leakage'} ({rows.length})</h2>
        <ul className="divide-y divide-line">
          {rows.slice(0, 100).map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-[13px]">
              <span><Link to={`/case/${r.id}`} className="num text-accent hover:underline">{r.case_number}</Link> <span className="text-ink-2">{r.payer}</span>
                {r.leak.notes.length > 0 && <span className="text-ink-3"> · {r.leak.notes.join('; ')}</span>}</span>
              <span className="num">{Object.entries(r.leak.codes).filter(([, v]) => v > 0).map(([k, v]) => `${k} ${rupees(v)}`).join(' · ')}</span>
            </li>
          ))}
          {rows.length === 0 && <li className="px-4 py-6 text-center text-ink-3">No leakage in this period.</li>}
        </ul>
      </section>
    </div>
  );
}
