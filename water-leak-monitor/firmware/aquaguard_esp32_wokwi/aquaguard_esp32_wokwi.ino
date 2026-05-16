/**
 * AQUAGUARD IoT — ESP32 (Wokwi) <-> Supabase live link
 *
 * Drop-in replacement for the Tinkercad Uno sketch. Same circuit idea
 * (3 moisture pots + 1 threshold pot + LEDs + buzzer + servo + reset
 * button) but on an ESP32 with real WiFi. The board talks straight to
 * Supabase using the same RPCs the mobile app uses, so the app and
 * the simulated circuit stay in sync without any bridge process.
 *
 * Bidirectional flow (no bridge / no PC required):
 *   • ESP32 -> app: every ~2s POSTs the max moisture % to RPC
 *     `submit_sensor_reading_device`. Cloud writes a sensor_reading,
 *     and if moisture >= zone threshold it auto-closes the valve and
 *     creates a leak_event. The app's Live / Monitor tabs update via
 *     Supabase Realtime.
 *   • app -> ESP32: every ~1.5s the ESP32 polls RPC
 *     `get_zone_state_device` (anon + device secret). When the user
 *     taps "Reset" in the app, zone.valve_open flips back to true and
 *     the ESP32 silences the buzzer / re-opens the servo. If anything
 *     in the cloud closes the valve (forced leak from app, threshold
 *     change, etc.) the buzzer fires here too.
 *
 * Local logic (so the demo still reacts the moment a pot is moved,
 * even before the cloud round-trip):
 *   • Three moisture pots A0..A2, threshold pot A3.
 *   • If max(K,B,S) >= threshold for 2s -> alarm latched, valve closes.
 *   • Reset button (D5) -> clear alarm + re-open valve locally.
 *
 * Wokwi:
 *   1) Open https://wokwi.com  -> "New Project" -> ESP32.
 *   2) Replace the editor's diagram.json with the one in this folder.
 *   3) Paste this .ino as the sketch.
 *   4) Add libraries: ArduinoJson, ESP32Servo (see libraries.txt).
 *   5) Copy secrets.h.example to secrets.h, fill in your values
 *      (WiFi: leave Wokwi-GUEST/empty for Wokwi's free WiFi).
 *   6) Press the green Play button.
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

#include "secrets.h"

// ─── Pin map (ESP32 DevKit V1 / V4) ─────────────────────────────────
//
// ADC1 input-only pins: 32-39. We use 36 (VP), 39 (VN), 34, 35.
// IMPORTANT on real ESP32: ADC2 pins (0,2,4,12-15,25-27) are unusable
// while WiFi is active — keep moisture sensors on ADC1 only.

const int S_KITCHEN  = 36;   // VP
const int S_BATHROOM = 39;   // VN
const int S_BASEMENT = 34;
const int S_THRESH   = 35;

const int ALARM_LED  = 18;   // Red  — leak confirmed
const int VALVE_LED  = 19;   // Green — ON when valve is open
const int BUZZER_PIN = 23;   // Active piezo
const int RESET_BTN  = 5;    // To GND, INPUT_PULLUP
const int SERVO_PIN  = 13;   // Valve position (0 = closed, 90 = open)

// ─── Timing ────────────────────────────────────────────────────────

const unsigned long CONFIRM_MS      = 2000;  // local leak debounce
const unsigned long DEBOUNCE_MS     = 200;
const unsigned long SERIAL_INTERVAL = 1000;
const unsigned long POST_INTERVAL   = 2000;  // cloud upload cadence
const unsigned long POLL_INTERVAL   = 1500;  // cloud state poll cadence

// ─── State ─────────────────────────────────────────────────────────

Servo valveServo;
WiFiClientSecure tlsClient;

bool valveOpen   = true;
bool alarmOn     = false;
bool leakLatched = false;

unsigned long leakStartMs    = 0;
unsigned long lastSerialMs   = 0;
unsigned long lastResetMs    = 0;
unsigned long lastPostMs     = 0;
unsigned long lastPollMs     = 0;

int  cloudThreshold   = -1;   // -1 = unknown, fall back to local pot
int  lastPostedPct    = -1;
bool wifiUp           = false;

// ─── Helpers ───────────────────────────────────────────────────────

int adcToPercent(int raw) {
  // ESP32 ADC is 12-bit (0-4095) by default
  return constrain(map(raw, 0, 4095, 0, 100), 0, 100);
}

void setValve(bool open) {
  valveOpen = open;
  digitalWrite(VALVE_LED, open ? HIGH : LOW);
  valveServo.write(open ? 90 : 0);
}

void triggerAlarm() {
  alarmOn     = true;
  leakLatched = true;
  setValve(false);
}

void clearAlarm() {
  alarmOn     = false;
  leakLatched = false;
  leakStartMs = 0;
  digitalWrite(ALARM_LED, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  setValve(true);
}

// ─── WiFi ──────────────────────────────────────────────────────────

void connectWifi() {
  Serial.printf("[wifi] connecting to %s ", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    wifiUp = true;
    Serial.printf("\n[wifi] OK ip=%s rssi=%d\n",
                  WiFi.localIP().toString().c_str(),
                  WiFi.RSSI());
  } else {
    wifiUp = false;
    Serial.println("\n[wifi] FAILED — running in local-only mode");
  }
  // Demo only: skip TLS cert validation. For production, pin the
  // Supabase root cert (Let's Encrypt ISRG) in setCACert(...).
  tlsClient.setInsecure();
}

// ─── Supabase REST ─────────────────────────────────────────────────

bool callRpc(const char *fnName, const String &body, String &outResp) {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  String url = String(SUPABASE_URL) + "/rest/v1/rpc/" + fnName;
  if (!http.begin(tlsClient, url)) {
    Serial.println("[http] begin() failed");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("apikey", SUPABASE_ANON_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);

  int code = http.POST(body);
  outResp = http.getString();
  http.end();

  if (code != 200) {
    Serial.printf("[http] %s -> %d %s\n", fnName, code, outResp.c_str());
    return false;
  }
  return true;
}

void postReading(int maxPct) {
  String body = String("{\"p_zone_id\":\"") + ZONE_ID +
                "\",\"p_moisture\":" + maxPct +
                ",\"p_device_secret\":\"" + DEVICE_SECRET + "\"}";

  String resp;
  if (!callRpc("submit_sensor_reading_device", body, resp)) return;

  JsonDocument doc;
  if (deserializeJson(doc, resp)) return;

  if (doc["leak_detected"].as<bool>()) {
    int rt = doc["response_ms"].as<int>();
    Serial.printf("[cloud] LEAK ack response_ms=%d\n", rt);
  }
}

void pollState() {
  String body = String("{\"p_zone_id\":\"") + ZONE_ID +
                "\",\"p_device_secret\":\"" + DEVICE_SECRET + "\"}";

  String resp;
  if (!callRpc("get_zone_state_device", body, resp)) return;

  JsonDocument doc;
  if (deserializeJson(doc, resp)) return;

  bool cloudValveOpen = doc["valve_open"].as<bool>();
  int  thr            = doc["threshold"].as<int>();
  if (thr >= 0 && thr <= 100) cloudThreshold = thr;

  // App reset -> reopen here
  if (cloudValveOpen && leakLatched) {
    Serial.println("[cloud] reset received — clearing alarm");
    clearAlarm();
  }
  // Cloud (or app forcing a leak) closed the valve -> sound buzzer here
  if (!cloudValveOpen && !leakLatched) {
    Serial.println("[cloud] valve closed remotely — triggering alarm");
    triggerAlarm();
  }
}

// ─── Setup / loop ──────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println();
  Serial.println("AQUAGUARD ESP32 booting…");

  pinMode(ALARM_LED, OUTPUT);
  pinMode(VALVE_LED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(RESET_BTN, INPUT_PULLUP);

  ESP32PWM::allocateTimer(0);
  valveServo.setPeriodHertz(50);
  valveServo.attach(SERVO_PIN, 500, 2400);

  digitalWrite(ALARM_LED, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  setValve(true);

  connectWifi();
  Serial.println("AQUAGUARD READY");
}

void loop() {
  unsigned long now = millis();

  // --- Sensors ---
  int rawK = analogRead(S_KITCHEN);
  int rawB = analogRead(S_BATHROOM);
  int rawS = analogRead(S_BASEMENT);
  int rawT = analogRead(S_THRESH);

  int pctK = adcToPercent(rawK);
  int pctB = adcToPercent(rawB);
  int pctS = adcToPercent(rawS);
  int localThr = adcToPercent(rawT);

  int maxPct   = max(pctK, max(pctB, pctS));
  int activeThr = (cloudThreshold >= 0) ? cloudThreshold : localThr;
  bool wet      = maxPct >= activeThr;

  // --- Reset button (instant local override) ---
  if (digitalRead(RESET_BTN) == LOW && (now - lastResetMs > DEBOUNCE_MS)) {
    lastResetMs = now;
    clearAlarm();
  }

  // --- Local leak detection with confirmation delay ---
  if (wet && !leakLatched) {
    if (leakStartMs == 0) {
      leakStartMs = now;
    } else if (now - leakStartMs >= CONFIRM_MS) {
      triggerAlarm();
    }
  }
  if (!wet && !leakLatched) {
    leakStartMs = 0;
  }

  // --- Drive outputs ---
  digitalWrite(ALARM_LED, alarmOn ? HIGH : LOW);
  digitalWrite(BUZZER_PIN, alarmOn ? HIGH : LOW);

  // --- Cloud upload (rate-limited; only when value changes too) ---
  if (now - lastPostMs >= POST_INTERVAL) {
    lastPostMs = now;
    if (maxPct != lastPostedPct) {
      lastPostedPct = maxPct;
      postReading(maxPct);
    }
  }

  // --- Cloud state poll (so app actions reach the circuit) ---
  if (now - lastPollMs >= POLL_INTERVAL) {
    lastPollMs = now;
    pollState();
  }

  // --- Serial log ---
  if (now - lastSerialMs >= SERIAL_INTERVAL) {
    lastSerialMs = now;
    Serial.println(maxPct);
    Serial.printf(
      "MOISTURE:%d|THRESHOLD:%d(%s)|VALVE:%s|ALARM:%s|K:%d|B:%d|S:%d|WIFI:%s\n",
      maxPct, activeThr, (cloudThreshold >= 0) ? "cloud" : "local",
      valveOpen ? "OPEN" : "CLOSED",
      alarmOn ? "ON" : "OFF",
      pctK, pctB, pctS,
      (WiFi.status() == WL_CONNECTED) ? "ok" : "down");
  }
}
