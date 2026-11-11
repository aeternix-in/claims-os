-- Live mode for the CTO's UI ("demo brain, saved in Supabase").
-- The UI's claim documents are stored per claim; hospital-wide parts (payers, rules, bank credits,
-- feed, settings) in one document per hospital. Browsers read through RLS; writes go through the
-- engine, which checks the hospital, the version (no silent overwrites) and that a claim's event
-- history is only ever appended to.

create table if not exists ui_state (
  hospital_id  uuid primary key references hospitals(id),
  doc          jsonb not null,
  version      int not null default 1,
  updated_at   timestamptz not null default now(),
  updated_by   uuid references auth.users(id)
);

create table if not exists ui_claims (
  hospital_id  uuid not null references hospitals(id),
  id           text not null,                 -- the UI's claim id
  doc          jsonb not null,
  version      int not null default 1,
  stage        text,                          -- copied from doc for quick lists
  updated_at   timestamptz not null default now(),
  updated_by   uuid references auth.users(id),
  primary key (hospital_id, id)
);
create index if not exists ui_claims_updated on ui_claims (hospital_id, updated_at);

-- append-only audit: every save records who changed which claim and the events it added
create table if not exists ui_claim_log (
  id           bigint generated always as identity primary key,
  hospital_id  uuid not null references hospitals(id),
  claim_id     text not null,
  version      int not null,
  actor_id     uuid references auth.users(id),
  actor_role   user_role,
  events_added jsonb not null default '[]',
  at           timestamptz not null default now()
);
create index if not exists ui_claim_log_claim on ui_claim_log (hospital_id, claim_id, id);
drop trigger if exists ui_claim_log_append_only on ui_claim_log;
create trigger ui_claim_log_append_only before update or delete on ui_claim_log
  for each row execute function forbid_change();

do $$
declare t text;
begin
  foreach t in array array['ui_state','ui_claims','ui_claim_log'] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists %I on %I', t || '_read_own', t);
    execute format('create policy %I on %I for select to authenticated using (hospital_id = current_hospital_id())',
                   t || '_read_own', t);
  end loop;
end $$;
