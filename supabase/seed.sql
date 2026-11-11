-- Run once AFTER 0001_core.sql. First create a user in Supabase -> Authentication -> Users,
-- then put that email below.
with h as (
  insert into hospitals (name, city, beds) values ('Demo Hospital', 'New Delhi', 180) returning id
), p as (
  insert into payers (hospital_id, name, kind, via_tpa)
  select h.id, x.name, x.kind::payer_kind, x.tpa from h, (values
    ('Insurer A', 'insurer', 'TPA One'), ('Insurer B', 'insurer', null), ('Insurer C', 'insurer', 'TPA Two'),
    ('CGHS', 'scheme', null), ('PM-JAY', 'scheme', null), ('ECHS', 'scheme', null), ('DGEHS', 'scheme', null)
  ) as x(name, kind, tpa) returning 1
)
insert into profiles (user_id, hospital_id, full_name, role)
select u.id, h.id, 'Desk Lead', 'desk' from auth.users u, h
where u.email = 'CHANGE-ME@example.com';
