#!/usr/bin/env python3
# Run with: python3 main.py
"""
Raspberry Pi Edge AI — Water Leak Monitor

Main loop:
  1. Read moisture sensor
  2. Run local AI (anomaly, trend, risk)
  3. If above threshold → close valve, trigger alarm
  4. Send reading to Supabase (source: "physical")
  5. If leak detected → send email alert + request cloud AI analysis

Works in mock mode on any machine (Mac/Linux/Windows) for testing.
On a real Pi, connect a moisture sensor, relay, buzzer, and LED.

Usage:
  python main.py              # mock mode (default)
  SENSOR_DRIVER=ADS1115 python main.py   # real sensor

Environment:
  See config.py for all options, or set SUPABASE_EMAIL / SUPABASE_PASSWORD
  to authenticate with your Supabase project.
"""

from __future__ import annotations

import signal
import sys
import time
import traceback

import config
import pairing
from actuators import Buzzer, StatusLED, Valve, cleanup as gpio_cleanup
from local_ai import LocalAI
from sensor import create_sensor
from supabase_client import SupabaseEdge


# ─── Globals ─────────────────────────────────────────────────────────

running = True


def handle_signal(_sig, _frame):
    global running
    print("\n[main] Shutting down…")
    running = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


# ─── Helpers ─────────────────────────────────────────────────────────

def print_insight(insight):
    """Pretty-print a local AI insight to the console."""
    risk_colors = {
        "low": "\033[92m",       # green
        "medium": "\033[93m",    # yellow
        "high": "\033[91m",      # red
        "critical": "\033[95m",  # magenta
    }
    reset = "\033[0m"
    color = risk_colors.get(insight.risk_label, "")

    print(f"  Moisture: {insight.moisture:.1f}%")
    print(f"  Trend:    {insight.trend_direction} ({insight.trend_slope:+.2f}%/min)")
    print(f"  Anomaly:  {'YES' if insight.is_anomaly else 'no'} (z={insight.anomaly_score})")
    if insight.is_stuck:
        print(f"  Stuck:    YES ({insight.stuck_count} identical readings)")
    print(f"  Risk:     {color}{insight.risk_label.upper()} ({insight.risk_score}/100){reset}")


# ─── Main loop ───────────────────────────────────────────────────────

