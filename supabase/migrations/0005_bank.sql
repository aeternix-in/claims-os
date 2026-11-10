-- Step 7: bank statement import and matching credits to claims.

-- payer's own reference for the settlement (from the settlement letter); used to match credits
alter table cases add column payer_ref text;
create index cases_payer_ref on cases (hospital_id, payer_ref) where payer_ref is not null;

create table bank_imports (
  id           uuid primary key default gen_random_uuid(),
  hospital_id  uuid not null references hospitals(id),
  filename     text,
  rows_read    int not null default 0,
  credits_new  int not null default 0,
  duplicates   int not null default 0,
  skipped      int not null default 0,      -- debits and unreadable rows
  is_sample    boolean not null default false,
  imported_by  uuid references auth.users(id),
  created_at   timestamptz not null default now()
);

create table bank_credits (
  id            uuid primary key default gen_random_uuid(),
  hospital_id   uuid not null references hospitals(id),
  import_id     uuid references bank_imports(id),
  value_date    date not null,
  amount        numeric(12,2) not null check (amount > 0),
  narration     text not null,
  utr           text,
  payer_id      uuid references payers(id),     -- detected from the narration
  status        text not null default 'unmatched' check (status in ('unmatched','matched','ignored')),
  ignored_note  text,
  is_sample     boolean not null default false,
  created_at    timestamptz not null default now(),
  unique (hospital_id, utr, amount)
);
create index bank_credits_open on bank_credits (hospital_id, status, value_date desc);

create table credit_matches (
  id            uuid primary key default gen_random_uuid(),
  hospital_id   uuid not null references hospitals(id),
  credit_id     uuid not null references bank_credits(id),
  case_id       uuid not null references cases(id),
  amount        numeric(12,2) not null check (amount > 0),
  expected      numeric(12,2),                  -- settled less TDS at the time of matching
  confidence    text not null check (confidence in ('exact','likely','split','manual')),
  why           text not null,
  confirmed_by  uuid not null references auth.users(id),
  confirmed_at  timestamptz not null default now(),
  unique (credit_id, case_id)
);

do $$
declare t text;
begin
  foreach t in array array['bank_imports','bank_credits','credit_matches'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select to authenticated using (hospital_id = current_hospital_id())',
                   t || '_read_own', t);
  end loop;
end $$;

alter publication supabase_realtime add table bank_credits;
