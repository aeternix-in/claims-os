-- Files uploaded against a claim in the live UI (Documents tab). The file itself sits in Storage
-- (bucket "documents", private); this row says which claim and checklist item it belongs to, who
-- uploaded it, and what was read from it. Rows are never deleted: "Remove" only marks them removed.

create table if not exists ui_files (
  id           uuid primary key default gen_random_uuid(),
  hospital_id  uuid not null references hospitals(id),
  claim_id     text not null,                 -- the UI's claim id
  doc_id       text not null,                 -- the checklist item on the claim (e.g. L-01002-d3)
  sha256       text not null,
  storage_path text not null,
  filename     text not null,
  mime         text not null,
  size_bytes   int not null,
  pages        int not null default 0,
  read_method  text,
  doc_type     text,                          -- what the reader thinks the document is
  text         text,                          -- text read from the pages (for search and checks)
  fields       jsonb not null default '[]',   -- key fields read, with page and snippet
  uploaded_by  uuid references auth.users(id),
  created_at   timestamptz not null default now(),
  removed_at   timestamptz,
  removed_by   uuid references auth.users(id)
);
create index if not exists ui_files_doc on ui_files (hospital_id, doc_id, created_at desc);
create index if not exists ui_files_claim on ui_files (hospital_id, claim_id);

alter table ui_files enable row level security;
drop policy if exists ui_files_read_own on ui_files;
create policy ui_files_read_own on ui_files for select to authenticated using (hospital_id = current_hospital_id());
