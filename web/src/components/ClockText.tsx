import { useEffect, useState } from 'react';

/** Live countdown for an IRDAI clock. */
export function ClockText({ due, stopped }: { due: string; stopped: string | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (stopped) return;
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(t);
  }, [stopped]);
  const end = stopped ? new Date(stopped).getTime() : now;
  const left = Math.round((new Date(due).getTime() - end) / 60000);
  const late = left < 0;
  const fmt = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);
  return (
    <span className={`num ${late ? 'text-crit' : left < 15 ? 'text-warn' : 'text-ink-2'}`}>
      {stopped ? (late ? `answered ${fmt(-left)} late` : 'answered in time') : late ? `${fmt(-left)} overdue` : `${fmt(left)} left`}
    </span>
  );
}
