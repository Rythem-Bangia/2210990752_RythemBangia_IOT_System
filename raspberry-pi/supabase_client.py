"""
Supabase client for the Raspberry Pi.

Authenticates with email/password, then uses the same RPCs and Edge
Functions as the Expo app — submit_sensor_reading, ai-hub, etc.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Any

import config


class SupabaseEdge:
    """
    Lightweight Supabase client using only urllib (no heavy SDK).
    Handles auth, RPC calls, and Edge Function invocations.
    """

    def __init__(self):
        self._url = config.SUPABASE_URL.rstrip("/")
        self._anon_key = config.SUPABASE_ANON_KEY
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0

    # ─── Auth ────────────────────────────────────────────────────────

    def sign_in(self) -> bool:
        """Sign in with email/password. Returns True on success."""
        if not config.SUPABASE_EMAIL or not config.SUPABASE_PASSWORD:
            print("[supabase] No SUPABASE_EMAIL/PASSWORD set — running without auth")
            return False

        try:
            body = json.dumps({
                "email": config.SUPABASE_EMAIL,
                "password": config.SUPABASE_PASSWORD,
            }).encode()
            req = urllib.request.Request(
                f"{self._url}/auth/v1/token?grant_type=password",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "apikey": self._anon_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            self._access_token = data["access_token"]
            self._refresh_token = data.get("refresh_token")
            self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
            print(f"[supabase] Signed in as {config.SUPABASE_EMAIL}")
            return True
        except Exception as e:
            print(f"[supabase] Sign-in failed: {e}")
            return False

    def has_user_session(self) -> bool:
        """True when signed in with email/password (JWT). Needed for ai-hub."""
        return bool(self._access_token)

    def _ensure_token(self):
        """Refresh the JWT if it's about to expire."""
        if not self._access_token:
            self.sign_in()
            return
        if time.time() < self._token_expires_at:
            return
        if not self._refresh_token:
            self.sign_in()
            return
        try:
            body = json.dumps({"refresh_token": self._refresh_token}).encode()
            req = urllib.request.Request(
                f"{self._url}/auth/v1/token?grant_type=refresh_token",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "apikey": self._anon_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            self._access_token = data["access_token"]
            self._refresh_token = data.get("refresh_token", self._refresh_token)
            self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
            print("[supabase] Token refreshed")
        except Exception as e:
            print(f"[supabase] Token refresh failed, re-signing in: {e}")
            self.sign_in()

    def _auth_headers(self) -> dict[str, str]:
        self._ensure_token()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "apikey": self._anon_key,
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _anon_headers(self) -> dict[str, str]:
        """PostgREST as anon role (for submit_sensor_reading_device)."""
        return {
            "Content-Type": "application/json",
            "apikey": self._anon_key,
            "Authorization": f"Bearer {self._anon_key}",
        }

    # ─── PostgREST RPC ───────────────────────────────────────────────

    def rpc(self, function_name: str, params: dict[str, Any]) -> Any:
        """Call a Supabase RPC function (e.g. submit_sensor_reading)."""
        body = json.dumps(params).encode()
        req = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/{function_name}",
            data=body,
            headers=self._auth_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            raise RuntimeError(f"RPC {function_name} failed ({e.code}): {error_body}")

    # ─── Submit sensor reading ───────────────────────────────────────

    def submit_reading(self, zone_id: str, moisture: float) -> dict:
        """
        Upload a physical reading.
        With a user session (email login): submit_sensor_reading (JWT).
        Without session: submit_sensor_reading_device (anon + DEVICE_SECRET).
        """
        if not self.has_user_session() and config.DEVICE_SECRET and config.ZONE_ID:
            body = json.dumps({
                "p_zone_id": zone_id,
                "p_moisture": round(moisture, 1),
                "p_device_secret": config.DEVICE_SECRET,
            }).encode()
            req = urllib.request.Request(
                f"{self._url}/rest/v1/rpc/submit_sensor_reading_device",
                data=body,
                headers=self._anon_headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    out = json.loads(resp.read())
                return out if isinstance(out, dict) else {}
            except urllib.error.HTTPError as e:
                error_body = e.read().decode() if e.fp else str(e)
                raise RuntimeError(
                    f"RPC submit_sensor_reading_device failed ({e.code}): {error_body}",
                )

        result = self.rpc("submit_sensor_reading", {
            "p_zone_id": zone_id,
            "p_moisture": round(moisture, 1),
            "p_source": "physical",
        })
        return result if isinstance(result, dict) else {}

    # ─── Fetch zones ─────────────────────────────────────────────────

    def fetch_zones(self) -> list[dict]:
        """Get all zones with their current threshold and valve state."""
        req = urllib.request.Request(
            f"{self._url}/rest/v1/zones?select=id,name,moisture_threshold,last_moisture,valve_open,valve_closed_at,devices!inner(id,mode,name)",
            headers=self._auth_headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"[supabase] fetch_zones failed: {e}")
            return []

    # ─── Edge Function: ai-hub ───────────────────────────────────────

    def invoke_ai_hub(self, action: str, payload: dict[str, Any] | None = None) -> dict:
        """
        Call the ai-hub Edge Function.
        Returns the parsed JSON response.
        """
        body_dict = {"action": action}
        if payload:
            body_dict.update(payload)
        body = json.dumps(body_dict).encode()
        req = urllib.request.Request(
            f"{self._url}/functions/v1/ai-hub",
            data=body,
            headers=self._auth_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            print(f"[supabase] ai-hub '{action}' failed ({e.code}): {error_body[:200]}")
            return {"error": error_body[:200]}

    # ─── Edge Function: send-leak-alert ──────────────────────────────

    def send_leak_alert(self, leak_event_id: str, device_secrets: list[str]) -> dict:
        """Trigger the send-leak-alert Edge Function."""
        body = json.dumps({
            "leak_event_id": leak_event_id,
            "device_secret": device_secrets,
        }).encode()
        headers = self._auth_headers()
        headers["Authorization"] = f"Bearer {self._anon_key}"
        req = urllib.request.Request(
            f"{self._url}/functions/v1/send-leak-alert",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            print(f"[supabase] send-leak-alert failed ({e.code}): {error_body[:200]}")
            return {"error": error_body[:200]}

    # ─── Reset valve RPC ─────────────────────────────────────────────

    def reset_valve(self, zone_id: str):
        """Reopen the valve in the cloud."""
        return self.rpc("reset_zone_valve", {"p_zone_id": zone_id})

    # ─── Pi pairing RPCs (anon-safe) ─────────────────────────────────

    def _anon_rpc(self, function_name: str, params: dict[str, Any]) -> Any:
        """Call an RPC as the anon role (no user JWT required)."""
        body = json.dumps(params).encode()
        req = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/{function_name}",
            data=body,
            headers=self._anon_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            raise RuntimeError(f"RPC {function_name} failed ({e.code}): {error_body}")

    def register_pi_pairing(self, code: str):
        """Register/refresh a 6-digit pairing code so the app can claim it."""
        return self._anon_rpc("register_pi_pairing", {"p_code": code})

    def read_pi_pairing(self, code: str) -> dict:
        """Poll for a claimed pairing. Returns {claimed, zone_id?, device_secret?}."""
        out = self._anon_rpc("read_pi_pairing", {"p_code": code})
        return out if isinstance(out, dict) else {"claimed": False}
