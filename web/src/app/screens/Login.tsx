import { useState, type FormEvent } from 'react';
import { supabase } from '../lib/api';
import wordmark from '../wordmark-white.png';

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr('');
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setErr(error.message);
    setBusy(false);
  }

  return (
    <div className="grid min-h-screen place-items-center bg-rail px-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6">
        <div className="-mx-6 -mt-6 mb-5 rounded-t-lg bg-rail px-6 py-4"><img src={wordmark} alt="Aeternix" className="h-5" /></div>
        <h1 className="mb-4 font-serif text-[22px]">Sign in to Claims OS</h1>
        <label className="label" htmlFor="email">Work email</label>
        <input id="email" className="input mb-3" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <label className="label" htmlFor="pw">Password</label>
        <input id="pw" className="input mb-4" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        {err && <p className="mb-3 rounded-md bg-crit-soft px-3 py-2 text-crit">{err}</p>}
        <button className="btn-primary w-full justify-center" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  );
}
