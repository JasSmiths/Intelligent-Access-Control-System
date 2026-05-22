#!/usr/bin/env node

import { spawnSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { performance } from "node:perf_hooks";

const DEFAULTS = {
  frontend: "http://localhost:8089",
  backend: "http://localhost:8088",
  duration: 15,
  connections: 80,
  timeout: 10,
  wsClients: 120,
  wsDuration: 15,
  wsEvents: 1000,
  wsPingIntervalMs: 250,
};

const args = parseArgs(process.argv.slice(2));
const options = {
  frontend: args.frontend ?? DEFAULTS.frontend,
  backend: args.backend ?? DEFAULTS.backend,
  duration: numberArg(args.duration, DEFAULTS.duration),
  connections: numberArg(args.connections, DEFAULTS.connections),
  timeout: numberArg(args.timeout, DEFAULTS.timeout),
  wsClients: numberArg(args["ws-clients"], DEFAULTS.wsClients),
  wsDuration: numberArg(args["ws-duration"], DEFAULTS.wsDuration),
  wsEvents: numberArg(args["ws-events"], DEFAULTS.wsEvents),
  wsPingIntervalMs: numberArg(args["ws-ping-interval-ms"], DEFAULTS.wsPingIntervalMs),
  skipToken: Boolean(args["skip-token"]),
  skipWebsocket: Boolean(args["skip-websocket"]),
  output: args.output,
};

const startedAt = new Date();
const token = options.skipToken ? "" : getAdminToken();
const authHeaders = token ? [`Authorization=Bearer ${token}`] : [];
const result = {
  started_at: startedAt.toISOString(),
  options: redactOptions(options),
  token_available: Boolean(token),
  metrics: [],
  http: [],
  websocket: null,
};

await sampleMetrics("start");

const httpTargets = [
  {
    title: "frontend_api_health",
    url: `${options.frontend}/api/v1/health`,
    headers: [],
  },
  {
    title: "backend_api_health_direct",
    url: `${options.backend}/api/v1/health`,
    headers: [],
  },
  {
    title: "frontend_auth_status",
    url: `${options.frontend}/api/v1/auth/status`,
    headers: [],
  },
  {
    title: "frontend_maintenance_status_auth",
    url: `${options.frontend}/api/v1/maintenance/status`,
    headers: authHeaders,
    requiresToken: true,
  },
  {
    title: "frontend_events_auth",
    url: `${options.frontend}/api/v1/events?limit=100`,
    headers: authHeaders,
    requiresToken: true,
  },
  {
    title: "frontend_movements_auth",
    url: `${options.frontend}/api/v1/access/movements?limit=100`,
    headers: authHeaders,
    requiresToken: true,
  },
  {
    title: "frontend_people_auth",
    url: `${options.frontend}/api/v1/people?include_media=false`,
    headers: authHeaders,
    requiresToken: true,
  },
];

for (const target of httpTargets) {
  if (target.requiresToken && !token) {
    result.http.push({ title: target.title, skipped: true, reason: "no admin token available" });
    continue;
  }
  await sampleMetrics(`before_${target.title}`);
  const phase = runAutocannon(target);
  result.http.push(phase);
  printHttpSummary(phase);
  await sampleMetrics(`after_${target.title}`);
}

if (!options.skipWebsocket) {
  if (!token) {
    result.websocket = { skipped: true, reason: "no admin token available" };
  } else {
    await sampleMetrics("before_realtime_websocket");
    result.websocket = await runWebsocketPhase(token);
    printWebsocketSummary(result.websocket);
    await sampleMetrics("after_realtime_websocket");
  }
}

await sampleMetrics("end");
result.finished_at = new Date().toISOString();

const outputPath = options.output ?? defaultOutputPath(startedAt);
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(result, null, 2)}\n`);
console.log(`load test result: ${outputPath}`);

function runAutocannon(target) {
  const args = [
    "--yes",
    "autocannon",
    "-j",
    "--no-progress",
    "--renderStatusCodes",
    "-c",
    String(options.connections),
    "-d",
    String(options.duration),
    "-t",
    String(options.timeout),
    "-T",
    target.title,
  ];
  for (const header of target.headers ?? []) {
    args.push("-H", header);
  }
  args.push(target.url);

  const started = performance.now();
  const completed = spawnSync("npx", args, {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  const elapsedMs = performance.now() - started;
  const parsed = parseJsonFromOutput(completed.stdout);
  return {
    title: target.title,
    url: target.url,
    connections: options.connections,
    duration_seconds: options.duration,
    elapsed_ms: Math.round(elapsedMs),
    exit_code: completed.status,
    error: completed.error?.message,
    stderr: sanitizeAutocannonStderr(completed.stderr),
    summary: summarizeAutocannon(parsed),
    raw: parsed,
  };
}

async function runWebsocketPhase(token) {
  const wsBase = options.frontend.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  const url = `${wsBase}/api/v1/realtime/ws?token=${encodeURIComponent(token)}`;
  const sockets = [];
  const counters = {
    opened: 0,
    failed: 0,
    ready: 0,
    pongs: 0,
    loadEventsReceived: 0,
    parseErrors: 0,
  };
  const pongLatencies = [];
  const pendingPings = new Map();

  await Promise.all(
    Array.from({ length: options.wsClients }, (_, index) =>
      openWebsocket(url, index, sockets, counters, pendingPings, pongLatencies),
    ),
  );

  const started = performance.now();
  const pingTimer = setInterval(() => {
    const now = Date.now();
    for (const [index, socket] of sockets.entries()) {
      if (socket?.readyState !== WebSocket.OPEN) {
        continue;
      }
      const id = `${index}-${now}`;
      pendingPings.set(id, performance.now());
      socket.send(JSON.stringify({ type: "client.ping", payload: { id, at: new Date(now).toISOString() } }));
    }
  }, options.wsPingIntervalMs);

  for (const socket of sockets) {
    if (!socket) {
      continue;
    }
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(String(event.data));
        if (message.type === "connection.ready") {
          counters.ready += 1;
          return;
        }
        if (message.type === "connection.pong") {
          counters.pongs += 1;
          const id = message.payload?.id;
          const sentAt = pendingPings.get(id);
          if (sentAt) {
            pongLatencies.push(performance.now() - sentAt);
            pendingPings.delete(id);
          }
          return;
        }
        if (message.type === "load.test") {
          counters.loadEventsReceived += 1;
        }
      } catch {
        counters.parseErrors += 1;
      }
    });
  }

  const publish = publishRealtimeEvents(options.wsEvents);
  await sleep(options.wsDuration * 1000);
  clearInterval(pingTimer);
  await publish;

  for (const socket of sockets) {
    try {
      socket?.close();
    } catch {
      // Best effort cleanup only.
    }
  }
  await sleep(500);

  return {
    clients_requested: options.wsClients,
    clients_opened: counters.opened,
    clients_failed: counters.failed,
    duration_seconds: options.wsDuration,
    synthetic_events_published: options.wsEvents,
    ready_messages: counters.ready,
    pong_messages: counters.pongs,
    load_events_received: counters.loadEventsReceived,
    expected_fanout_messages: counters.opened * options.wsEvents,
    parse_errors: counters.parseErrors,
    elapsed_ms: Math.round(performance.now() - started),
    pong_latency_ms: summarizeNumbers(pongLatencies),
  };
}

function openWebsocket(url, index, sockets, counters, pendingPings, pongLatencies) {
  return new Promise((resolve) => {
    const socket = new WebSocket(url);
    const timeout = setTimeout(() => {
      counters.failed += 1;
      try {
        socket.close();
      } catch {
        // Best effort cleanup only.
      }
      resolve();
    }, 10000);

    socket.addEventListener("open", () => {
      clearTimeout(timeout);
      counters.opened += 1;
      sockets[index] = socket;
      resolve();
    });
    socket.addEventListener("error", () => {
      clearTimeout(timeout);
      counters.failed += 1;
      resolve();
    });
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(String(event.data));
        if (message.type === "connection.pong") {
          const id = message.payload?.id;
          const sentAt = pendingPings.get(id);
          if (sentAt) {
            pongLatencies.push(performance.now() - sentAt);
            pendingPings.delete(id);
          }
        }
      } catch {
        // Main phase handler counts parse errors after setup.
      }
    });
  });
}

function publishRealtimeEvents(count) {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", ["exec", "-i", "iacs-redis", "redis-cli", "--pipe"], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`redis-cli --pipe exited ${code}: ${stderr.trim()}`));
      }
    });

    for (let seq = 0; seq < count; seq += 1) {
      const event = {
        type: "load.test",
        payload: { seq, source: "scripts/load-test.mjs" },
        created_at: new Date().toISOString(),
      };
      child.stdin.write(
        respArray([
          "XADD",
          "iacs:realtime:events:v1",
          "MAXLEN",
          "~",
          "10000",
          "*",
          "event",
          JSON.stringify(event),
          "origin",
          "load-test",
        ]),
      );
    }
    child.stdin.end();
  });
}

async function sampleMetrics(label) {
  result.metrics.push({
    label,
    at: new Date().toISOString(),
    docker_stats: dockerStats(),
    backend_proc: backendProcMetrics(),
    redis_memory: redisMemory(),
    postgres_activity: postgresActivity(),
  });
}

function dockerStats() {
  const completed = spawnSync(
    "docker",
    [
      "stats",
      "--no-stream",
      "--format",
      "{{json .}}",
      "iacs-backend",
      "iacs-frontend",
      "iacs-postgres",
      "iacs-redis",
    ],
    { encoding: "utf8", maxBuffer: 4 * 1024 * 1024 },
  );
  return completed.stdout
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => safeJson(line));
}

function backendProcMetrics() {
  const script = [
    "python - <<'PY'",
    "from pathlib import Path",
    "import json, re",
    "def kv_status():",
    "    keep = {'VmRSS','VmHWM','VmSize','VmData','Threads','voluntary_ctxt_switches','nonvoluntary_ctxt_switches'}",
    "    out = {}",
    "    for line in Path('/proc/1/status').read_text().splitlines():",
    "        key = line.split(':', 1)[0]",
    "        if key in keep:",
    "            out[key] = line.split(':', 1)[1].strip()",
    "    return out",
    "def smaps():",
    "    out = {}",
    "    p = Path('/proc/1/smaps_rollup')",
    "    if not p.exists():",
    "        return out",
    "    keep = {'Rss','Pss','Private_Clean','Private_Dirty','Shared_Clean','Shared_Dirty','Anonymous','Swap'}",
    "    for line in p.read_text().splitlines():",
    "        key = line.split(':', 1)[0]",
    "        if key in keep:",
    "            out[key] = line.split(':', 1)[1].strip()",
    "    return out",
    "def cgroup():",
    "    out = {}",
    "    base = Path('/sys/fs/cgroup')",
    "    for name in ['memory.current','memory.peak','memory.max','cpu.stat','pids.current']:",
    "        p = base / name",
    "        if p.exists():",
    "            out[name] = p.read_text().strip()",
    "    return out",
    "print(json.dumps({'status': kv_status(), 'smaps_rollup': smaps(), 'cgroup': cgroup()}))",
    "PY",
  ].join("\n");
  const completed = spawnSync("docker", ["exec", "iacs-backend", "sh", "-lc", script], {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
  });
  return safeJson(completed.stdout.trim());
}

function redisMemory() {
  const completed = spawnSync(
    "docker",
    ["exec", "iacs-redis", "redis-cli", "INFO", "memory"],
    { encoding: "utf8", maxBuffer: 2 * 1024 * 1024 },
  );
  const out = {};
  for (const line of completed.stdout.split(/\r?\n/)) {
    const [key, value] = line.split(":");
    if (
      [
        "used_memory_human",
        "used_memory_peak_human",
        "used_memory_dataset",
        "mem_fragmentation_ratio",
        "maxmemory",
        "maxmemory_policy",
        "evicted_keys",
      ].includes(key)
    ) {
      out[key] = value;
    }
  }
  return out;
}

function postgresActivity() {
  const sql = "select coalesce(state,'internal') as state, count(*) from pg_stat_activity group by 1 order by 1;";
  const completed = spawnSync(
    "docker",
    ["exec", "iacs-postgres", "psql", "-U", "iacs", "-d", "iacs", "-t", "-A", "-F", ",", "-c", sql],
    { encoding: "utf8", maxBuffer: 2 * 1024 * 1024 },
  );
  return completed.stdout
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [state, count] = line.split(",");
      return { state, count: Number(count) };
    });
}

function getAdminToken() {
  if (process.env.IACS_LOAD_TEST_TOKEN) {
    return process.env.IACS_LOAD_TEST_TOKEN;
  }
  const script = [
    "cd /workspace/backend",
    "python - <<'PY'",
    "import asyncio",
    "from sqlalchemy import select",
    "from app.db.session import AsyncSessionLocal",
    "from app.models import User",
    "from app.models.enums import UserRole",
    "from app.services.auth import create_access_token",
    "async def main():",
    "    async with AsyncSessionLocal() as session:",
    "        user = await session.scalar(",
    "            select(User).where(User.is_active.is_(True), User.role == UserRole.ADMIN).limit(1)",
    "        )",
    "        if not user:",
    "            return",
    "        token, _ = await create_access_token(user, remember_me=False)",
    "        print(token)",
    "asyncio.run(main())",
    "PY",
  ].join("\n");
  const completed = spawnSync("docker", ["compose", "exec", "-T", "backend", "sh", "-lc", script], {
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
  });
  if (completed.status !== 0) {
    console.error("warning: failed to generate admin load-test token");
    return "";
  }
  return completed.stdout.trim();
}

function summarizeAutocannon(raw) {
  if (!raw) {
    return null;
  }
  return {
    requests_average: raw.requests?.average,
    requests_total: raw.requests?.total,
    latency_avg_ms: raw.latency?.average,
    latency_p50_ms: raw.latency?.p50,
    latency_p90_ms: raw.latency?.p90,
    latency_p99_ms: raw.latency?.p99,
    latency_max_ms: raw.latency?.max,
    throughput_average_bytes: raw.throughput?.average,
    errors: raw.errors,
    timeouts: raw.timeouts,
    non2xx: raw.non2xx,
    statusCodeStats: raw.statusCodeStats,
  };
}

function summarizeNumbers(values) {
  if (!values.length) {
    return { count: 0 };
  }
  const sorted = [...values].sort((a, b) => a - b);
  return {
    count: values.length,
    avg: round(values.reduce((sum, value) => sum + value, 0) / values.length),
    p50: round(percentile(sorted, 50)),
    p90: round(percentile(sorted, 90)),
    p99: round(percentile(sorted, 99)),
    max: round(sorted[sorted.length - 1]),
  };
}

function percentile(sorted, p) {
  const index = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[index];
}

function printHttpSummary(phase) {
  const summary = phase.summary;
  if (!summary) {
    console.log(`${phase.title}: failed to parse autocannon output`);
    return;
  }
  console.log(
    [
      phase.title,
      `${summary.requests_average} req/s`,
      `p99 ${summary.latency_p99_ms} ms`,
      `max ${summary.latency_max_ms} ms`,
      `errors ${summary.errors ?? 0}`,
      `timeouts ${summary.timeouts ?? 0}`,
      `non2xx ${summary.non2xx ?? 0}`,
    ].join(" | "),
  );
}

function printWebsocketSummary(summary) {
  if (summary.skipped) {
    console.log(`websocket: skipped (${summary.reason})`);
    return;
  }
  console.log(
    [
      "websocket_realtime",
      `${summary.clients_opened}/${summary.clients_requested} opened`,
      `${summary.synthetic_events_published} redis events`,
      `${summary.load_events_received}/${summary.expected_fanout_messages} fanout messages`,
      `pong p99 ${summary.pong_latency_ms.p99 ?? "n/a"} ms`,
    ].join(" | "),
  );
}

function parseArgs(argv) {
  const parsed = {};
  for (const arg of argv) {
    if (!arg.startsWith("--")) {
      continue;
    }
    const [key, value] = arg.slice(2).split("=", 2);
    parsed[key] = value ?? true;
  }
  return parsed;
}

function numberArg(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function parseJsonFromOutput(output) {
  const lines = output
    .trim()
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (!lines[index].startsWith("{")) {
      continue;
    }
    const parsed = safeJson(lines[index]);
    if (parsed) {
      return parsed;
    }
  }
  return null;
}

function sanitizeAutocannonStderr(stderr) {
  return stderr
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith("npm warn deprecated"))
    .join("\n")
    .slice(0, 4000);
}

function safeJson(value) {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function respArray(parts) {
  return `*${parts.length}\r\n${parts.map(respBulk).join("")}`;
}

function respBulk(value) {
  const stringValue = String(value);
  return `$${Buffer.byteLength(stringValue)}\r\n${stringValue}\r\n`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function round(value) {
  return Math.round(value * 100) / 100;
}

function redactOptions(value) {
  return { ...value, skipToken: undefined };
}

function defaultOutputPath(date) {
  const stamp = date.toISOString().replace(/[:.]/g, "-");
  return path.resolve("logs/load-tests", `iacs-load-test-${stamp}.json`);
}
