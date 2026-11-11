import { NavLink, Outlet } from 'react-router-dom';
import { supabase } from '../lib/api';
import { useSession } from '../lib/session';
import wordmark from '../wordmark-white.png';

/** Same 14 screens and groups as the CTO's demo. `ready` marks what is wired to the backend. */
export const NAV = [
  { name: 'overview', ready: true, label: 'Overview', group: 'Run the desk', hint: 'Money, clocks and what needs action' },
  { name: 'desk', label: 'Claims desk', group: 'Run the desk', hint: 'Every admission across every payer', ready: true },
  { name: 'preauth', ready: true, label: 'Pre-auth', group: 'Run the desk', hint: 'Requests on the one-hour clock' },
  { name: 'queries', ready: true, label: 'Queries', group: 'Run the desk', hint: 'Payer questions with drafted replies' },
  { name: 'discharge', ready: true, label: 'Discharge', group: 'Run the desk', hint: 'Final approvals on the three-hour clock' },
  { name: 'cash', ready: true, label: 'Claim to cash', group: 'Get paid', hint: 'Receivables, ageing and follow-ups' },
  { name: 'bank', ready: true, label: 'Bank match', group: 'Get paid', hint: 'Match credits to claims' },
  { name: 'leakage', ready: true, label: 'Leakage', group: 'Measure', hint: 'Where the money was lost, by cause' },
  { name: 'payers', ready: true, label: 'Payers', group: 'Measure', hint: 'Scorecards and the payer rulebook' },
  { name: 'tariffs', label: 'Tariffs', group: 'Measure', hint: 'Agreed rates and what was realised' },
  { name: 'network', label: 'Network', group: 'Measure', hint: 'Preview of cross-hospital benchmarks' },
  { name: 'check', label: 'Leakage check', group: 'Hospital', hint: "Run the hospital's own claim list through the same arithmetic" },
  { name: 'reports', ready: true, label: 'Reports', group: 'Hospital', hint: 'Exports and the owner pack' },
  { name: 'setup', ready: true, label: 'Setup', group: 'Hospital', hint: 'Hospital, simulation and data' },
] as const;

const GROUPS = [...new Set(NAV.map((n) => n.group))];

export default function Shell() {
  const { profile, hospitalName } = useSession();
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-rail text-rail-ink md:flex">
        <div className="border-b border-rail-line px-5 py-4">
          <img src={wordmark} alt="Aeternix" className="h-5" />
          <div className="mt-2 truncate text-[12px] text-rail-ink-2">{hospitalName}</div>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3">
          {GROUPS.map((g) => (
            <div key={g} className="mb-3">
              <div className="px-3 pb-1 text-[11px] uppercase tracking-wider text-rail-ink-2">{g}</div>
              {NAV.filter((n) => n.group === g).map((n) => (
                <NavLink key={n.name} to={`/${n.name}`} title={n.hint}
                  className={({ isActive }) =>
                    `flex items-center justify-between rounded-md px-3 py-1.5 ${isActive ? 'bg-rail-hover text-white' : 'text-rail-ink-2 hover:bg-rail-hover hover:text-rail-ink'}`}>
                  {n.label}
                  {!('ready' in n) && <span className="text-[10px] text-rail-ink-2/60">soon</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t border-rail-line px-5 py-3 text-[12px]">
          <div className="text-rail-ink">{profile?.full_name}</div>
          <div className="flex justify-between text-rail-ink-2">
            <span className="capitalize">{profile?.role}</span>
            <button onClick={() => supabase.auth.signOut()} className="hover:text-white">Sign out</button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8"><Outlet /></main>
    </div>
  );
}
