-- Step 6: query desk and letters.

create table queries (
  id                uuid primary key default gen_random_uuid(),
  hospital_id       uuid not null references hospitals(id),
  case_id           uuid not null references cases(id) on delete cascade,
  stage             text not null check (stage in ('preauth','discharge','claim')),
  query_text        text not null,
  code              text not null check (code in ('Q1','Q2','Q3','Q4','Q5','Q6','Q7','Q8')),
  code_set_by       text not null default 'auto',       -- auto | person
  status            text not null default 'open' check (status in ('open','drafted','signed','sent')),
  draft             text,
  attachments       jsonb not null default '[]',        -- document ids
  missing           jsonb not null default '[]',        -- document types asked for but not on file
  requires_signoff  boolean not null default false,
  signed_by         uuid references auth.users(id),
  signed_name       text,
  signed_at         timestamptz,
  sent_by           uuid references auth.users(id),
  sent_at           timestamptz,
  received_at       timestamptz not null default now(),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index queries_open on queries (hospital_id, status, received_at);
create trigger queries_touch before update on queries for each row execute function touch_updated_at();

create table letters (
  id            uuid primary key default gen_random_uuid(),
  hospital_id   uuid not null references hospitals(id),
  case_id       uuid not null references cases(id) on delete cascade,
  kind          text not null check (kind in ('reminder','grievance','dispute','appeal','condonation')),
  subject       text not null,
  body          text not null,
  reason        text not null,                       -- why the sweep drafted it
  sources       jsonb not null default '[]',
  amount        numeric(12,2),
  status        text not null default 'draft' check (status in ('draft','approved','sent','dropped')),
  created_by    uuid references auth.users(id),      -- null = drafted by the sweep
  approved_by   uuid references auth.users(id),
  approved_at   timestamptz,
  sent_at       timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index letters_status on letters (hospital_id, status, created_at desc);
create trigger letters_touch before update on letters for each row execute function touch_updated_at();

-- hospital name/address used in letters
alter table hospitals add column address text;
alter table hospitals add column letter_signatory text;

do $$
declare t text;
begin
  foreach t in array array['queries','letters'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select to authenticated using (hospital_id = current_hospital_id())',
                   t || '_read_own', t);
  end loop;
end $$;

alter publication supabase_realtime add table queries, letters;
