-- Aeternix Claims OS: core ledger schema (step 1)
-- Run in Supabase SQL editor, or `supabase db push`.
-- Rule: browsers READ through RLS; only the engine (direct DB connection) WRITES.

create extension if not exists pgcrypto;

-- ---------- enums ----------
create type user_role as enum ('desk', 'doctor', 'accounts', 'owner', 'admin');

create type claim_stage as enum (
  'admitted', 'preauth', 'preauth_pending', 'preauth_query', 'denied',
  'in_treatment', 'discharge', 'discharge_pending', 'discharge_query', 'discharged',
  'claim', 'submitted', 'claim_query', 'settled', 'rejected', 'paid', 'closed'
);

create type payer_kind as enum ('insurer', 'tpa', 'scheme');

-- ---------- tenancy ----------
create table hospitals (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  city        text,
  beds        int,
  settings    jsonb not null default '{"preauthSlaMin":60,"dischargeSlaMin":180}',
  created_at  timestamptz not null default now()
);

create table profiles (
  user_id      uuid primary key references auth.users(id) on delete cascade,
  hospital_id  uuid not null references hospitals(id),
  full_name    text not null,
  role         user_role not null default 'desk',
  created_at   timestamptz not null default now()
);

-- the hospital of whoever is calling (used by every RLS policy)
create or replace function public.current_hospital_id() returns uuid
language sql stable security definer set search_path = public as $$
  select hospital_id from profiles where user_id = auth.uid()
$$;

-- ---------- reference data ----------
create table payers (
  id           uuid primary key default gen_random_uuid(),
  hospital_id  uuid not null references hospitals(id),
  name         text not null,
  kind         payer_kind not null,
  via_tpa      text,                       -- TPA that processes for this insurer, if any
  created_at   timestamptz not null default now()
);

create table patients (
  id           uuid primary key default gen_random_uuid(),
  hospital_id  uuid not null references hospitals(id),
  full_name    text not null,
  sex          text check (sex in ('M','F','O')),
  dob          date,
  phone        text,
  abha_id      text,
  uhid         text,                       -- hospital's own patient number
  created_at   timestamptz not null default now()
);

create table policies (
  id                 uuid primary key default gen_random_uuid(),
  hospital_id        uuid not null references hospitals(id),
  patient_id         uuid not null references patients(id),
  payer_id           uuid not null references payers(id),
  policy_number      text not null,
  member_id          text,
  sum_insured        numeric(12,2),
  balance_available  numeric(12,2),
  room_rent_cap      numeric(10,2),        -- per day; null = no cap
  room_rent_cap_pct  numeric(5,2),         -- % of sum insured per day, if capped that way
  icu_cap            numeric(10,2),
  copay_pct          numeric(5,2) not null default 0,
  sub_limits         jsonb not null default '{}',   -- {"cataract": 40000, ...}
  valid_from         date,
  valid_to           date,
  created_at         timestamptz not null default now()
);

-- ---------- the claim ----------
create table cases (
  id             uuid primary key default gen_random_uuid(),
  hospital_id    uuid not null references hospitals(id),
  case_number    text not null,            -- human-friendly, e.g. AX-2611-0042; safe for messages
  patient_id     uuid not null references patients(id),
  policy_id      uuid references policies(id),
  payer_id       uuid references payers(id),
  stage          claim_stage not null default 'admitted',
  admitted_at    timestamptz not null default now(),
  discharged_at  timestamptz,
  treating_doctor text,
  ward_category  text,
  room_rent_per_day numeric(10,2),
  provisional_dx text,
  icd10          text,
  estimate       numeric(12,2),
  approved_amount numeric(12,2) not null default 0,
  billed_amount  numeric(12,2) not null default 0,
  settled_amount numeric(12,2),
  received_amount numeric(12,2),
  version        int not null default 0,   -- bumped on every state move (optimistic checks)
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (hospital_id, case_number)
);
create index cases_worklist on cases (hospital_id, stage, updated_at desc);

-- event-sourced timeline = audit log. Append-only.
create table case_events (
  id           bigint generated always as identity primary key,
  hospital_id  uuid not null references hospitals(id),
  case_id      uuid not null references cases(id),
  event        text not null,
  from_stage   claim_stage,
  to_stage     claim_stage not null,
  actor_id     uuid references auth.users(id),
  actor_role   user_role,
  payload      jsonb not null default '{}',
  note         text,
  created_at   timestamptz not null default now()
);
create index case_events_case on case_events (case_id, id);

create or replace function forbid_change() returns trigger language plpgsql as $$
begin raise exception 'case_events is append-only'; end $$;
create trigger case_events_append_only before update or delete on case_events
  for each row execute function forbid_change();

-- IRDAI clocks (1h pre-auth, 3h discharge) and later scheme deadlines
create table clocks (
  id           uuid primary key default gen_random_uuid(),
  hospital_id  uuid not null references hospitals(id),
  case_id      uuid not null references cases(id),
  kind         text not null,              -- 'preauth' | 'discharge' | ...
  started_at   timestamptz not null default now(),
  due_at       timestamptz not null,
  stopped_at   timestamptz,
  breached     boolean generated always as (stopped_at is not null and stopped_at > due_at) stored,
  source       text                        -- circular the deadline comes from
);
create index clocks_open on clocks (hospital_id, due_at) where stopped_at is null;

-- original documents: immutable, named by hash (file lives in Storage bucket 'documents')
create table documents (
  id            uuid primary key default gen_random_uuid(),
  hospital_id   uuid not null references hospitals(id),
  case_id       uuid not null references cases(id),
  sha256        text not null,
  storage_path  text not null,
  filename      text,
  mime          text,
  doc_type      text,                      -- discharge_summary, bill, lab, ...
  pages         int,
  uploaded_by   uuid references auth.users(id),
  created_at    timestamptz not null default now(),
  unique (hospital_id, sha256)
);

-- keep updated_at fresh
create or replace function touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;
create trigger cases_touch before update on cases for each row execute function touch_updated_at();

-- ---------- RLS: read own hospital only, no client writes ----------
do $$
declare t text;
begin
  foreach t in array array['payers','patients','policies','cases','case_events','clocks','documents'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select to authenticated using (hospital_id = current_hospital_id())',
                   t || '_read_own', t);
  end loop;
end $$;

alter table hospitals enable row level security;
create policy hospitals_read_own on hospitals for select to authenticated using (id = current_hospital_id());

alter table profiles enable row level security;
create policy profiles_read_same_hospital on profiles for select to authenticated
  using (hospital_id = current_hospital_id());

-- live worklist updates
alter publication supabase_realtime add table cases, clocks;

-- private bucket for originals (engine uploads with service role)
insert into storage.buckets (id, name, public) values ('documents', 'documents', false)
  on conflict do nothing;
