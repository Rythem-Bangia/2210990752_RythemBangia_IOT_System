-- ESP32 / IoT firmware needs to know when the user resets the valve in
-- the app or the cloud forces a leak, so it can drive its own buzzer
-- and servo. Zones are RLS-locked to the owning user, so the device
-- (anon role + per-device secret) cannot SELECT from public.zones
-- directly. This security-definer RPC exposes just the small bit of
-- state the firmware needs, gated by the device secret.

create or replace function public.get_zone_state_device(
  p_zone_id uuid,
  p_device_secret uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_zone record;
begin
  select z.valve_open,
         z.valve_closed_at,
         z.moisture_threshold,
         z.last_moisture,
         d.device_secret as expected_secret
  into v_zone
  from public.zones z
  join public.devices d on d.id = z.device_id
  where z.id = p_zone_id;

  if not found then
    raise exception 'Zone not found';
  end if;

  if p_device_secret is distinct from v_zone.expected_secret then
    raise exception 'Invalid device secret';
  end if;

  return jsonb_build_object(
    'valve_open', v_zone.valve_open,
    'threshold', v_zone.moisture_threshold,
    'last_moisture', v_zone.last_moisture,
    'valve_closed_at', v_zone.valve_closed_at
  );
end;
$$;

grant execute on function public.get_zone_state_device(uuid, uuid) to anon, authenticated;
