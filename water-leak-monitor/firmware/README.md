# AQUAGUARD IoT - Dynamic Water Leak Detection

Three options live in this folder, ranked by how easy they are to demo:

| Folder | Best for | Talks to app via |
|--------|----------|------------------|
| `virtual-gateway/` | **Browser-based control panel — easiest, zero hardware, zero simulator** | Direct HTTPS to Supabase RPC, runs in any browser |
| `aquaguard_esp32_wokwi/` | Wokwi simulator or a real ESP32 | Direct HTTPS to Supabase RPC, no PC bridge |
| `aquaguard_iot/` | Tinkercad / Arduino Uno over USB | Manual mirror, or `bridge/serial-bridge.mjs` |

**For a teacher demo this week, use `virtual-gateway/` — see its [README](./virtual-gateway/README.md). One `npm start`, opens in a browser, looks like a control panel, talks to the app over Supabase exactly the way the ESP32 would.**

> **Tinkercad cannot make network calls** — its sandboxed Arduino has no WiFi. To get a live, bidirectional connection between the simulated circuit and the app for a teacher demo, use the **ESP32 + Wokwi** setup below. Wokwi is a free in-browser simulator that ships with virtual WiFi (`Wokwi-GUEST`) and runs the same kind of buzzer / LED / servo / pots circuit.

---

## Option A: ESP32 on Wokwi (live, bidirectional)

This is the demo path. The simulated ESP32 talks to your Supabase project directly using the same RPCs (`submit_sensor_reading_device`, `get_zone_state_device`, `reset_zone_valve`) that the mobile app and Raspberry Pi use. **No bridge process or PC required.**

What the teacher will see:

1. You twist a moisture pot in Wokwi -> red LED + buzzer fire on the simulated board AND the app's Live / Monitor tab updates within a couple of seconds. Valve servo closes, alert email goes out.
2. You tap **Reset** in the app -> the buzzer in Wokwi goes silent and the servo swings back to OPEN. Bidirectional.

### One-time Supabase setup

1. Apply migrations (one new file added: `supabase/migrations/20250516120000_get_zone_state_device.sql` — exposes a small read-only RPC the ESP32 can call with its device secret).
   ```bash
   cd IOT-System
   supabase db push                # or supabase migration up
   ```
2. In the app, **sign in** with your normal account, **create a Device** (mode: `physical`) and at least one **Zone**. Note these from Supabase Studio:
   - `public.zones.id` (the zone the ESP32 will report on)
   - `public.devices.device_secret` (per-device UUID; treat as a password)

### Wokwi setup (web simulator — easiest)

1. Open https://wokwi.com -> **New Project** -> **ESP32**.
2. In the editor, switch to the `diagram.json` tab and paste the contents of `aquaguard_esp32_wokwi/diagram.json` (replaces the default circuit with our 4 pots + LEDs + buzzer + button + servo).
3. Switch to the `sketch.ino` tab and paste `aquaguard_esp32_wokwi/aquaguard_esp32_wokwi.ino`.
4. Open the **Library Manager** (the books icon) and add the libraries from `libraries.txt`:
   - `ArduinoJson` (>= 7.0.4)
   - `ESP32Servo` (>= 1.1.2)
5. Add a new file named `secrets.h` (use the **+** button next to file tabs). Copy the contents of `secrets.h.example` into it and fill in:
   - `SUPABASE_URL` and `SUPABASE_ANON_KEY` — from Supabase Dashboard -> Project Settings -> API. Same values as the mobile app's `.env` (`EXPO_PUBLIC_SUPABASE_URL`, `EXPO_PUBLIC_SUPABASE_ANON_KEY`).
   - `ZONE_ID` — `public.zones.id` of the zone you want this circuit bound to.
   - `DEVICE_SECRET` — `public.devices.device_secret` for that zone's device.
   - Leave `WIFI_SSID = "Wokwi-GUEST"` and `WIFI_PASSWORD = ""` so Wokwi's free virtual WiFi connects automatically.
