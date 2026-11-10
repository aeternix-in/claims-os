-- Step 3–4: payer rulebook, room rules, bill lines, procedure on the case.

-- hospital room tariff + whether it bills by room category (needed for proportionate deduction)
update hospitals set settings = settings
  || '{"rooms":{"general":2500,"twin":4200,"single":6500,"deluxe":9000,"icu":12500},"differentialBilling":true}'::jsonb
  where not settings ? 'rooms';
alter table hospitals alter column settings set default
  '{"preauthSlaMin":60,"dischargeSlaMin":180,"rooms":{"general":2500,"twin":4200,"single":6500,"deluxe":9000,"icu":12500},"differentialBilling":true}';

-- payer rulebook (MoU terms)
alter table payers add column rules jsonb not null default
  '{"basis":"itemised","mou_discount_pct":0,"discount_excludes":["pharmacy","implants"],"applies_proportionate":true,"package_rates":{},"tds_pct":10,"payment_term_days":30,"claim_file_window_days":15}';
update payers set rules = jsonb_set(jsonb_set(rules, '{basis}', '"package"'), '{applies_proportionate}', 'false')
  where kind = 'scheme';

-- room eligibility: {"kind":"pctSI","roomPct":1,"icuPct":2} | {"kind":"category","max":"twin"} | {"kind":"none"}
alter table policies add column room_rule jsonb not null default '{"kind":"none"}';
alter table policies add column sub_limit numeric(12,2);

alter table cases add column procedure_id text;     -- matches payer package_rates keys
alter table cases add column room_category text check (room_category in ('general','twin','single','deluxe','icu'));

create type bill_category as enum ('room','icu','ot','surgeon','anaesthesia','consult','procedure',
  'investigations','pharmacy','consumables','implants','misc','nonpayable');

create table bill_lines (
  id           uuid primary key default gen_random_uuid(),
  hospital_id  uuid not null references hospitals(id),
  case_id      uuid not null references cases(id) on delete cascade,
  position     int not null default 0,
  category     bill_category not null,
  description  text not null,
  qty          numeric(10,2) not null check (qty >= 0),
  rate         numeric(12,2) not null check (rate >= 0),
  mou_rate     numeric(12,2),
  to_payer     boolean not null default true,
  created_at   timestamptz not null default now()
);
create index bill_lines_case on bill_lines (case_id, position);

alter table bill_lines enable row level security;
create policy bill_lines_read_own on bill_lines for select to authenticated
  using (hospital_id = current_hospital_id());
