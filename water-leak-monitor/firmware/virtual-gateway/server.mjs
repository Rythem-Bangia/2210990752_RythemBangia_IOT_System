/**
 * Virtual Gateway server.
 *
 * Serves index.html and (optionally) injects values from the project's
 * `.env` so the page can boot already configured. Zero npm dependencies.
 *
 * Usage:
 *   cd water-leak-monitor/firmware/virtual-gateway
 *   npm start
 * Then open http://localhost:4000
 *
 * Config priority for the `/config` endpoint, in order:
 *   1) env vars on process (SUPABASE_URL, SUPABASE_ANON_KEY, ZONE_ID,
 *      DEVICE_SECRET).
 *   2) `.env` at the IOT-System repo root, if present:
 *        SUPABASE_URL or EXPO_PUBLIC_SUPABASE_URL
 *        SUPABASE_ANON_KEY or EXPO_PUBLIC_SUPABASE_ANON_KEY
 *        ZONE_ID
 *        DEVICE_SECRET
 *   3) Empty (the page will pop the manual config dialog).
 *
 * If no .env values are found, the page still works perfectly — you just
 * fill in the four fields once, and they're saved to localStorage.
 */

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);
const PORT = Number(process.env.PORT || 4000);

// Walk up to find IOT-System/.env
function findRepoEnv(start) {
  let dir = start;
  for (let i = 0; i < 8; i++) {
    const candidate = path.join(dir, ".env");
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

function parseEnv(file) {
  if (!file) return {};
  try {
    const txt = fs.readFileSync(file, "utf8");
    const out = {};
    for (const raw of txt.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const i = line.indexOf("=");
      if (i < 0) continue;
      const k = line.slice(0, i).trim();
      let v = line.slice(i + 1).trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
        v = v.slice(1, -1);
      }
      out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

function getConfig() {
  const envFile = findRepoEnv(__dirname);
  const fileEnv = parseEnv(envFile);

  const url =
    process.env.SUPABASE_URL ||
    process.env.EXPO_PUBLIC_SUPABASE_URL ||
    fileEnv.SUPABASE_URL ||
    fileEnv.EXPO_PUBLIC_SUPABASE_URL ||
    "";
  const key =
    process.env.SUPABASE_ANON_KEY ||
    process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ||
    fileEnv.SUPABASE_ANON_KEY ||
    fileEnv.EXPO_PUBLIC_SUPABASE_ANON_KEY ||
    "";
  const zone   = process.env.ZONE_ID       || fileEnv.ZONE_ID       || "";
  const secret = process.env.DEVICE_SECRET || fileEnv.DEVICE_SECRET || "";

  return { url, key, zone, secret, env_file: envFile || null };
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js":   "application/javascript; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".svg":  "image/svg+xml",
  ".png":  "image/png",
  ".ico":  "image/x-icon",
  ".json": "application/json; charset=utf-8",
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, {
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
    ...headers,
  });
  res.end(body);
}

function serveStatic(req, res) {
  const url = new URL(req.url, "http://localhost");
  let p = decodeURIComponent(url.pathname);
  if (p === "/") p = "/index.html";
  const safe = path.normalize(p).replace(/^([/\\])+/, "");
  const file = path.join(__dirname, safe);
  if (!file.startsWith(__dirname)) return send(res, 403, "forbidden");
  fs.readFile(file, (err, buf) => {
    if (err) return send(res, 404, "not found");
    const type = MIME[path.extname(file)] || "application/octet-stream";
    send(res, 200, buf, { "Content-Type": type });
  });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");

  if (url.pathname === "/config") {
    const cfg = getConfig();
    return send(
      res,
      200,
      JSON.stringify({
        url: cfg.url,
        key: cfg.key,
        zone: cfg.zone,
        secret: cfg.secret,
      }),
      { "Content-Type": MIME[".json"] },
    );
  }

  if (url.pathname === "/health") {
    return send(res, 200, JSON.stringify({ ok: true }), { "Content-Type": MIME[".json"] });
  }

  return serveStatic(req, res);
});

server.listen(PORT, "127.0.0.1", () => {
  const cfg = getConfig();
  console.log(`AQUAGUARD virtual gateway → http://localhost:${PORT}`);
  if (cfg.env_file) {
    console.log(`  env file: ${cfg.env_file}`);
  } else {
    console.log("  env file: not found — fill the config dialog in the page");
  }
  console.log(`  cloud:    ${cfg.url ? "auto-loaded" : "needs manual entry"}`);
  console.log(`  zone:     ${cfg.zone ? cfg.zone.slice(0, 8) + "…" : "needs manual entry"}`);
});
