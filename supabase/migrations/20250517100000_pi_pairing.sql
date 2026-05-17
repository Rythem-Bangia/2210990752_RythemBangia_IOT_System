-- Lightweight pairing table so a Raspberry Pi (or any device) can ask the
-- user to bind it to one of their zones from inside the Expo app — no env
-- vars, no copy/paste of UUIDs.
--
-- Flow:
--   1. Pi calls register_pi_pairing(code)            -- anon
--   2. User opens app, types code, picks a zone
--      App calls claim_pi_pairing(code, zone_id)     -- authenticated
--   3. Pi polls read_pi_pairing(code)                -- anon
--      → gets {zone_id, device_secret} and the row is deleted
--
-- Codes expire after 10 minutes. RLS is on; the only access path is
-- through these security-definer RPCs.

create table if not exists public.pi_pairings (
  code           text primary key,
  zone_id        uuid references public.zones on delete cascade,
  device_secret  uuid,
  created_at     timestamptz not null default now(),
  claimed_at     timestamptz
);

create index if not exists pi_pairings_created_at_idx
  on public.pi_pairings (created_at);

alter table public.pi_pairings enable row level security;

-- ─── Garbage collector (called inline by every RPC) ────────────────────
create or replace function public.gc_pi_pairings()
returns void
language sql
security definer
set search_path = public
as $$
  delete from public.pi_pairings
  where created_at < now() - interval '10 minutes';
$$;

-- ─── Device side: register/refresh a pairing code ──────────────────────
create or replace function public.register_pi_pairing(p_code text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_code is null or length(p_code) < 4 or length(p_code) > 16 then
    raise exception 'Bad pairing code';
  end if;

  perform public.gc_pi_pairings();

  insert into public.pi_pairings (code, created_at)
  values (p_code, now())
  on conflict (code) do update
    set created_at = excluded.created_at,
        zone_id = case when public.pi_pairings.claimed_at is null
                       then null else public.pi_pairings.zone_id end,
        device_secret = case when public.pi_pairings.claimed_at is null
                             then null else public.pi_pairings.device_secret end;
end;
$$;

-- ─── App side: claim a pending pairing for one of the caller's zones ──
create or replace function public.claim_pi_pairing(
  p_code    text,
  p_zone_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner     uuid;
  v_secret    uuid;
  v_zone_name text;
begin
  perform public.gc_pi_pairings();

  select d.user_id, d.device_secret, z.name
  into v_owner, v_secret, v_zone_name
  from public.zones z
  join public.devices d on d.id = z.device_id
  where z.id = p_zone_id;

  if not found then
    raise exception 'Zone not found';
  end if;

  if v_owner is distinct from auth.uid() then
    raise exception 'Not authorized for this zone';
  end if;

  if not exists (select 1 from public.pi_pairings where code = p_code) then
    raise exception 'Pairing code not found or expired';
  end if;

  update public.pi_pairings
  set zone_id       = p_zone_id,
      device_secret = v_secret,
      claimed_at    = now()
  where code = p_code;

  return jsonb_build_object(
    'ok', true,
    'zone_name', v_zone_name
  );
end;
$$;

-- ─── Device side: read claimed pairing once, then delete it ────────────
create or replace function public.read_pi_pairing(p_code text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_zone   uuid;
  v_secret uuid;
begin
  perform public.gc_pi_pairings();

  select zone_id, device_secret
  into v_zone, v_secret
  from public.pi_pairings
  where code = p_code and claimed_at is not null;

  if not found then
    return jsonb_build_object('claimed', false);
  end if;

  delete from public.pi_pairings where code = p_code;

  return jsonb_build_object(
    'claimed', true,
    'zone_id', v_zone,
    'device_secret', v_secret
  );
end;
$$;

grant execute on function public.register_pi_pairing(text) to anon, authenticated;
grant execute on function public.read_pi_pairing(text)     to anon, authenticated;
grant execute on function public.claim_pi_pairing(text, uuid) to authenticated;
