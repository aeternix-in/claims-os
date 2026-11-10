-- ONLY for local tests on plain Postgres. Never run on Supabase.
-- minimal stand-ins for Supabase-managed objects
create role authenticated nologin; create role anon nologin;
create schema auth; create schema storage;
create table auth.users (id uuid primary key default gen_random_uuid(), email text unique);
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
create table storage.buckets (id text primary key, name text, public boolean);
create publication supabase_realtime;
grant usage on schema public to authenticated;
