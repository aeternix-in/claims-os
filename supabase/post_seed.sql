insert into profiles (user_id, hospital_id, full_name, role)
select u.id, (select id from hospitals order by created_at limit 1), x.name, x.role::user_role
from auth.users u
join (values ('contact@aeternix.in', 'Aeternix Admin', 'owner'),
             ('desk@aeternix.in',    'Insurance Desk', 'desk')) as x(email, name, role)
  on lower(u.email) = x.email
on conflict (user_id) do update
  set role = excluded.role, full_name = excluded.full_name, hospital_id = excluded.hospital_id;

-- check : select u.email, p.full_name, p.role from profiles p join auth.users u on u.id = p.user_id; 
