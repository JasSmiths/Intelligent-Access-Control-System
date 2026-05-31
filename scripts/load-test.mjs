#!/usr/bin/env node

import { spawnSync, spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import process from "node:process";
import tls from "node:tls";
import { performance } from "node:perf_hooks";

const LOAD_WS_CONNECTING = 0;
const LOAD_WS_OPEN = 1;
const LOAD_WS_CLOSING = 2;
const LOAD_WS_CLOSED = 3;

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
  tokenStdin: Boolean(args["token-stdin"]),
  output: args.output,
};

const startedAt = new Date();
const token = options.skipToken ? "" : await getAdminToken();
const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};
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
  httpTarget("frontend_api_health", options.frontend, "/api/v1/health"),
  httpTarget("backend_api_health_direct", options.backend, "/api/v1/health"),
  httpTarget("frontend_auth_status", options.frontend, "/api/v1/auth/status"),
  httpTarget("frontend_maintenance_status_auth", options.frontend, "/api/v1/maintenance/status", true),
  httpTarget("frontend_events_auth", options.frontend, "/api/v1/events?limit=100", true),
  httpTarget("frontend_movements_auth", options.frontend, "/api/v1/access/movements?limit=100", true),
  httpTarget("frontend_people_auth", options.frontend, "/api/v1/people?include_media=false", true),
];

for (const target of httpTargets) {
  if (target.requiresToken && !token) {
    result.http.push({ title: target.title, skipped: true, reason: "no admin token available" });
    continue;
  }
  await sampleMetrics(`before_${target.title}`);
  const phase = await runHttpLoad(target);
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

async function runHttpLoad(target) {
  const started = performance.now();
  const deadline = started + options.duration * 1000;
  const latencies = [];
  const statusCodeStats = {};
  let requests = 0;
  let errors = 0;
  let timeouts = 0;
  let non2xx = 0;
  let bytes = 0;

  async function worker() {
    while (performance.now() < deadline) {
      const requestStarted = performance.now();
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), options.timeout * 1000);
      try {
        const response = await fetch(target.url, {
          headers: target.headers ?? {},
          signal: controller.signal,
        });
        const body = await response.arrayBuffer();
        bytes += body.byteLength;
        requests += 1;
        statusCodeStats[response.status] = {
          count: (statusCodeStats[response.status]?.count ?? 0) + 1,
        };
        if (!response.ok) {
          non2xx += 1;
        }
      } catch (error) {
        requests += 1;
        if (error?.name === "AbortError") {
          timeouts += 1;
        } else {
          errors += 1;
        }
      } finally {
        clearTimeout(timeout);
        latencies.push(performance.now() - requestStarted);
      }
    }
  }

  await Promise.all(Array.from({ length: options.connections }, () => worker()));
  const elapsedMs = performance.now() - started;
  return {
    title: target.title,
    url: target.url,
    connections: options.connections,
    duration_seconds: options.duration,
    elapsed_ms: Math.round(elapsedMs),
    exit_code: 0,
    error: undefined,
    stderr: "",
    summary: summarizeHttpLoad({
      requests,
      latencies,
      bytes,
      elapsedMs,
      errors,
      timeouts,
      non2xx,
      statusCodeStats,
    }),
    raw: null,
  };
}

