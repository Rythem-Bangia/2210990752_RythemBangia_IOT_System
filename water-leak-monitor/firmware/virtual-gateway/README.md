# AQUAGUARD — Virtual Gateway

A browser-based control panel that **looks and behaves like the simulated circuit**, without needing Wokwi or hardware. It talks to your Supabase project using the same RPCs the ESP32 / Raspberry Pi firmware uses, so the app and this page stay in sync.

Use it for a teacher demo when you want a "circuit on the laptop" alongside the app and don't want to fight with Wokwi's UI.

## What it does

- 4 sliders (3 moisture sensors + a local threshold knob).
- Quick presets: Dry / Normal / Damp / Wet / **Leak**.
- Two LEDs (red alarm, green valve), an animated buzzer, and a servo that swings open/closed — same visual language as the ESP32 sketch.
- Posts max moisture every 2s to `submit_sensor_reading_device` (anon RPC + device secret).
- Polls `get_zone_state_device` every 1.5s, so when the user taps **Reset valve** in the app the gateway clears its alarm and re-opens the servo.
- Threshold from the cloud overrides the local slider (matching the firmware behaviour). The pill in the top bar shows whether threshold is `(cloud)` or `(local)`.

## Run it

```bash
cd water-leak-monitor/firmware/virtual-gateway
npm start
```

Then open http://localhost:4000

There are no npm dependencies. `npm start` just runs `node server.mjs` (Node's built-in `http` module).

### Config

Two ways:

1. **Auto-load from your repo's `.env`** (the same one the Expo app uses). The server walks up to `IOT-System/.env` and reads:

   ```
   SUPABASE_URL                 # or EXPO_PUBLIC_SUPABASE_URL
   SUPABASE_ANON_KEY            # or EXPO_PUBLIC_SUPABASE_ANON_KEY
   ZONE_ID
   DEVICE_SECRET
   ```

   If your `.env` already contains the first two (it should — that's how the app talks to Supabase), just **append** the last two:

   ```
   ZONE_ID=00000000-0000-0000-0000-000000000000
   DEVICE_SECRET=00000000-0000-0000-0000-000000000000
   ```

   Get those values from Supabase Studio with this query:

   ```sql
   select z.id as zone_id, d.device_secret
   from public.zones z
   join public.devices d on d.id = z.device_id
   limit 1;
   ```

2. **Manual via the page**: just open http://localhost:4000 with no `.env` setup. A **Connect to Supabase** dialog pops up. Paste the four values, click **Save & connect**. They live in your browser's `localStorage`.

The page also has a `⚙ Config` button in the top right to re-open the dialog.

## Demo script

Run Metro / the Expo app on one half of the screen, this gateway on the other.

1. Open the app → Live tab, watching the moisture and valve.
2. In the gateway, click **Leak 95%** preset (or drag any moisture slider above the threshold).
3. Within ~2 s:
   - Gateway: red ALARM LED on, green VALVE LED off, buzzer pulsing, servo swings to closed position, log line says `cloud confirms LEAK (xxx ms)`.
   - App: moisture climbs, valve closes, leak alert email fires, history records the event.
4. In the app, tap **Reset valve**. Within ~1.5 s the gateway's poll loop sees `valve_open=true` and the alarm clears here automatically. Log line: `app sent RESET → alarm cleared`.

That's full bidirectional control — same end result as the ESP32 / Wokwi firmware, just rendered in the browser.

## How it talks to Supabase

| Direction | Endpoint | Auth |
|---|---|---|
| Gateway → cloud (every 2s) | `POST /rest/v1/rpc/submit_sensor_reading_device` | `apikey: anon` + `device_secret` in body |
| Gateway ← cloud (every 1.5s) | `POST /rest/v1/rpc/get_zone_state_device` | `apikey: anon` + `device_secret` in body |

These are the exact RPCs in `supabase/migrations/20250325120000_init.sql` and `supabase/migrations/20250516120000_get_zone_state_device.sql`. No new server code, no new tables.

## Troubleshooting

- **Top pill says `Cloud unreachable`**
  - URL is wrong, or you have no internet, or Supabase is down. Click `⚙ Config` and re-paste.
- **Top pill says `submit 401` / `submit 400 "Invalid device secret"` / `poll 404 "Zone not found"`**
  - Wrong anon key / device secret / zone id. The error body is in the **Logs** card at the bottom.
- **`get_zone_state_device` returns 404 `Could not find the function`**
  - The new migration (`20250516120000_get_zone_state_device.sql`) hasn't been applied yet. Run it in Supabase Studio's SQL editor.
- **Threshold pill keeps showing `(local)`**
  - The poll endpoint is failing (see Logs). Check the same things as the bullet above. Until cloud is reachable, the gateway uses the local threshold slider.
- **App shows nothing changing**
  - Make sure the **same zone** is selected in the app and in the gateway. The gateway's `ZONE_ID` must match the zone the app is currently viewing.

## Why this exists

Wokwi works but is fiddly on small windows / slow networks, and Tinkercad has no WiFi. This page is a deterministic, no-network-dependent fallback that demos the **exact** cloud round-trip your real ESP32 would do. It's also great for screen-recording.
