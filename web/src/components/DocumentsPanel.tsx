import { useEffect, useRef, useState } from 'react';
import { engine, engineUpload, openDocument } from '../lib/api';

interface Doc { id: string; filename: string; doc_type: string; pages: number; read_method: string; type_confirmed: boolean; created_at: string }
interface Fld { id: string; document_id: string; name: string; value: string; original_value: string; page: number; snippet: string; confirmed: boolean }
interface Resp { documents: Doc[]; fields: Fld[]; types: Record<string, string>; key_fields: string[] }

const LABEL: Record<string, string> = {
  patient_name: 'Patient name', uhid: 'UHID', admission_date: 'Admission date', discharge_date: 'Discharge date',
  diagnosis: 'Diagnosis', icd10: 'ICD-10', policy_number: 'Policy number', total_amount: 'Bill total', treating_doctor: 'Treating doctor',
};
const METHOD: Record<string, string> = { 'pdf-text': 'PDF text', 'ocr-tesseract': 'OCR', 'pdf-text+ocr': 'PDF + OCR', text: 'Text' };

function FieldRow({ f, isKey, onDone }: { f: Fld; isKey: boolean; onDone: () => void }) {
  const [v, setV] = useState(f.value);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  async function confirm() {
    setBusy(true); setErr('');
    try { await engine(`/fields/${f.id}/confirm`, { method: 'POST', body: JSON.stringify(v !== f.value ? { value: v } : {}) }); onDone(); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }
  return (
    <tr className="border-b border-line last:border-0 align-top">
      <td className="px-3 py-2 text-ink-2">{LABEL[f.name] ?? f.name}{isKey && <span className="ml-1 text-[11px] text-warn">key</span>}</td>
      <td className="px-3 py-2">
        <input className="input py-1" value={v} onChange={(e) => setV(e.target.value)} aria-label={LABEL[f.name]} />
        <div className="mt-1 truncate text-[12px] text-ink-3" title={f.snippet}>p.{f.page}: “…{f.snippet}…”</div>
        {f.confirmed && f.value !== f.original_value && <div className="text-[12px] text-ink-3">Read as “{f.original_value}”, corrected</div>}
        {err && <div className="text-[12px] text-crit">{err}</div>}
      </td>
      <td className="px-3 py-2 text-right">
        {f.confirmed && v === f.value
          ? <span className="text-good">Confirmed</span>
          : <button className="btn-ghost py-1" disabled={busy} onClick={confirm}>{v !== f.value ? 'Correct' : 'Confirm'}</button>}
      </td>
    </tr>
  );
}

/** Upload originals, see how each was read and sorted, confirm the fields pulled out. */
export default function DocumentsPanel({ caseId, onChange }: { caseId: string; onChange?: () => void }) {
  const [d, setD] = useState<Resp | null>(null);
  const [msgs, setMsgs] = useState<{ name: string; text: string; bad?: boolean }[]>([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = () => engine<Resp>(`/cases/${caseId}/documents`).then(setD).catch((e) => setMsgs([{ name: '', text: e.message, bad: true }]));
  useEffect(() => { load(); }, [caseId]);
  const refresh = () => { load(); onChange?.(); };

  async function upload(files: FileList | File[]) {
    setBusy(true); const out: typeof msgs = [];
    for (const f of Array.from(files)) {
      const form = new FormData(); form.append('file', f);
      try {
        const r = await engineUpload<{ document: Doc; fields: unknown[] }>(`/cases/${caseId}/documents`, form);
        out.push({ name: f.name, text: `Read as ${d?.types[r.document.doc_type] ?? r.document.doc_type}, ${r.fields.length} field(s) found` });
      } catch (e) { out.push({ name: f.name, text: (e as Error).message, bad: true }); }
    }
    setMsgs(out); setBusy(false); refresh();
  }

  async function retype(id: string, doc_type: string) {
    await engine(`/documents/${id}`, { method: 'PATCH', body: JSON.stringify({ doc_type }) }); refresh();
  }

  if (!d) return <p className="text-ink-3">Loading documents…</p>;
  const pending = d.fields.filter((f) => d.key_fields.includes(f.name) && !f.confirmed).length;

  return (
    <div className="space-y-4">
      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
        className={`card flex flex-col items-center gap-2 border-dashed p-6 text-center ${drag ? 'border-accent bg-accent-soft' : ''}`}>
        <p className="font-medium">Drop the claim pages here</p>
        <p className="text-ink-3">PDFs or phone photos. Each file is hashed, kept unchanged, read and sorted by type.</p>
        <input ref={input} type="file" multiple accept="application/pdf,image/png,image/jpeg,image/tiff" className="hidden"
          onChange={(e) => e.target.files && upload(e.target.files)} />
        <button className="btn-primary" disabled={busy} onClick={() => input.current?.click()}>{busy ? 'Reading…' : 'Choose files'}</button>
        {msgs.map((m, i) => <p key={i} className={`text-[13px] ${m.bad ? 'text-crit' : 'text-good'}`}>{m.name && <b>{m.name}: </b>}{m.text}</p>)}
      </div>

      {d.documents.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead className="border-b border-line text-[12px] uppercase tracking-wide text-ink-3">
              <tr><th className="px-3 py-2">File</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Read by</th><th className="px-3 py-2 text-right">Pages</th></tr>
            </thead>
            <tbody>
              {d.documents.map((doc) => (
                <tr key={doc.id} className="border-b border-line last:border-0">
                  <td className="px-3 py-2"><button className="text-accent hover:underline" onClick={() => openDocument(doc.id)}>{doc.filename}</button></td>
                  <td className="px-3 py-2">
                    <select className="input py-1" value={doc.doc_type} onChange={(e) => retype(doc.id, e.target.value)} aria-label={`Type of ${doc.filename}`}>
                      {Object.entries(d.types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-2 text-ink-3">{METHOD[doc.read_method] ?? doc.read_method}</td>
                  <td className="num px-3 py-2 text-right">{doc.pages}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d.fields.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <h3 className="font-medium">Fields read from the documents</h3>
            {pending > 0 ? <span className="text-[13px] text-warn">{pending} key field(s) to confirm</span> : <span className="text-[13px] text-good">Key fields confirmed</span>}
          </div>
          <table className="w-full text-left"><tbody>
            {d.fields.map((f) => <FieldRow key={f.id + f.value + f.confirmed} f={f} isKey={d.key_fields.includes(f.name)} onDone={refresh} />)}
          </tbody></table>
        </div>
      )}
    </div>
  );
}