async function runWebsocketPhase(token) {
  const wsBase = options.frontend.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  const url = `${wsBase}/api/v1/realtime/ws`;
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
      openWebsocket(url, index, sockets, counters),
    ),
  );

  const started = performance.now();
  const pingTimer = setInterval(() => {
    const now = Date.now();
    for (const [index, socket] of sockets.entries()) {
      if (socket?.readyState !== LOAD_WS_OPEN) {
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

function openWebsocket(url, index, sockets, counters) {
  return new Promise((resolve) => {
    const socket = createAuthenticatedWebSocket(url, token);
    let settled = false;
    const settle = (failed) => {
      if (settled) {
        return;
      }
      settled = true;
      if (failed) {
        counters.failed += 1;
      }
      resolve();
    };
    const timeout = setTimeout(() => {
      try {
        socket.close();
      } catch {
        // Best effort cleanup only.
      }
      settle(true);
    }, 10000);

    socket.addEventListener("open", () => {
      clearTimeout(timeout);
      counters.opened += 1;
      sockets[index] = socket;
      settle(false);
    });
    socket.addEventListener("error", () => {
      clearTimeout(timeout);
      settle(true);
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

async function getAdminToken() {
  if (process.env.IACS_LOAD_TEST_TOKEN) {
    return process.env.IACS_LOAD_TEST_TOKEN.trim();
  }
  if (process.env.IACS_LOAD_TEST_TOKEN_FILE) {
    return fs.readFileSync(process.env.IACS_LOAD_TEST_TOKEN_FILE, "utf8").trim();
  }
  if (options.tokenStdin) {
    return (await readStdin()).trim();
  }
  console.error(
    "warning: no load-test token supplied; set IACS_LOAD_TEST_TOKEN, IACS_LOAD_TEST_TOKEN_FILE, or pass --token-stdin",
  );
  return "";
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let value = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      value += chunk;
    });
    process.stdin.on("end", () => resolve(value));
    process.stdin.on("error", reject);
  });
}

function summarizeHttpLoad(stats) {
  const sorted = [...stats.latencies].sort((a, b) => a - b);
  const elapsedSeconds = Math.max(stats.elapsedMs / 1000, 0.001);
  const latencyAverage =
    stats.latencies.length > 0
      ? stats.latencies.reduce((sum, value) => sum + value, 0) / stats.latencies.length
      : 0;
  return {
    requests_average: round(stats.requests / elapsedSeconds),
    requests_total: stats.requests,
    latency_avg_ms: round(latencyAverage),
    latency_p50_ms: sorted.length ? round(percentile(sorted, 50)) : 0,
    latency_p90_ms: sorted.length ? round(percentile(sorted, 90)) : 0,
    latency_p99_ms: sorted.length ? round(percentile(sorted, 99)) : 0,
    latency_max_ms: sorted.length ? round(sorted[sorted.length - 1]) : 0,
    throughput_average_bytes: round(stats.bytes / elapsedSeconds),
    errors: stats.errors,
    timeouts: stats.timeouts,
    non2xx: stats.non2xx,
    statusCodeStats: stats.statusCodeStats,
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
    console.log(`${phase.title}: no HTTP load summary available`);
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

function httpTarget(title, baseUrl, pathname, requiresToken = false) {
  return {
    title,
    url: `${baseUrl}${pathname}`,
    headers: requiresToken ? authHeaders : undefined,
    requiresToken,
  };
}

function numberArg(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
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

function createAuthenticatedWebSocket(urlText, token) {
  const url = new URL(urlText);
  const secure = url.protocol === "wss:";
  const port = Number(url.port || (secure ? 443 : 80));
  const hostHeader = url.port ? `${url.hostname}:${url.port}` : url.hostname;
  const pathAndQuery = `${url.pathname || "/"}${url.search}`;
  const listeners = { open: [], error: [], message: [], close: [] };
  let readyState = LOAD_WS_CONNECTING;
  let handshakeComplete = false;
  let buffer = Buffer.alloc(0);
  let closeDispatched = false;

  const socket = secure
    ? tls.connect({ host: url.hostname, port, servername: url.hostname })
    : net.connect({ host: url.hostname, port });

  const api = {
    get readyState() {
      return readyState;
    },
    addEventListener(type, handler) {
      if (listeners[type]) {
        listeners[type].push(handler);
      }
    },
    send(message) {
      if (readyState !== LOAD_WS_OPEN) {
        return;
      }
      sendFrame(socket, 0x1, Buffer.from(String(message)));
    },
    close() {
      if (readyState === LOAD_WS_CLOSED || readyState === LOAD_WS_CLOSING) {
        return;
      }
      readyState = LOAD_WS_CLOSING;
      try {
        sendFrame(socket, 0x8, Buffer.alloc(0));
      } catch {
        // Best effort cleanup only.
      }
      socket.end();
    },
  };

  const dispatch = (type, event = {}) => {
    for (const handler of listeners[type] ?? []) {
      try {
        handler(event);
      } catch {
        // Load-test callbacks should not crash the runner.
      }
    }
  };

  const fail = (error) => {
    if (readyState === LOAD_WS_CLOSED) {
      return;
    }
    readyState = LOAD_WS_CLOSED;
    dispatch("error", { error });
    socket.destroy();
  };

  socket.setTimeout(10000, () => fail(new Error("websocket handshake timed out")));
  socket.on("connect", () => {
    const key = crypto.randomBytes(16).toString("base64");
    const request = [
      `GET ${pathAndQuery} HTTP/1.1`,
      `Host: ${hostHeader}`,
      "Upgrade: websocket",
      "Connection: Upgrade",
      `Sec-WebSocket-Key: ${key}`,
      "Sec-WebSocket-Version: 13",
      `Authorization: Bearer ${token}`,
      "\r\n",
    ].join("\r\n");
    socket.write(request);
  });
  socket.on("data", (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    if (!handshakeComplete) {
      const headerEnd = buffer.indexOf("\r\n\r\n");
      if (headerEnd === -1) {
        return;
      }
      const header = buffer.subarray(0, headerEnd).toString("utf8");
      if (!header.startsWith("HTTP/1.1 101") && !header.startsWith("HTTP/1.0 101")) {
        fail(new Error(`websocket handshake failed: ${header.split("\r\n")[0]}`));
        return;
      }
      buffer = buffer.subarray(headerEnd + 4);
      handshakeComplete = true;
      readyState = LOAD_WS_OPEN;
      socket.setTimeout(0);
      dispatch("open");
    }
    parseWebSocketFrames();
  });
  socket.on("error", (error) => fail(error));
  socket.on("close", () => {
    readyState = LOAD_WS_CLOSED;
    if (!closeDispatched) {
      closeDispatched = true;
      dispatch("close");
    }
  });

  function parseWebSocketFrames() {
    while (buffer.length >= 2) {
      const first = buffer[0];
      const second = buffer[1];
      const opcode = first & 0x0f;
      const masked = Boolean(second & 0x80);
      let length = second & 0x7f;
      let offset = 2;

      if (length === 126) {
        if (buffer.length < offset + 2) {
          return;
        }
        length = buffer.readUInt16BE(offset);
        offset += 2;
      } else if (length === 127) {
        if (buffer.length < offset + 8) {
          return;
        }
        const bigLength = buffer.readBigUInt64BE(offset);
        if (bigLength > BigInt(Number.MAX_SAFE_INTEGER)) {
          fail(new Error("websocket frame too large"));
          return;
        }
        length = Number(bigLength);
        offset += 8;
      }

      let mask;
      if (masked) {
        if (buffer.length < offset + 4) {
          return;
        }
        mask = buffer.subarray(offset, offset + 4);
        offset += 4;
      }
      if (buffer.length < offset + length) {
        return;
      }

      let payload = buffer.subarray(offset, offset + length);
      buffer = buffer.subarray(offset + length);
      if (masked && mask) {
        payload = unmaskPayload(payload, mask);
      }

      if (opcode === 0x1) {
        dispatch("message", { data: payload.toString("utf8") });
      } else if (opcode === 0x8) {
        api.close();
        return;
      } else if (opcode === 0x9) {
        sendFrame(socket, 0xA, payload);
      }
    }
  }

  return api;
}

function sendFrame(socket, opcode, payload) {
  const length = payload.length;
  const header =
    length < 126
      ? Buffer.from([0x80 | opcode, 0x80 | length])
      : length < 65536
        ? Buffer.from([0x80 | opcode, 0x80 | 126, (length >> 8) & 0xff, length & 0xff])
        : websocketLargeFrameHeader(opcode, length);
  const mask = crypto.randomBytes(4);
  const masked = Buffer.alloc(length);
  for (let index = 0; index < length; index += 1) {
    masked[index] = payload[index] ^ mask[index % 4];
  }
  socket.write(Buffer.concat([header, mask, masked]));
}

function websocketLargeFrameHeader(opcode, length) {
  const header = Buffer.alloc(10);
  header[0] = 0x80 | opcode;
  header[1] = 0x80 | 127;
  header.writeBigUInt64BE(BigInt(length), 2);
  return header;
}

function unmaskPayload(payload, mask) {
  const unmasked = Buffer.alloc(payload.length);
  for (let index = 0; index < payload.length; index += 1) {
    unmasked[index] = payload[index] ^ mask[index % 4];
  }
  return unmasked;
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
