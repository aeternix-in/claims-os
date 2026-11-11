import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from './api';
import type { Profile } from './types';

interface Ctx { session: Session | null; profile: Profile | null; hospitalName: string; loading: boolean }
const SessionCtx = createContext<Ctx>({ session: null, profile: null, hospitalName: '', loading: true });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [hospitalName, setHospitalName] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); if (!data.session) setLoading(false); });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!session) { setProfile(null); return; }
    (async () => {
      const { data: p } = await supabase.from('profiles').select('*').eq('user_id', session.user.id).maybeSingle();
      setProfile(p as Profile | null);
      if (p) {
        const { data: h } = await supabase.from('hospitals').select('name').eq('id', p.hospital_id).maybeSingle();
        setHospitalName(h?.name ?? '');
      }
      setLoading(false);
    })();
  }, [session]);

  return <SessionCtx.Provider value={{ session, profile, hospitalName, loading }}>{children}</SessionCtx.Provider>;
}

export const useSession = () => useContext(SessionCtx);