def main():
    global running

    print("=" * 60)
    print("  Raspberry Pi Edge AI — Water Leak Monitor")
    print("=" * 60)
    print(f"  Sensor driver : {config.SENSOR_DRIVER}")
    print(f"  On Pi hardware: {config.ON_PI}")
    print(f"  Valve control : {config.ENABLE_VALVE_CONTROL}")
    print(f"  Alarm enabled : {config.ENABLE_ALARM}")
    print(f"  Cloud AI      : {config.ENABLE_CLOUD_AI}")
    print(f"  Poll interval : {config.POLL_INTERVAL_S}s")
    print("=" * 60)

    # Initialize components
    sensor = create_sensor()
    ai = LocalAI()
    valve = Valve()
    buzzer = Buzzer()
    led = StatusLED()
    cloud = SupabaseEdge()

    device_cloud = bool(config.DEVICE_SECRET and config.ZONE_ID)
    email_ok = cloud.sign_in()

    # If env vars aren't set, fall back to the saved pairing config
    # (~/.aquaguard-pi.json), and if that's missing too, run the
    # interactive pairing flow so the user can claim this Pi from the app.
    saved = {} if device_cloud else pairing.load_saved()
    if not device_cloud and not email_ok and not saved:
        print("[main] No credentials found — starting pairing flow.")
        paired = pairing.pair_interactive(cloud)
        if paired:
            saved = paired
        else:
            print("[main] Pairing aborted; running OFFLINE (readings stay local).")

    # Promote saved/paired secret into the global config so submit_reading
    # picks it up via the existing device-secret path.
    if saved:
        config.ZONE_ID = saved["zone_id"]
        config.DEVICE_SECRET = saved["device_secret"]
        device_cloud = True

    cloud_sync = email_ok or device_cloud
    if not cloud_sync:
        print("[main] WARNING: No cloud credentials — readings stay local only (app shows OFFLINE)")
        print("[main]   Tip: re-run the script and enter the printed code in the app's")
        print("[main]   Lab → Raspberry Pi → ‘Pair this Pi’ modal.")

    zone_id: str | None = None
    threshold = config.DEFAULT_MOISTURE_THRESHOLD
    device_secrets: list[str] = []

    if device_cloud:
        zone_id = config.ZONE_ID
        device_secrets = [config.DEVICE_SECRET]
        threshold = config.DEFAULT_MOISTURE_THRESHOLD
        print(f"[main] Cloud: device-secret mode · zone {zone_id[:8]}… · threshold {threshold}%")

    if email_ok:
        try:
            zones = cloud.fetch_zones()
            if zones:
                zone = zones[0]
                zone_id = zone["id"]
                threshold = zone.get("moisture_threshold", threshold)
                devices = zone.get("devices", {})
                if isinstance(devices, dict):
                    sec = devices.get("device_secret", "")
                    if sec:
                        device_secrets = [sec] if sec not in device_secrets else device_secrets
                elif isinstance(devices, list):
                    for d in devices:
                        sec = d.get("device_secret", "")
                        if sec and sec not in device_secrets:
                            device_secrets.append(sec)
                print(f"[main] Zone: {zone.get('name', '?')} (threshold {threshold}%)")
            else:
                print("[main] No zones found — open the Expo app first to create one")
                if not device_cloud:
                    zone_id = None
        except Exception as e:
            print(f"[main] Failed to fetch zones: {e}")
            if not device_cloud:
                zone_id = None

    leak_cooldown_until = 0.0
    cycle = 0

    print("\n[main] Starting sensor loop… (Ctrl+C to stop)\n")

    while running:
        cycle += 1
        try:
            # 1. Read sensor
            moisture = sensor.read_moisture()

            # 2. Local AI analysis
            insight = ai.analyze(moisture, threshold)

            # 3. Console output
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] #{cycle}")
            print_insight(insight)

            above_threshold = moisture >= threshold
            in_cooldown = time.time() < leak_cooldown_until

            # 4. Leak response
            if above_threshold and not in_cooldown:
                print(f"\n  *** LEAK DETECTED — {moisture:.1f}% >= {threshold}% ***\n")

                # Close valve
                valve.close()

                # Trigger alarm
                buzzer.alarm(duration_s=5.0)
                led.set_leak(duration_s=10.0)

                # Cooldown prevents rapid-fire leak events
                leak_cooldown_until = time.time() + 30.0

                # 5. Send to Supabase
                if cloud_sync and zone_id:
                    try:
                        result = cloud.submit_reading(zone_id, moisture)
                        print(f"  Cloud: {result}")

                        leak_event_id = result.get("leak_event_id")

                        # 6. Send email alert
                        if leak_event_id and device_secrets:
                            try:
                                email_result = cloud.send_leak_alert(
                                    leak_event_id, device_secrets,
                                )
                                print(f"  Email: {email_result}")
                            except Exception as e:
                                print(f"  Email failed: {e}")

                        # 7. Cloud AI analysis
                        if config.ENABLE_CLOUD_AI and leak_event_id and cloud.has_user_session():
                            try:
                                ai_result = cloud.invoke_ai_hub(
                                    "simulate_analysis",
                                    {
                                        "zone_id": zone_id,
                                        "simulation_result": {
                                            "leak_detected": True,
                                            "valve_closed": True,
                                            "moisture_sent": round(moisture, 1),
                                            "threshold": threshold,
                                            "source": "physical",
                                            "local_ai": {
                                                "risk_score": insight.risk_score,
                                                "risk_label": insight.risk_label,
                                                "trend": insight.trend_direction,
                                                "anomaly": insight.is_anomaly,
                                                "stats": ai.get_stats(),
                                            },
                                        },
                                    },
                                )
                                reply = ai_result.get("reply", "")
                                if reply:
                                    print(f"\n  --- Cloud AI Brief ---")
                                    for line in reply.split("\n"):
                                        print(f"  {line}")
                                    print(f"  ----------------------\n")
                            except Exception as e:
                                print(f"  Cloud AI failed: {e}")
                        elif config.ENABLE_CLOUD_AI and leak_event_id and not cloud.has_user_session():
                            print("  Cloud AI skipped (sign in with email/password for ai-hub)")

                    except Exception as e:
                        print(f"  Cloud submit failed: {e}")

            elif not above_threshold and not valve.is_open:
                # Moisture dropped back below threshold while valve is closed.
                # In a real system you'd want manual reset, but for demo
                # we just note it. Use the app's "Reset valve" to reopen.
                print("  (Moisture below threshold but valve remains closed — reset from app)")

            else:
                # Normal reading — heartbeat every cycle so the app stays ONLINE (< stale window)
                if cloud_sync and zone_id:
                    try:
                        result = cloud.submit_reading(zone_id, moisture)
                        print(f"  Cloud: ok · leak={result.get('leak_detected', False)}")

                        # Refresh threshold from cloud
                        new_thr = result.get("threshold")
                        if new_thr and isinstance(new_thr, (int, float)):
                            if new_thr != threshold:
                                print(f"  Threshold updated: {threshold}% -> {new_thr}%")
                                threshold = int(new_thr)
                    except Exception as e:
                        print(f"  Cloud sync: {e}")

                led.set_normal()

            print()

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[main] Error in cycle {cycle}: {e}")
            traceback.print_exc()

        # Wait for next poll
        try:
            time.sleep(config.POLL_INTERVAL_S)
        except KeyboardInterrupt:
            break

    # ─── Shutdown ────────────────────────────────────────────────────
    print("[main] Cleaning up…")
    buzzer.stop()
    led.off()
    gpio_cleanup()
    print("[main] Goodbye!")


if __name__ == "__main__":
    main()
