"""
Pi pairing flow — bind this device to a zone from inside the Expo app.

When the script starts and no zone/device-secret is configured, we:
  1. Generate a 6-digit code, print a banner, and call register_pi_pairing(code).
  2. Poll read_pi_pairing(code) every 3 seconds.
  3. Once the user claims it in the app, we save {zone_id, device_secret}
     to ~/.aquaguard-pi.json and the main loop continues normally.

Only stdlib + the SupabaseEdge client are used.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get(
    "AQUAGUARD_PI_CONFIG",
    str(Path.home() / ".aquaguard-pi.json"),
))


def load_saved() -> dict[str, str]:
    """Return saved {zone_id, device_secret} or {} if none/invalid."""
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
            if isinstance(data, dict) and data.get("zone_id") and data.get("device_secret"):
                return {"zone_id": data["zone_id"], "device_secret": data["device_secret"]}
    except Exception:
        pass
    return {}


def save(zone_id: str, device_secret: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({
        "zone_id": zone_id,
        "device_secret": device_secret,
    }, indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


def clear() -> None:
    try:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
    except Exception:
        pass


def _gen_code() -> str:
    """6-digit numeric code, easy to type on mobile."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _print_banner(code: str) -> None:
    pretty = f"{code[:3]} {code[3:]}"
    bar = "═" * 56
    print()
    print(f"╔{bar}╗")
    print("║" + "  PAIR THIS RASPBERRY PI WITH THE APP".ljust(56) + "║")
    print("║" + " " * 56 + "║")
    print("║" + f"      Pairing code:   {pretty}".ljust(56) + "║")
    print("║" + " " * 56 + "║")
    print("║" + "  In the Expo app:".ljust(56) + "║")
    print("║" + "    Lab → Raspberry Pi → tap “Pair this Pi”".ljust(56) + "║")
    print("║" + "    Enter the code above and choose a zone.".ljust(56) + "║")
    print(f"╚{bar}╝")
    print()
    sys.stdout.flush()


def _register(cloud, code: str) -> bool:
    try:
        cloud.register_pi_pairing(code)
        return True
    except Exception as e:
        print(f"[pair] register failed: {e}")
        return False


def _read(cloud, code: str) -> dict[str, Any] | None:
    try:
        result = cloud.read_pi_pairing(code)
        if isinstance(result, dict):
            return result
    except Exception as e:
        print(f"[pair] read failed: {e}")
    return None


def pair_interactive(cloud, poll_seconds: float = 3.0, refresh_seconds: float = 60.0) -> dict[str, str] | None:
    """
    Run the pairing loop. Returns {'zone_id', 'device_secret'} on success,
    or None if interrupted / cloud unreachable for too long.
    """
    code = _gen_code()
    if not _register(cloud, code):
        return None

    _print_banner(code)
    print("[pair] Waiting for the app to claim this code… (Ctrl+C to cancel)")

    last_refresh = time.time()
    waited = 0
    try:
        while True:
            time.sleep(poll_seconds)
            waited += poll_seconds

            # Re-register every ~minute so the row never expires while we wait.
            if time.time() - last_refresh > refresh_seconds:
                _register(cloud, code)
                last_refresh = time.time()

            r = _read(cloud, code)
            if r is None:
                continue
            if r.get("claimed"):
                zone = r.get("zone_id")
                secret = r.get("device_secret")
                if zone and secret:
                    save(zone, secret)
                    print(f"[pair] Paired! zone={zone[:8]}… (saved to {CONFIG_PATH})")
                    return {"zone_id": zone, "device_secret": secret}
                print("[pair] claimed but missing fields — retrying with a new code")
                code = _gen_code()
                _register(cloud, code)
                _print_banner(code)
            else:
                if int(waited) % 30 == 0:
                    print(f"[pair] still waiting… (open the app and enter {code[:3]} {code[3:]})")
    except KeyboardInterrupt:
        print("\n[pair] Pairing cancelled.")
        return None
