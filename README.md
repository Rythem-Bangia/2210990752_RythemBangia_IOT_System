Rythem Bangia - 2210990752









# Water leak IoT — React Native + Supabase + Tailwind (NativeWind)

End-to-end system for **moisture-based leak detection**, **automatic solenoid valve shutoff** (represented in software), **email alerts**, and **history** — compatible with **physical** hardware (MCU + sensors + valve driver) and **virtual** simulation (no hardware).

## What you get

| Layer | Role |
|--------|------|
| **React Native (Expo)** | Mobile app: thresholds, live moisture, valve state, virtual simulator, physical integration hints |
| **Supabase (Postgres)** | Auth, RLS, zones/devices, readings, leak events, RPC for threshold logic + valve close |
| **Supabase Edge Function** | Sends email via [Resend](https://resend.com) when a leak is detected |
| **NativeWind** | Tailwind CSS utility classes in React Native |

## Project layout

```
IOT System/
├── README.md                 # This file
├── supabase/
│   ├── migrations/           # SQL schema + RPCs
│   └── functions/
│       └── send-leak-alert/  # Email Edge Function (Resend)
└── water-leak-monitor/       # Expo app
    ├── .env.example
    ├── App.tsx
    ├── global.css
    └── src/
```

## Invention summary (problem / solution)

**Problem:** Water leaks often stay hidden until damage is severe; manual checks are slow; alarms without shutoff are incomplete; industrial systems are costly for homes.

**Solution:** Moisture sensors feed a decision path (threshold + debouncing possible in firmware). On leak confirmation, the system **closes the solenoid valve** (modeled as `valve_open` in the database), **logs the event**, **sends an email**, and supports **reset after repair** from the app. **Physical** mode uses your MCU to call Supabase RPC with a **device secret**; **virtual** mode simulates moisture in the app.

**Advantages:** Fast response path, automatic shutoff, remote monitoring, scalable zones, history for analysis.

## Stepwise workflow (aligned with your spec)

1. **Sensing** — Moisture % is sent to Supabase (`sensor_readings` + `zones.last_moisture`).
2. **Processing** — RPC `submit_sensor_reading` or `submit_sensor_reading_device` compares to `moisture_threshold`.
3. **Local alarm** — Implement buzzer/LED on the MCU (not in this repo; app shows status).
4. **Shutoff** — RPC sets `zone.valve_open = false` and records `leak_events`.
5. **IoT notification** — App invokes `send-leak-alert`; firmware can call the same function with `device_secret`.
6. **Logging** — `leak_events` + history screen.
7. **Reset** — RPC `reset_zone_valve` after inspection; valve reopens only when the user confirms safe conditions.

## Prerequisites

- Node.js 18+
- [Supabase](https://supabase.com) project
- [Resend](https://resend.com) API key (for email)
- Expo Go or a dev build for the mobile app

## 1. Supabase database

1. Open **SQL Editor** in the Supabase dashboard.
2. Run the migration file: `supabase/migrations/20250325120000_init.sql`.

This creates `profiles`, `devices`, `zones`, `sensor_readings`, `leak_events`, RLS policies, and RPCs:

- `submit_sensor_reading(p_zone_id, p_moisture, p_source)` — authenticated app user.
- `submit_sensor_reading_device(p_zone_id, p_moisture, p_device_secret)` — physical device (anon + secret).
- `reset_zone_valve(p_zone_id)` — user reset after repair.

Optional: enable **Realtime** on `zones` / `leak_events` under **Database → Replication**.

## 2. Edge Function (email)

If the app shows **`Requested function was not found` (HTTP 404 / `NOT_FOUND`)**, the Edge Function has not been deployed to your Supabase project yet. Deploy from the **`IOT System`** directory (the folder that contains `supabase/functions`, not only `water-leak-monitor`):

```bash
cd /path/to/IOT System
supabase login
supabase link --project-ref YOUR_PROJECT_REF
supabase functions deploy send-leak-alert
```

1. Install [Supabase CLI](https://supabase.com/docs/guides/cli) and link your project, or use the dashboard **Edge Functions** editor.
2. Deploy `supabase/functions/send-leak-alert` (command above).
3. Set secrets (CLI example):

```bash
supabase secrets set RESEND_API_KEY=re_xxx
# Optional: custom from-address after domain verification
supabase secrets set RESEND_FROM="Water Leak <alerts@yourdomain.com>"
```

4. In Resend, verify a domain or use the onboarding sender for testing.

The function accepts:

- **Authorization: Bearer &lt;user JWT&gt;** (from the mobile app) with body `{ "leak_event_id": "..." }`, or  
- **Authorization: Bearer &lt;anon key&gt;** with body `{ "leak_event_id": "...", "device_secret": "..." }` for firmware.

## 3. React Native app

```bash
cd water-leak-monitor
copy .env.example .env
# Edit .env with EXPO_PUBLIC_SUPABASE_URL and EXPO_PUBLIC_SUPABASE_ANON_KEY

npm install
npx expo start
```

Use **Monitor** to switch **virtual** vs **physical**, adjust moisture, **Send reading** (virtual or bench-test physical), and **Reset valve** after a leak. **Settings** stores **alert email** and shows **device secret** for firmware.

## Physical firmware (ESP32 / Arduino)

- **Readings:** `POST /rest/v1/rpc/submit_sensor_reading_device` with anon key + JSON body `{ "p_zone_id", "p_moisture", "p_device_secret" }`.
- **Email (after leak):** `POST /functions/v1/send-leak-alert` with anon key + `{ "leak_event_id", "device_secret" }` (or rely on the app to send mail when testing).

Copy **REST hints** from the app (physical mode) on a zone card.

## Virtual mode

Use the slider and **Send reading**, or enable **auto simulator** to drift moisture and then send readings manually to cross the threshold.

## Security notes

- Never ship the **service role** key in the app.
- Keep **device_secret** per device; rotate if leaked.
- RLS restricts users to their own devices and zones.

---

## License

Use and modify for your own project and patent documentation as needed.