6. Press the green **Play** button. The Serial Monitor (115200 baud) should print:
   ```
   AQUAGUARD ESP32 booting...
   [wifi] connecting to Wokwi-GUEST ......
   [wifi] OK ip=10.13.37.x rssi=-50
   AQUAGUARD READY
   65
   MOISTURE:65|THRESHOLD:70(cloud)|VALVE:OPEN|ALARM:OFF|K:32|B:65|S:18|WIFI:ok
   ```
   Anything different? See **Troubleshooting** at the bottom.

### Demo script (the part the teacher actually sees)

1. Open Wokwi simulation full-screen on one half of the laptop, the **app -> Live tab** on the other half.
2. Twist the **THRESHOLD** pot (bottom left in the diagram) so the cloud's threshold matches what's shown in the Monitor tab — or leave it; the firmware honours the cloud threshold automatically.
3. Twist any of the three **moisture** pots up to ~95%. After ~2 seconds:
   - Red alarm LED lights, buzzer sounds, green valve LED turns off, servo swings to 0 deg (closed).
   - The app's Live tab shows **LEAK** and the email alert fires.
4. In the app, tap **Reset valve**. Within ~1.5 s the buzzer in Wokwi goes silent and the servo returns to 90 deg.
5. (Optional, for the "remote control" angle) From the app's Live tab, send a high moisture value yourself — the cloud closes the valve, and Wokwi's buzzer fires from the **app side** without touching the pots.

### Real ESP32 (USB)

The same sketch and `secrets.h` flash to a real ESP32 unchanged. After uploading, the board talks straight to Supabase over your home WiFi — no bridge, no Pi.

### Troubleshooting

- **`[wifi] FAILED`**: in Wokwi, make sure `WIFI_SSID = "Wokwi-GUEST"` and the password is the empty string. On a real ESP32, double-check your AP creds.
- **`[http] submit_sensor_reading_device -> 401 ...`**: `SUPABASE_ANON_KEY` is wrong, or `apikey` header isn't being sent.
- **`[http] ... -> 400 "Invalid device secret"`**: `DEVICE_SECRET` doesn't match `public.devices.device_secret` for the device that owns `ZONE_ID`.
- **`[http] ... -> 404 "Zone not found"`**: `ZONE_ID` is wrong, or you didn't run the migration.
- **`get_zone_state_device` returns 404**: migration not applied — re-run `supabase db push` or paste the SQL from the migration file into the SQL editor.
- **App shows new readings but circuit never reacts to Reset**: check Serial Monitor for `[cloud] reset received` lines. If you see no poll lines at all, WiFi dropped — restart the simulation.

---

## Option B: Tinkercad / Arduino Uno (manual mirror or USB bridge)

Use this path only when you have to stay on Tinkercad (no WiFi simulation) or want to drive a real Uno over USB. The link to the app is **manual or via the local serial bridge**, not direct.

### Components needed

| Component | Qty | Purpose |
|-----------|-----|---------|
| Arduino Uno | 1 | Controller |
| Potentiometer (10k) | 4 | 3 moisture sensors + 1 threshold knob |
| Red LED + 220 ohm resistor | 1 | Alarm indicator |
| Green LED + 220 ohm resistor | 1 | Valve status (ON = open) |
| Piezo buzzer | 1 | Audible alarm |
| Micro servo | 1 | Valve open/close visualizer |
| Pushbutton | 1 | Manual reset after leak |

### Pin wiring

