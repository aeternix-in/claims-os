export type Role = 'desk' | 'doctor' | 'accounts' | 'owner' | 'admin';

export type Stage =
  | 'admitted' | 'preauth' | 'preauth_pending' | 'preauth_query' | 'denied'
  | 'in_treatment' | 'discharge' | 'discharge_pending' | 'discharge_query' | 'discharged'
  | 'claim' | 'submitted' | 'claim_query' | 'settled' | 'rejected' | 'paid' | 'closed';

export const STAGE_LABEL: Record<Stage, string> = {
  admitted: 'Admitted', preauth: 'Preparing pre-auth', preauth_pending: 'Pre-auth with payer',
  preauth_query: 'Pre-auth query', denied: 'Pre-auth denied', in_treatment: 'In treatment',
  discharge: 'Preparing discharge', discharge_pending: 'Final approval with payer',
  discharge_query: 'Discharge query', discharged: 'Discharged', claim: 'Preparing claim file',
  submitted: 'Claim with payer', claim_query: 'Claim query', settled: 'Settled, awaiting credit',
  rejected: 'Rejected', paid: 'Paid', closed: 'Closed',
};

export const STAGE_TONE: Partial<Record<Stage, 'warn' | 'crit' | 'good'>> = {
  preauth_query: 'warn', discharge_query: 'warn', claim_query: 'warn',
  denied: 'crit', rejected: 'crit', paid: 'good', closed: 'good',
};

export interface Profile { user_id: string; hospital_id: string; full_name: string; role: Role }

export interface CaseRow {
  id: string; case_number: string; stage: Stage; version: number;
  patient_id: string; payer_id: string | null; admitted_at: string; updated_at: string;
  provisional_dx: string | null; estimate: number | null; approved_amount: number;
  billed_amount: number; treating_doctor: string | null;
  patients?: { full_name: string } | null; payers?: { name: string } | null;
}
