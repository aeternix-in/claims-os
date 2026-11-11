import { Link } from 'react-router-dom';
import Worklist from '../components/Worklist';
import LettersPanel from '../components/LettersPanel';
import { STAGE_LABEL, STAGE_TONE, type CaseRow } from '../lib/types';

export function StageBadge({ stage }: { stage: CaseRow['stage'] }) {
  const tone = STAGE_TONE[stage];
  const cls = tone === 'warn' ? 'bg-warn-soft text-warn' : tone === 'crit' ? 'bg-crit-soft text-crit'
    : tone === 'good' ? 'bg-good-soft text-good' : 'bg-accent-soft text-accent';
  return <span className={`whitespace-nowrap rounded px-2 py-0.5 text-[12px] font-medium ${cls}`}>{STAGE_LABEL[stage]}</span>;
}

const newAdmission = <Link to="/admit" className="btn-primary">New admission</Link>;

export const Desk = () => <Worklist title="Claims desk" hint="Every admission across every payer" action={newAdmission}
  empty="No admissions yet. Create the first one." />;

export const Preauth = () => <Worklist title="Pre-auth" hint="Requests on the one-hour clock, soonest due first"
  stages={['admitted', 'preauth', 'preauth_pending', 'preauth_query']} showClock action={newAdmission} />;


export const Discharge = () => <Worklist title="Discharge" hint="Final approvals on the three-hour clock"
  stages={['in_treatment', 'discharge', 'discharge_pending', 'discharge_query']} showClock />;

export const Cash = () => (
  <div>
    <Worklist title="Claim to cash" hint="From discharge to the credit in the bank"
      stages={['discharged', 'claim', 'submitted', 'claim_query', 'settled', 'rejected', 'paid']} />
    <div className="mt-6"><LettersPanel /></div>
  </div>
);

export default Desk;
