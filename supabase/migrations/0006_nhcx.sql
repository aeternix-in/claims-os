-- NHCX: identifiers the exchange needs, the message log, and payer-driven moves.
-- The NHCX protocol itself (FHIR ClaimBundle, envelope, JWE, sandbox session) is aeternix-core (nhcx/aeternix-core).

-- payer decisions arriving over NHCX move cases as the "system" actor
alter type user_role add value if not exists 'system';

-- NHA guide identifiers
alter table hospitals add column if not exists hfr_id text;                     -- Health Facility Registry ID (NPI)
alter table hospitals add column if not exists nhcx_participant_code text;      -- ours, e.g. hospital@hcx
alter table payers add column if not exists nhcx_participant_code text;         -- insurer (NIIP)
alter table payers add column if not exists nhcx_processing_code text;          -- who processes (the TPA, if any)
alter table policies add column if not exists product_code text;                -- IRDAI product code (Coverage NH)
alter table policies add column if not exists relationship text not null default 'self'
  check (relationship in ('self','spouse','child','parent','other'));
alter table cases add column if not exists doctor_hpr_id text;                  -- treating doctor's HPR ID (HPIN)
alter table bill_lines add column if not exists snomed_code text;               -- NHCX bills by SNOMED CT

-- what NHCX told us
alter table cases add column if not exists nhcx_last_decision jsonb;            -- interpreted ClaimResponse
alter table cases add column if not exists nhcx_utr text;                       -- UTR from the payment notice
alter table cases add column if not exists nhcx_paid_amount numeric(12,2);      -- amount on the payment notice

-- insurer queries that came over NHCX are answered over NHCX
alter table queries add column if not exists source text not null default 'desk' check (source in ('desk','nhcx'));
alter table queries add column if not exists nhcx_request jsonb;                -- received headers, CommunicationRequest id, workflow

create table if not exists nhcx_exchanges (
  id             bigint generated always as identity primary key,
  hospital_id    uuid not null references hospitals(id),
  case_id        uuid not null references cases(id),
  workflow       text not null,          -- preauth | claim | communication | paymentnotice
  action         text not null,          -- submit | on_submit | request | on_request
  direction      text not null check (direction in ('outbound','inbound')),
  api_call_id    text not null unique,
  correlation_id text not null,
  workflow_id    text,                   -- NHA code: 12, 121, 15, 19, 161, 24, 27 ...
  status         text not null,          -- sending | acked | failed | received
  http_status    int,
  error          text,
  wire           jsonb,                  -- the body as sent / received (JWE string in jwe mode)
  plain          jsonb,                  -- readable headers + FHIR. Patient data: same retention as the case.
  sync_response  jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists nhcx_exchanges_case on nhcx_exchanges (case_id, id);
create index if not exists nhcx_exchanges_corr on nhcx_exchanges (correlation_id, workflow, direction);
create trigger nhcx_exchanges_touch before update on nhcx_exchanges for each row execute function touch_updated_at();

alter table nhcx_exchanges enable row level security;
create policy nhcx_exchanges_read_own on nhcx_exchanges for select to authenticated
  using (hospital_id = current_hospital_id());
