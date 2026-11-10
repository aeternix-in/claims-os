import { useEffect, useState } from 'react';
import { engine } from '../lib/api';
import { useSession } from '../lib/session';

export interface Query {
  id: string; case_id: string; stage: string; query_text: string; code: string; code_name: string; status: 'open' | 'drafted' | 'signed' | 'sent';
  draft: string | null; attachments: string[]; missing: string[]; requires_signoff: boolean; signed_name: string | null;
  received_at: string; sent_at: string | null; placeholders: string[]; can_send: boolean;
  case_number?: string; patient?: string; payer?: string;
}
export const CODES: Record<string, string> = {
  Q1: 'Missing document', Q2: 'Clinical justification', Q3: 'Past history / PED', Q4: 'Billing clarification',
  Q5: 'Identity or policy', Q6: 'Accident / medico-legal', Q7: 'Discrepancy', Q8: 'Investigation',
};
const STATUS: Record<Query['status'], [string, string]> = {
  open: ['To draft', 'bg-warn-soft text-warn'], drafted: ['Drafted', 'bg-accent-soft text-accent'],
  signed: ['Signed by doctor', 'bg-accent-soft text-accent'], sent: ['Answered', 'bg-good-soft text-good'],
};
export const QueryStatus = ({ s }: { s: Query['status'] }) => <span className={`whitespace-nowrap rounded px-2 py-0.5 text-[12px] font-medium ${STATUS[s][1]}`}>{STATUS[s][0]}</span>;

/** Draft from the record → doctor signs (clinical) → mark as sent. Any edit clears a signature. */
export default function QueryEditor({ q, onChange }: { q: Query; onChange: (q?: Query) => void }) {
  const { profile } = useSession();
  const [draft, setDraft] = useState(q.draft ?? '');
  const [docs, setDocs] = useState<{ id: string; filename: string }[]>([]);
  const [att, setAtt] = useState<string[]>(q.attachments);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  useEffect(() => { setDraft(q.draft ?? ''); setAtt(q.attachments); }, [q.id, q.draft, q.status]);
  useEffect(() => { engine<{ documents: { id: string; filename: string }[] }>(`/cases/${q.case_id}/documents`).then((r) => setDocs(r.documents)).catch(() => {}); }, [q.case_id]);

  const dirty = draft !== (q.draft ?? '') || JSON.stringify(att) !== JSON.stringify(q.attachments);
  const sent = q.status === 'sent';
  const isDoctor = profile?.role === 'doctor' || profile?.role === 'admin';
  const left = (draft.match(/\[[^\]\n]{3,}\]/g) ?? []);

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label); setErr('');
    try { const r = await fn(); onChange(r && typeof r === 'object' && 'id' in r ? (r as Query) : undefined); }
    catch (e) { setErr((e as Error).message); }
    setBusy('');
  }
  const save = () => engine<Query>(`/queries/${q.id}`, { method: 'PUT', body: JSON.stringify({ draft, attachments: att }) });

  return (
    <div className="space-y-3">
      <div className="rounded-md bg-surface-2 p-3">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <select className="input w-auto py-0.5 text-[13px]" disabled={sent} value={q.code} aria-label="Query type"
            onChange={(e) => act('code', () => engine(`/queries/${q.id}`, { method: 'PUT', body: JSON.stringify({ code: e.target.value }) }))}>
            {Object.entries(CODES).map(([k, v]) => <option key={k} value={k}>{k} · {v}</option>)}
          </select>
          <QueryStatus s={q.status} />
          {q.requires_signoff && <span className="text-[12px] text-warn">Needs doctor sign-off</span>}
        </div>
        <p className="text-ink">“{q.query_text}”</p>
      </div>

      {!q.draft && !sent ? (
        <button className="btn-primary" disabled={!!busy} onClick={() => act('draft', () => engine(`/queries/${q.id}/draft`, { method: 'POST' }))}>
          {busy === 'draft' ? 'Drafting…' : 'Draft a reply from the record'}</button>
      ) : (
        <>
          <textarea className="input num h-72 text-[13px] leading-5" readOnly={sent} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Reply" />
          {left.length > 0 && !sent && (
            <div className="rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
              {left.length} bracketed item(s) to write before this can be signed or sent:
              <ul className="ml-4 list-disc">{left.map((p, i) => <li key={i}>{p.slice(1, 90)}{p.length > 90 ? '…' : ''}</li>)}</ul>
            </div>
          )}
          <fieldset>
            <legend className="label">Attach</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {docs.map((d) => (
                <label key={d.id} className="flex items-center gap-1.5 text-[13px]">
                  <input type="checkbox" disabled={sent} className="accent-[var(--accent)]" checked={att.includes(d.id)}
                    onChange={(e) => setAtt(e.target.checked ? [...att, d.id] : att.filter((x) => x !== d.id))} />{d.filename}
                </label>
              ))}
              {docs.length === 0 && <span className="text-ink-3">No documents on this case yet.</span>}
            </div>
          </fieldset>
          {q.signed_name && q.status === 'signed' && <p className="text-[13px] text-good">Signed by {q.signed_name}</p>}
          {!sent && (
            <div className="flex flex-wrap gap-2">
              <button className="btn-ghost" disabled={!dirty || !!busy} onClick={() => act('save', save)}>Save{q.status === 'signed' ? ' (clears signature)' : ''}</button>
              <button className="btn-ghost" disabled={!!busy} onClick={() => act('draft', () => engine(`/queries/${q.id}/draft`, { method: 'POST' }))}>Redraft</button>
              {q.requires_signoff && isDoctor && q.status !== 'signed' && (
                <button className="btn-primary" disabled={!!busy || left.length > 0}
                  onClick={() => act('sign', async () => { if (dirty) await save(); return engine(`/queries/${q.id}/signoff`, { method: 'POST' }); })}>Approve and sign</button>
              )}
              <button className="btn-primary" disabled={!!busy || dirty || !q.can_send}
                title={q.can_send ? '' : q.requires_signoff && q.status !== 'signed' ? 'A doctor must sign first' : 'Fill in the bracketed items'}
                onClick={() => act('send', () => engine(`/queries/${q.id}/send`, { method: 'POST' }))}>Mark as sent to payer</button>
            </div>
          )}
          {sent && <p className="text-[13px] text-good">Answered {q.sent_at && new Date(q.sent_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</p>}
        </>
      )}
      {err && <p className="rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}
    </div>
  );
}
