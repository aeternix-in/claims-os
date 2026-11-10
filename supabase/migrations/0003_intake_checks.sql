-- Step 5: document intake, extracted fields with provenance, pre-flight checks.

alter table documents add column read_method text;            -- pdf-text | ocr-tesseract | pdf-text+ocr | text
alter table documents add column page_types jsonb not null default '[]';
alter table documents add column type_scores jsonb not null default '{}';
alter table documents add column type_confirmed boolean not null default false;
alter table documents add column size_bytes int;

-- page text (for clinical-fit search). Stays in India region with the rest of the record.
create table document_pages (
  document_id  uuid not null references documents(id) on delete cascade,
  hospital_id  uuid not null references hospitals(id),
  page         int not null,
  text         text not null,
  page_type    text,
  primary key (document_id, page)
);

create table extracted_fields (
  id             uuid primary key default gen_random_uuid(),
  hospital_id    uuid not null references hospitals(id),
  case_id        uuid not null references cases(id) on delete cascade,
  document_id    uuid not null references documents(id) on delete cascade,
  name           text not null,
  value          text not null,
  original_value text not null,               -- what the extractor read; value may be corrected
  page           int not null,
  start_pos      int,
  end_pos        int,
  snippet        text,
  method         text not null,               -- extractor name + version
  confirmed      boolean not null default false,
  confirmed_by   uuid references auth.users(id),
  confirmed_at   timestamptz,
  created_at     timestamptz not null default now()
);
create index extracted_fields_case on extracted_fields (case_id, name);

create table check_runs (
  id           bigint generated always as identity primary key,
  hospital_id  uuid not null references hospitals(id),
  case_id      uuid not null references cases(id) on delete cascade,
  stage        text not null check (stage in ('preauth','discharge','claim')),
  status       text not null check (status in ('red','amber','green')),
  summary      jsonb not null,
  run_by       uuid references auth.users(id),
  trigger      text not null default 'manual',  -- manual | gate
  created_at   timestamptz not null default now()
);
create index check_runs_case on check_runs (case_id, stage, id desc);

create table check_clearances (
  id           uuid primary key default gen_random_uuid(),
  hospital_id  uuid not null references hospitals(id),
  case_id      uuid not null references cases(id) on delete cascade,
  stage        text not null,
  rule_id      text not null,
  note         text not null check (length(trim(note)) >= 5),
  cleared_by   uuid not null references auth.users(id),
  cleared_name text not null,
  cleared_role user_role not null,
  created_at   timestamptz not null default now(),
  unique (case_id, stage, rule_id)
);

do $$
declare t text;
begin
  foreach t in array array['document_pages','extracted_fields','check_runs','check_clearances'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select to authenticated using (hospital_id = current_hospital_id())',
                   t || '_read_own', t);
  end loop;
end $$;