| Arduino Pin | Component | Notes |
|-------------|-----------|-------|
| **A0** | Pot 1 (middle pin) | Kitchen moisture sensor |
| **A1** | Pot 2 (middle pin) | Bathroom moisture sensor |
| **A2** | Pot 3 (middle pin) | Basement moisture sensor |
| **A3** | Pot 4 (middle pin) | **Threshold knob** - turn to set trip point |
| **D2** | Red LED (via 220 ohm) | Alarm - lights when leak confirmed |
| **D3** | Green LED (via 220 ohm) | Valve status - ON = open, OFF = closed |
| **D4** | Piezo buzzer | Sounds while leak is active |
| **D5** | Pushbutton (other leg to GND) | Reset - clears alarm, reopens valve |
| **D9** | Servo signal wire | Valve visualizer - 90 deg = open, 0 deg = closed |
| **5V** | All pot left pins, servo VCC | Power rail |
| **GND** | All pot right pins, servo GND, LED cathodes, buzzer GND, button GND | Ground rail |

### Changes from the old circuit

1. **Add a 4th potentiometer** to **A3** - this is the threshold knob. Turn it to change the leak trip point (0-100%) live during simulation. No more hardcoded threshold.
2. **Add a micro servo** to **D9** - this physically shows the valve opening/closing (90 deg = open, sweeps to 0 = closed on leak). Drag "Micro Servo" from the Tinkercad component panel.
3. **Add a pushbutton** between **D5** and **GND** - press it to reset the alarm and reopen the valve after a leak event. The code uses INPUT_PULLUP so no external resistor is needed.

### How the dynamic code works

- **Threshold is live**: turn pot A3 and the trip point changes immediately. The Serial Monitor shows the current threshold so you can match it in the app's Monitor tab.
- **Leak confirmation**: when any sensor pot exceeds the threshold, a 2-second confirmation timer starts. If it stays above for 2s, the leak is confirmed - alarm LED lights, buzzer sounds, servo closes, green valve LED turns off.
- **Reset**: press the pushbutton on D5 to clear the alarm, silence the buzzer, and reopen the valve (servo goes back to 90 deg).
- **Serial output** (9600 baud, every 500ms):
  - Line 1: plain number `0-100` (max moisture %) - **this is what you type into the app**
  - Line 2: detailed `MOISTURE:72|THRESHOLD:65|VALVE:OPEN|ALARM:OFF|K:45|B:72|S:30`

### Connecting Tinkercad to the App (manual mirror)

No external server or bridge needed. Everything happens inside the app.

#### Step by step

1. **Tinkercad**: Open your circuit, paste the code from `aquaguard_iot.ino`, click **Start Simulation**
2. **Adjust pots**: Turn the sensor pots (A0-A2) to simulate different moisture levels. Turn the threshold pot (A3) to set the trip point.
3. **Read Serial Monitor**: Open Serial Monitor in Tinkercad. You'll see lines like:
   ```
   72
   MOISTURE:72|THRESHOLD:65|VALVE:OPEN|ALARM:OFF|K:45|B:72|S:30
   ```
4. **App - Monitor tab**: Set the threshold slider to match the threshold % shown in Serial Monitor, then Save
5. **App - Live tab**: Type the moisture number (e.g. `72`) into the control panel, tap **Send**. Or drag the slider and tap **Send to cloud**.
6. **Result**: Both Tinkercad and the app now show the same behavior - if moisture exceeds threshold, both close the valve and trigger the alarm. The app also sends a leak alert email if configured.

#### Presets in the app

The Live tab has quick presets: Dry (15%), Normal (40%), Damp (65%), Wet (80%), Leak (95%). Tap any to instantly send that value.

#### Auto-repeat mode

Turn on auto-repeat in the Live tab to keep sending the current slider value every few seconds - useful to simulate a continuous sensor feed while you watch the valve/alarm react.

### Real Arduino Uno over USB

For a physical Arduino Uno instead of Tinkercad:

1. Wire the circuit as above
2. Upload the sketch via Arduino IDE
3. The serial bridge (`firmware/bridge/serial-bridge.mjs`) can read the COM port automatically:
   ```powershell
   cd firmware/bridge
   npm install
   $env:SERIAL_PORT="COM3"   # your Arduino's port from Device Manager
   npm start
   ```
4. Or just read the Serial Monitor value and type it into the app's Live tab - same as Tinkercad.
