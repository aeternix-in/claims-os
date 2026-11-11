import { createClient } from '@supabase/supabase-js';

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
);

const ENGINE = import.meta.env.VITE_ENGINE_URL as string;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

/** Calls the Python engine with the user's Supabase token. Use for every write. */
export async function engine<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const res = await fetch(`${ENGINE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${data.session?.access_token ?? ''}`,
      ...init.headers,
    },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail;
    throw new ApiError(res.status, d?.code ?? 'error', d?.message ?? (typeof d === 'string' ? d : res.statusText));
  }
  return body as T;
}

export const rupees = (n: number | null | undefined) =>
  n == null ? '—' : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });

/** Multipart upload to the engine (documents). */
export async function engineUpload<T = unknown>(path: string, form: FormData): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const res = await fetch(`${ENGINE}${path}`, {
    method: 'POST', body: form, headers: { Authorization: `Bearer ${data.session?.access_token ?? ''}` },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail;
    throw new ApiError(res.status, d?.code ?? 'error', d?.message ?? (typeof d === 'string' ? d : res.statusText));
  }
  return body as T;
}

/** Opens an original document: signed URL from Supabase, or a streamed file in local dev. */
export async function openDocument(id: string) {
  const { data } = await supabase.auth.getSession();
  const res = await fetch(`${ENGINE}/documents/${id}/file`, { headers: { Authorization: `Bearer ${data.session?.access_token ?? ''}` } });
  if (!res.ok) throw new ApiError(res.status, 'error', 'Could not open the document');
  if ((res.headers.get('content-type') ?? '').includes('application/json')) {
    window.open((await res.json()).url, '_blank', 'noopener');
  } else {
    window.open(URL.createObjectURL(await res.blob()), '_blank', 'noopener');
  }
}
