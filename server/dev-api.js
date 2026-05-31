import express from "express";
import { createWriteStream } from "node:fs";
import { mkdir, open, readFile, readdir, rm, stat, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import crypto from "node:crypto";

const app = express();
const PORT = Number(process.env.HEAL_API_PORT || 8787);
const UPLOAD_ROOT =
  process.env.HEAL_UPLOAD_ROOT ||
  "C:\\ServerCIT\\services\\heal-vcf-integrity\\incoming";
const VALIDATOR_SCRIPT =
  process.env.HEAL_VALIDATOR_SCRIPT ||
  "C:\\ServerCIT\\services\\heal-vcf-integrity\\validate_vcf_integrity.py";
const CANON_ROOT =
  process.env.HEAL_CANON_ROOT ||
  "C:\\ServerCIT\\services\\heal-canon-intake";
const CANON_PROCESSOR_SCRIPT =
  process.env.HEAL_CANON_PROCESSOR_SCRIPT ||
  "C:\\ServerCIT\\services\\heal-canon-intake\\process_heal_canon.py";
const RSID_RESOLUTION_ROOT =
  process.env.HEAL_RSID_RESOLUTION_ROOT ||
  "C:\\ServerCIT\\services\\heal-rsid-resolution";
const VCF_CANON_MATCH_ROOT =
  process.env.HEAL_VCF_CANON_MATCH_ROOT ||
  "C:\\ServerCIT\\services\\heal-vcf-canon-match";
const PYTHON_EXE = process.env.HEAL_PYTHON_EXE || "python";
const MAX_UPLOADS = Math.max(1, Number.parseInt(process.env.HEAL_MAX_UPLOADS || "12", 10) || 12);
const UPLOAD_TTL_MS =
  Math.max(1, Number.parseInt(process.env.HEAL_UPLOAD_TTL_HOURS || "24", 10) || 24) * 60 * 60 * 1000;
const CHUNK_SIZE_BYTES = Math.min(
  24 * 1024 * 1024,
  Math.max(1024 * 1024, Number.parseInt(process.env.HEAL_UPLOAD_CHUNK_SIZE_BYTES || `${8 * 1024 * 1024}`, 10)),
);
const MAX_FILE_SIZE_BYTES = Math.max(
  1024 * 1024,
  Number.parseInt(process.env.HEAL_MAX_FILE_SIZE_BYTES || `${6 * 1024 * 1024 * 1024}`, 10),
);
const MAX_CANON_FILE_SIZE_BYTES = Math.max(
  64 * 1024,
  Number.parseInt(process.env.HEAL_MAX_CANON_FILE_SIZE_BYTES || `${25 * 1024 * 1024}`, 10),
);
const MAX_CANONS = Math.max(1, Number.parseInt(process.env.HEAL_MAX_CANONS || "8", 10) || 8);
const MAX_ACTIVE_UPLOADS_PER_CLIENT = Math.max(
  1,
  Number.parseInt(process.env.HEAL_MAX_ACTIVE_UPLOADS_PER_CLIENT || "2", 10) || 2,
);
const INIT_RATE_LIMIT_PER_HOUR = Math.max(
  1,
  Number.parseInt(process.env.HEAL_INIT_RATE_LIMIT_PER_HOUR || "10", 10) || 10,
);
const TURNSTILE_SECRET = process.env.HEAL_TURNSTILE_SECRET || "";
const REQUIRE_ORIGIN = process.env.HEAL_REQUIRE_ORIGIN !== "false";
const N8N_UPLOAD_WEBHOOK_URL = process.env.HEAL_N8N_UPLOAD_WEBHOOK_URL || "";
const N8N_VALIDATION_WEBHOOK_URL =
  process.env.HEAL_N8N_VALIDATION_WEBHOOK_URL || process.env.HEAL_N8N_WEBHOOK_URL || "";
const N8N_CANON_WEBHOOK_URL = process.env.HEAL_N8N_CANON_WEBHOOK_URL || "";
const N8N_RSID_RESOLUTION_WEBHOOK_URL = process.env.HEAL_N8N_RSID_RESOLUTION_WEBHOOK_URL || "";
const N8N_VCF_CANON_MATCH_WEBHOOK_URL = process.env.HEAL_N8N_VCF_CANON_MATCH_WEBHOOK_URL || "";
const N8N_WEBHOOK_TOKEN = process.env.HEAL_N8N_WEBHOOK_TOKEN || "";
const ALLOWED_ORIGINS = (process.env.HEAL_ALLOWED_ORIGINS ||
  "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);
const TURNSTILE_ALLOWED_HOSTNAMES = (process.env.HEAL_TURNSTILE_ALLOWED_HOSTNAMES ||
  ALLOWED_ORIGINS.map((origin) => {
    try {
      return new URL(origin).hostname;
    } catch {
      return "";
    }
  }).join(","))
  .split(",")
  .map((hostname) => hostname.trim().toLowerCase())
  .filter(Boolean);

const jobs = new Map();
const uploads = new Map();
const initRateLimits = new Map();

app.use((req, res, next) => {
  const requestOrigin = req.headers.origin;
  if (requestOrigin && !ALLOWED_ORIGINS.includes(requestOrigin)) {
    res.status(403).json({ error: "Origin is not allowed." });
    return;
  }
  if (REQUIRE_ORIGIN && !requestOrigin && !["GET", "OPTIONS"].includes(req.method)) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }
  if (requestOrigin) {
    res.setHeader("Access-Control-Allow-Origin", requestOrigin);
    res.setHeader("Vary", "Origin");
  }
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS");
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Content-Type, X-Chunk-Index, X-Upload-Id, X-Canon-File-Name, X-Turnstile-Token",
  );
  if (req.method === "OPTIONS") {
    res.status(204).send();
    return;
  }
  next();
});

app.use(express.json({ limit: "1mb" }));

function safeFileName(name) {
  const parsed = path.basename(String(name || "upload.vcf"));
  return parsed.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 180) || "upload.vcf";
}

function isAllowedVcfName(fileName) {
  const normalized = fileName.toLowerCase();
  return normalized.endsWith(".vcf") || normalized.endsWith(".vcf.gz") || normalized.endsWith(".gz");
}

function isAllowedCanonName(fileName) {
  const normalized = fileName.toLowerCase();
  return normalized.endsWith(".csv") || normalized.endsWith(".xlsx");
}

function canonPaths() {
  const root = path.resolve(CANON_ROOT);
  return {
    root,
    incoming: path.join(root, "incoming"),
    runs: path.join(root, "runs"),
    current: path.join(root, "current"),
    currentManifest: path.join(root, "current", "current.json"),
  };
}

function rsidResolutionPaths() {
  const root = path.resolve(RSID_RESOLUTION_ROOT);
  return {
    root,
    runs: path.join(root, "runs"),
    current: path.join(root, "current"),
    currentManifest: path.join(root, "current", "current.json"),
  };
}

function vcfCanonMatchPaths() {
  const root = path.resolve(VCF_CANON_MATCH_ROOT);
  return {
    root,
    runs: path.join(root, "runs"),
    current: path.join(root, "current"),
    currentManifest: path.join(root, "current", "current.json"),
  };
}

function clientIp(req) {
  const forwarded = req.headers["cf-connecting-ip"] || req.headers["x-forwarded-for"];
  if (Array.isArray(forwarded)) return forwarded[0];
  if (forwarded) return String(forwarded).split(",")[0].trim();
  return req.socket.remoteAddress || "unknown";
}

function clientFingerprint(req) {
  const source = `${clientIp(req)}|${req.headers["user-agent"] || ""}`;
  return crypto.createHash("sha256").update(source).digest("hex");
}

function checkInitRateLimit(req) {
  const ip = clientIp(req);
  const now = Date.now();
  const windowMs = 60 * 60 * 1000;
  const current = initRateLimits.get(ip) || [];
  const fresh = current.filter((timestamp) => now - timestamp < windowMs);
  if (fresh.length >= INIT_RATE_LIMIT_PER_HOUR) {
    initRateLimits.set(ip, fresh);
    return false;
  }
  fresh.push(now);
  initRateLimits.set(ip, fresh);
  return true;
}

function activeUploadsForClient(fingerprint) {
  let count = 0;
  for (const upload of uploads.values()) {
    if (
      upload.clientFingerprint === fingerprint &&
      ["initialized", "uploading", "assembled"].includes(upload.status)
    ) {
      count += 1;
    }
  }
  return count;
}

function isPathInside(parent, target) {
  const relative = path.relative(path.resolve(parent), path.resolve(target));
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function manifestPath(uploadDir) {
  return path.join(uploadDir, "upload.json");
}

function publicUpload(upload) {
  const receivedChunks = upload.receivedChunks.filter(Boolean).length;
  return {
    uploadId: upload.uploadId,
    fileName: upload.fileName,
    sizeBytes: upload.sizeBytes,
    chunkSizeBytes: upload.chunkSizeBytes,
    totalChunks: upload.totalChunks,
    receivedChunks,
    uploadedBytes: Math.min(upload.sizeBytes, receivedChunks * upload.chunkSizeBytes),
    progress: upload.totalChunks > 0 ? Math.round((receivedChunks / upload.totalChunks) * 100) : 0,
    status: upload.status,
    createdAt: upload.createdAt,
    updatedAt: upload.updatedAt,
  };
}

function publicCanon(summary, preview, manifest) {
  if (!summary) {
    return {
      hasCanon: false,
      current: null,
      preview: { columns: [], rows: [] },
    };
  }

  return {
    hasCanon: true,
    current: {
      runId: manifest?.runId || null,
      sourceFileName: summary.sourceFileName || manifest?.sourceFileName || null,
      status: summary.status,
      errors: summary.errors || [],
      warnings: summary.warnings || [],
      metadata: summary.metadata || {},
      timestamps: summary.timestamps || {},
      createdAt: manifest?.createdAt || summary.timestamps?.completedAt || null,
    },
    preview: {
      columns: preview?.columns || [],
      rows: preview?.rows || [],
      generatedAt: preview?.generatedAt || null,
    },
  };
}

function sanitizeValidationResult(result, upload) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  publicResult.metadata = publicResult.metadata || {};
  delete publicResult.metadata.path;
  publicResult.metadata.file_name = upload.fileName;
  publicResult.metadata.upload_id = upload.uploadId;
  return publicResult;
}

function sanitizeVcfCanonMatchResult(result, upload) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPaths;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  publicResult.metadata = publicResult.metadata || {};
  publicResult.metadata.file_name = upload.fileName;
  publicResult.metadata.upload_id = upload.uploadId;
  return publicResult;
}

async function saveUpload(upload) {
  upload.updatedAt = new Date().toISOString();
  uploads.set(upload.uploadId, upload);
  await writeFile(manifestPath(upload.uploadDir), JSON.stringify(upload, null, 2), "utf8");
}

async function loadUpload(uploadId) {
  if (uploads.has(uploadId)) return uploads.get(uploadId);
  const resolvedUploadRoot = path.resolve(UPLOAD_ROOT);
  const uploadDir = path.join(resolvedUploadRoot, uploadId);
  const resolvedUploadDir = path.resolve(uploadDir);
  if (!isPathInside(resolvedUploadRoot, resolvedUploadDir)) return null;
  const raw = await readFile(manifestPath(resolvedUploadDir), "utf8").catch(() => null);
  if (!raw) return null;
  const upload = JSON.parse(raw);
  if (!isPathInside(resolvedUploadRoot, upload.storedPath)) return null;
  uploads.set(upload.uploadId, upload);
  return upload;
}

async function findReusableUpload(fileName, sizeBytes, fingerprint) {
  await mkdir(UPLOAD_ROOT, { recursive: true });
  const resolvedUploadRoot = path.resolve(UPLOAD_ROOT);
  const entries = await readdir(resolvedUploadRoot, { withFileTypes: true });
  const matches = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const uploadDir = path.join(resolvedUploadRoot, entry.name);
    const resolvedUploadDir = path.resolve(uploadDir);
    if (!isPathInside(resolvedUploadRoot, resolvedUploadDir)) continue;
    const raw = await readFile(manifestPath(resolvedUploadDir), "utf8").catch(() => null);
    if (!raw) continue;
    const upload = JSON.parse(raw);
    if (
      upload.status === "complete" &&
      upload.fileName === fileName &&
      Number(upload.sizeBytes) === Number(sizeBytes) &&
      upload.clientFingerprint === fingerprint
    ) {
      matches.push(upload);
    }
  }

  matches.sort((a, b) => new Date(b.updatedAt || b.createdAt).getTime() - new Date(a.updatedAt || a.createdAt).getTime());
  return matches[0] || null;
}

async function cleanupStaleUploads() {
  await mkdir(UPLOAD_ROOT, { recursive: true });
  const resolvedUploadRoot = path.resolve(UPLOAD_ROOT);
  const now = Date.now();
  const entries = await readdir(resolvedUploadRoot, { withFileTypes: true });
  const candidates = [];

  for (const entry of entries) {
    const target = path.join(resolvedUploadRoot, entry.name);
    const resolvedTarget = path.resolve(target);
    if (!isPathInside(resolvedUploadRoot, resolvedTarget)) continue;
    const entryStat = await stat(resolvedTarget).catch(() => null);
    if (!entryStat) continue;
    candidates.push({
      name: entry.name,
      path: resolvedTarget,
      mtimeMs: entryStat.mtimeMs,
      stale: now - entryStat.mtimeMs > UPLOAD_TTL_MS,
    });
  }

  const stale = candidates.filter((entry) => entry.stale);
  const fresh = candidates.filter((entry) => !entry.stale).sort((a, b) => b.mtimeMs - a.mtimeMs);
  const overflow = fresh.slice(MAX_UPLOADS);
  const removals = [...stale, ...overflow];
  await Promise.all(
    removals.map(async (entry) => {
      await rm(entry.path, { recursive: true, force: true });
      uploads.delete(entry.name);
    }),
  );
}

async function cleanupOldCanons() {
  const paths = canonPaths();
  await mkdir(paths.runs, { recursive: true });
  const entries = await readdir(paths.runs, { withFileTypes: true }).catch(() => []);
  const candidates = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const target = path.join(paths.runs, entry.name);
    const targetStat = await stat(target).catch(() => null);
    if (!targetStat) continue;
    candidates.push({ name: entry.name, path: target, mtimeMs: targetStat.mtimeMs });
  }
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  await Promise.all(candidates.slice(MAX_CANONS).map((entry) => rm(entry.path, { recursive: true, force: true })));
}

function runCanonProcessor(inputPath, outputDir, sourceFileName) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      PYTHON_EXE,
      [
        CANON_PROCESSOR_SCRIPT,
        "--input",
        inputPath,
        "--output-dir",
        outputDir,
        "--source-file-name",
        sourceFileName,
      ],
      { windowsHide: true },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
      const lastLine = lines[lines.length - 1] || "{}";
      let result;
      try {
        result = JSON.parse(lastLine);
      } catch (error) {
        reject(new Error(`Canon processor returned invalid JSON. ${stderr || error.message}`));
        return;
      }
      if (code !== 0 && result.status !== "warning") {
        reject(new Error(result.errors?.[0] || stderr || `Canon processor exited with code ${code}.`));
        return;
      }
      resolve(result);
    });
  });
}

async function processCanonWithN8n(payload) {
  if (!N8N_CANON_WEBHOOK_URL) return null;
  const headers = { "Content-Type": "application/json" };
  if (N8N_WEBHOOK_TOKEN) headers.Authorization = `Bearer ${N8N_WEBHOOK_TOKEN}`;
  const response = await fetch(N8N_CANON_WEBHOOK_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { error: text };
  }
  if (!response.ok) {
    throw new Error(body.error || body.message || `n8n canon intake failed with ${response.status}.`);
  }
  return body.summary || body.result || body;
}

async function postWorkflowForSummary(url, payload, label) {
  if (!url) return null;
  const headers = { "Content-Type": "application/json" };
  if (N8N_WEBHOOK_TOKEN) headers.Authorization = `Bearer ${N8N_WEBHOOK_TOKEN}`;
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { error: text };
  }
  if (!response.ok) {
    throw new Error(body.error || body.message || `${label} failed with ${response.status}.`);
  }
  return body.summary || body.result || body;
}

function runBase64JsonScript(scriptPath, payload) {
  return new Promise((resolve, reject) => {
    const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
    const child = spawn(PYTHON_EXE, [scriptPath, "--input-json-base64", encoded], {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", reject);
    child.on("close", (code) => {
      const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
      const lastLine = lines[lines.length - 1] || "{}";
      let result;
      try {
        result = JSON.parse(lastLine);
      } catch (error) {
        reject(new Error(`Processor returned invalid JSON. ${stderr || error.message}`));
        return;
      }
      if (code !== 0 && result.status !== "warning") {
        reject(new Error(result.errors?.[0] || stderr || `Processor exited with code ${code}.`));
        return;
      }
      resolve(result);
    });
  });
}

async function processRsidResolution(payload) {
  return (
    (await postWorkflowForSummary(N8N_RSID_RESOLUTION_WEBHOOK_URL, payload, "n8n rsID resolution")) ||
    (await runBase64JsonScript(path.join(RSID_RESOLUTION_ROOT, "resolve_rsid_coordinates.py"), payload))
  );
}

async function processVcfCanonMatch(payload) {
  return (
    (await postWorkflowForSummary(N8N_VCF_CANON_MATCH_WEBHOOK_URL, payload, "n8n VCF-canon match")) ||
    (await runBase64JsonScript(path.join(VCF_CANON_MATCH_ROOT, "match_vcf_to_rsid_ready.py"), payload))
  );
}

async function loadCurrentCanon() {
  const manifest = await loadCurrentCanonManifest();
  if (!manifest) return publicCanon(null, null, null);
  const paths = canonPaths();
  const summaryPath = path.resolve(manifest.summaryPath || "");
  const previewPath = path.resolve(manifest.previewPath || "");
  if (!isPathInside(paths.root, summaryPath) || !isPathInside(paths.root, previewPath)) {
    return publicCanon(null, null, null);
  }
  const summary = JSON.parse(await readFile(summaryPath, "utf8"));
  const preview = JSON.parse(await readFile(previewPath, "utf8").catch(() => '{"columns":[],"rows":[]}'));
  return publicCanon(summary, preview, manifest);
}

async function loadCurrentCanonManifest() {
  const paths = canonPaths();
  const raw = await readFile(paths.currentManifest, "utf8").catch(() => null);
  if (!raw) return null;
  return JSON.parse(raw);
}

async function loadCurrentRsidResolutionManifest() {
  const paths = rsidResolutionPaths();
  const raw = await readFile(paths.currentManifest, "utf8").catch(() => null);
  if (!raw) return null;
  return JSON.parse(raw);
}

async function saveCurrentRsidResolution(runId, summary) {
  const paths = rsidResolutionPaths();
  await mkdir(paths.current, { recursive: true });
  const manifest = {
    runId,
    summaryPath: path.join(paths.runs, runId, "rsid_resolution_summary.json"),
    rsidMatchReadyCsv: summary.outputs?.rsidMatchReadyCsv || path.join(paths.runs, runId, "rsid_match_ready.csv"),
    createdAt: new Date().toISOString(),
  };
  await writeFile(paths.currentManifest, JSON.stringify(manifest, null, 2), "utf8");
  return manifest;
}

async function resolveRsidForCanon(canonRunId, canonSummary) {
  const paths = rsidResolutionPaths();
  const canonRoot = path.resolve(CANON_ROOT);
  const rsidMasterPath = path.resolve(canonSummary.outputs?.rsidMasterCsv || "");
  if (!isPathInside(canonRoot, rsidMasterPath)) {
    throw new Error("Canon rsID master path is outside the allowed canon root.");
  }
  const rsidRunId = `rsid-${canonRunId}`;
  const outputDir = path.join(paths.runs, rsidRunId);
  await mkdir(outputDir, { recursive: true });
  const payload = {
    event: "heal.rsid.coordinate_resolution.requested",
    runId: rsidRunId,
    canonRunId,
    inputPath: rsidMasterPath,
    outputDir,
    requestedAt: new Date().toISOString(),
  };
  const summary = await processRsidResolution(payload);
  const manifest = await saveCurrentRsidResolution(rsidRunId, summary);
  return { summary, manifest };
}

async function saveCurrentCanon(runId, sourceFileName, summary) {
  const paths = canonPaths();
  await mkdir(paths.current, { recursive: true });
  const manifest = {
    runId,
    sourceFileName,
    summaryPath: path.join(paths.runs, runId, "canon_summary.json"),
    previewPath: path.join(paths.runs, runId, "canon_preview.json"),
    createdAt: new Date().toISOString(),
  };
  await writeFile(paths.currentManifest, JSON.stringify(manifest, null, 2), "utf8");
  return publicCanon(summary, JSON.parse(await readFile(manifest.previewPath, "utf8")), manifest);
}

async function verifyTurnstile(token, remoteIp) {
  if (!TURNSTILE_SECRET) return { ok: true, skipped: true };
  if (!token) return { ok: false, error: "Missing Turnstile token." };

  const body = new URLSearchParams();
  body.set("secret", TURNSTILE_SECRET);
  body.set("response", token);
  if (remoteIp && remoteIp !== "unknown") body.set("remoteip", remoteIp);

  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.success) {
    return { ok: false, error: "Turnstile verification failed." };
  }
  const hostname = String(result.hostname || "").toLowerCase();
  if (TURNSTILE_ALLOWED_HOSTNAMES.length > 0 && !TURNSTILE_ALLOWED_HOSTNAMES.includes(hostname)) {
    return { ok: false, error: "Turnstile hostname is not allowed." };
  }
  return { ok: true };
}

function publicJob(job) {
  return {
    id: job.id,
    status: job.status,
    progress: job.progress,
    message: job.message,
    uploadId: job.uploadId,
    analysisMode: job.analysisMode,
    fileName: job.fileName,
    sizeBytes: job.sizeBytes,
    result: job.result,
    error: job.error,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
  };
}

async function postWebhook(url, payload, job) {
  if (!url) return;
  const headers = { "Content-Type": "application/json" };
  if (N8N_WEBHOOK_TOKEN) headers.Authorization = `Bearer ${N8N_WEBHOOK_TOKEN}`;
  await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  }).catch((error) => {
    if (job) job.n8nError = error.message;
  });
}

async function notifyN8nUpload(upload) {
  const payload = {
    event: "heal.vcf.upload.completed",
    uploadId: upload.uploadId,
    fileName: upload.fileName,
    sizeBytes: upload.sizeBytes,
    storedPath: upload.storedPath,
    completedAt: new Date().toISOString(),
  };
  await postWebhook(N8N_UPLOAD_WEBHOOK_URL, payload);
}

async function notifyN8nValidation(job, upload) {
  if (job.status !== "complete") return;
  const payload = {
    event: "heal.vcf.integrity.completed",
    uploadId: upload.uploadId,
    fileName: upload.fileName,
    sizeBytes: upload.sizeBytes,
    storedPath: upload.storedPath,
    analysisMode: job.analysisMode,
    validationStatus: job.result?.status,
    validationResult: job.result,
    completedAt: new Date().toISOString(),
  };
  await postWebhook(N8N_VALIDATION_WEBHOOK_URL, payload, job);
}

app.get("/api/health", (_req, res) => {
  res.json({
    ok: true,
    storageConfigured: Boolean(UPLOAD_ROOT),
    validatorConfigured: Boolean(VALIDATOR_SCRIPT),
    canonRoot: CANON_ROOT,
    canonProcessorConfigured: Boolean(CANON_PROCESSOR_SCRIPT),
    rsidResolutionRoot: RSID_RESOLUTION_ROOT,
    vcfCanonMatchRoot: VCF_CANON_MATCH_ROOT,
    maxCanonFileSizeBytes: MAX_CANON_FILE_SIZE_BYTES,
    maxCanons: MAX_CANONS,
    maxUploads: MAX_UPLOADS,
    uploadTtlHours: Math.round(UPLOAD_TTL_MS / 60 / 60 / 1000),
    chunkSizeBytes: CHUNK_SIZE_BYTES,
    maxFileSizeBytes: MAX_FILE_SIZE_BYTES,
    maxActiveUploadsPerClient: MAX_ACTIVE_UPLOADS_PER_CLIENT,
    initRateLimitPerHour: INIT_RATE_LIMIT_PER_HOUR,
    requireOrigin: REQUIRE_ORIGIN,
    turnstileRequired: Boolean(TURNSTILE_SECRET),
    turnstileAllowedHostnames: TURNSTILE_SECRET ? TURNSTILE_ALLOWED_HOSTNAMES : [],
    n8nUploadWebhookConfigured: Boolean(N8N_UPLOAD_WEBHOOK_URL),
    n8nValidationWebhookConfigured: Boolean(N8N_VALIDATION_WEBHOOK_URL),
    n8nCanonWebhookConfigured: Boolean(N8N_CANON_WEBHOOK_URL),
    n8nRsidResolutionWebhookConfigured: Boolean(N8N_RSID_RESOLUTION_WEBHOOK_URL),
    n8nVcfCanonMatchWebhookConfigured: Boolean(N8N_VCF_CANON_MATCH_WEBHOOK_URL),
  });
});

app.get("/api/canon/current", async (_req, res) => {
  const current = await loadCurrentCanon().catch((error) => ({
    hasCanon: false,
    current: null,
    preview: { columns: [], rows: [] },
    error: error.message,
  }));
  res.json(current);
});

app.get("/api/canon/current/download", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const paths = canonPaths();
  const manifest = await loadCurrentCanonManifest().catch(() => null);
  if (!manifest) {
    res.status(404).json({ error: "No canon is currently loaded." });
    return;
  }

  const summaryPath = path.resolve(manifest.summaryPath || "");
  if (!isPathInside(paths.root, summaryPath)) {
    res.status(400).json({ error: "Current canon summary is outside the allowed root." });
    return;
  }

  const summary = JSON.parse(await readFile(summaryPath, "utf8"));
  const cleanRowsPath = path.resolve(summary.outputs?.cleanRowsCsv || "");
  if (!isPathInside(paths.root, cleanRowsPath)) {
    res.status(400).json({ error: "Current canon CSV is outside the allowed root." });
    return;
  }
  const cleanRowsStat = await stat(cleanRowsPath).catch(() => null);
  if (!cleanRowsStat || cleanRowsStat.size <= 0) {
    res.status(404).json({ error: "Current canon CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(summary.sourceFileName || "heal-canon").replace(/\.(csv|xlsx)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(cleanRowsPath, `${baseName}_clean_rows.csv`);
});

app.get("/api/canon/current/rsid-master", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const paths = canonPaths();
  const manifest = await loadCurrentCanonManifest().catch(() => null);
  if (!manifest) {
    res.status(404).json({ error: "No canon is currently loaded." });
    return;
  }

  const summaryPath = path.resolve(manifest.summaryPath || "");
  if (!isPathInside(paths.root, summaryPath)) {
    res.status(400).json({ error: "Current canon summary is outside the allowed root." });
    return;
  }

  const summary = JSON.parse(await readFile(summaryPath, "utf8"));
  const resolutionPaths = rsidResolutionPaths();
  const resolutionManifest = await loadCurrentRsidResolutionManifest().catch(() => null);
  let rsidMasterPath = "";
  let downloadSuffix = "rsid_master";
  if (resolutionManifest?.rsidMatchReadyCsv) {
    const resolvedPath = path.resolve(resolutionManifest.rsidMatchReadyCsv);
    if (isPathInside(resolutionPaths.root, resolvedPath)) {
      rsidMasterPath = resolvedPath;
      downloadSuffix = "rsid_master_resolved";
    }
  }
  if (!rsidMasterPath) {
    rsidMasterPath = path.resolve(summary.outputs?.rsidMasterCsv || "");
    if (!isPathInside(paths.root, rsidMasterPath)) {
      res.status(400).json({ error: "Current rsID master CSV is outside the allowed root." });
      return;
    }
  }
  const rsidMasterStat = await stat(rsidMasterPath).catch(() => null);
  if (!rsidMasterStat || rsidMasterStat.size <= 0) {
    res.status(404).json({ error: "Current rsID master CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(summary.sourceFileName || "heal-canon").replace(/\.(csv|xlsx)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(rsidMasterPath, `${baseName}_${downloadSuffix}.csv`);
});

app.post("/api/canon/upload", express.raw({ type: "*/*", limit: MAX_CANON_FILE_SIZE_BYTES }), async (req, res) => {
  const rawFileName = req.headers["x-canon-file-name"];
  const encodedFileName = Array.isArray(rawFileName) ? rawFileName[0] : rawFileName;
  const decodedFileName = (() => {
    try {
      return decodeURIComponent(String(encodedFileName || ""));
    } catch {
      return String(encodedFileName || "");
    }
  })();
  const fileName = safeFileName(decodedFileName);
  if (!isAllowedCanonName(fileName)) {
    res.status(400).json({ error: "Only .csv and .xlsx canon files are accepted." });
    return;
  }
  const body = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
  if (body.length <= 0) {
    res.status(400).json({ error: "Canon file is empty." });
    return;
  }
  if (body.length > MAX_CANON_FILE_SIZE_BYTES) {
    res.status(413).json({ error: "Canon file exceeds the configured maximum size." });
    return;
  }

  const turnstileToken = String(req.headers["x-turnstile-token"] || "");
  const turnstile = await verifyTurnstile(turnstileToken, clientIp(req));
  if (!turnstile.ok) {
    res.status(403).json({ error: turnstile.error });
    return;
  }

  await cleanupOldCanons();
  const paths = canonPaths();
  await mkdir(paths.incoming, { recursive: true });
  await mkdir(paths.runs, { recursive: true });
  const runId = crypto.randomUUID();
  const stagingDir = path.join(paths.incoming, runId);
  const outputDir = path.join(paths.runs, runId);
  const inputPath = path.join(stagingDir, fileName);
  await mkdir(stagingDir, { recursive: true });
  await mkdir(outputDir, { recursive: true });
  await writeFile(inputPath, body);

  try {
    const payload = {
      event: "heal.canon.sheet_intake.requested",
      runId,
      fileName,
      sizeBytes: body.length,
      inputPath,
      outputDir,
      requestedAt: new Date().toISOString(),
    };
    const summary = (await processCanonWithN8n(payload)) || (await runCanonProcessor(inputPath, outputDir, fileName));
    const rsidResolution = await resolveRsidForCanon(runId, summary);
    const current = await saveCurrentCanon(runId, fileName, summary);
    current.current.rsidResolution = {
      status: rsidResolution.summary.status,
      runId: rsidResolution.manifest.runId,
      metadata: rsidResolution.summary.metadata || {},
      createdAt: rsidResolution.manifest.createdAt,
    };
    res.status(201).json(current);
  } catch (error) {
    res.status(422).json({ error: error.message || String(error), runId });
  }
});

app.post("/api/uploads/lookup", async (req, res) => {
  await cleanupStaleUploads();

  const { fileName: rawFileName, sizeBytes: rawSizeBytes } = req.body || {};
  const fileName = safeFileName(rawFileName);
  const sizeBytes = Number(rawSizeBytes);
  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0 || !isAllowedVcfName(fileName)) {
    res.json({ match: null });
    return;
  }

  const upload = await findReusableUpload(fileName, sizeBytes, clientFingerprint(req));
  if (!upload) {
    res.json({ match: null });
    return;
  }

  res.json({
    match: {
      uploadId: upload.uploadId,
      fileName: upload.fileName,
      sizeBytes: upload.sizeBytes,
      createdAt: upload.createdAt,
      updatedAt: upload.updatedAt,
    },
  });
});

app.post("/api/uploads/init", async (req, res) => {
  await cleanupStaleUploads();

  const {
    fileName: rawFileName,
    sizeBytes: rawSizeBytes,
    contentType = "application/octet-stream",
    turnstileToken = "",
  } = req.body || {};
  const fingerprint = clientFingerprint(req);
  if (!checkInitRateLimit(req)) {
    res.status(429).json({ error: "Too many upload attempts. Try again later." });
    return;
  }
  if (activeUploadsForClient(fingerprint) >= MAX_ACTIVE_UPLOADS_PER_CLIENT) {
    res.status(429).json({ error: "Too many active uploads for this client." });
    return;
  }
  const turnstile = await verifyTurnstile(turnstileToken, clientIp(req));
  if (!turnstile.ok) {
    res.status(403).json({ error: turnstile.error });
    return;
  }

  const sizeBytes = Number(rawSizeBytes);
  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) {
    res.status(400).json({ error: "sizeBytes must be greater than zero." });
    return;
  }
  if (sizeBytes > MAX_FILE_SIZE_BYTES) {
    res.status(413).json({ error: "File exceeds the configured maximum size." });
    return;
  }

  const fileName = safeFileName(rawFileName);
  if (!isAllowedVcfName(fileName)) {
    res.status(400).json({ error: "Only .vcf and .vcf.gz files are accepted." });
    return;
  }
  const uploadId = crypto.randomUUID();
  const resolvedUploadRoot = path.resolve(UPLOAD_ROOT);
  const uploadDir = path.join(resolvedUploadRoot, uploadId);
  const storedPath = path.join(uploadDir, fileName);
  await mkdir(uploadDir, { recursive: true });

  const handle = await open(storedPath, "w");
  try {
    await handle.truncate(sizeBytes);
  } finally {
    await handle.close();
  }

  const totalChunks = Math.ceil(sizeBytes / CHUNK_SIZE_BYTES);
  const now = new Date().toISOString();
  const upload = {
    uploadId,
    fileName,
    originalFileName: String(rawFileName || fileName),
    contentType,
    sizeBytes,
    chunkSizeBytes: CHUNK_SIZE_BYTES,
    totalChunks,
    receivedChunks: Array(totalChunks).fill(false),
    clientFingerprint: fingerprint,
    uploadDir,
    storedPath,
    status: "initialized",
    createdAt: now,
    updatedAt: now,
  };
  await saveUpload(upload);
  res.status(201).json(publicUpload(upload));
});

app.put("/api/uploads/:uploadId/chunks/:chunkIndex", async (req, res) => {
  const upload = await loadUpload(req.params.uploadId);
  if (!upload) {
    res.status(404).json({ error: "Upload not found." });
    return;
  }
  if (upload.clientFingerprint && upload.clientFingerprint !== clientFingerprint(req)) {
    res.status(403).json({ error: "Upload belongs to a different client." });
    return;
  }
  if (upload.status === "complete") {
    res.status(409).json({ error: "Upload is already complete." });
    return;
  }

  const chunkIndex = Number.parseInt(req.params.chunkIndex, 10);
  if (!Number.isInteger(chunkIndex) || chunkIndex < 0 || chunkIndex >= upload.totalChunks) {
    res.status(400).json({ error: "Invalid chunk index." });
    return;
  }

  const start = chunkIndex * upload.chunkSizeBytes;
  const expectedBytes = Math.min(upload.chunkSizeBytes, upload.sizeBytes - start);
  const contentLength = Number(req.headers["content-length"] || "0");
  if (contentLength && contentLength !== expectedBytes) {
    res.status(400).json({ error: `Chunk ${chunkIndex} must be ${expectedBytes} bytes.` });
    return;
  }

  let receivedBytes = 0;
  const output = createWriteStream(upload.storedPath, { flags: "r+", start });
  req.on("data", (chunk) => {
    receivedBytes += chunk.length;
  });
  req.on("error", async () => {
    await unlink(upload.storedPath).catch(() => {});
  });
  output.on("error", () => {
    if (!res.headersSent) res.status(500).json({ error: "Could not write chunk." });
  });
  output.on("finish", async () => {
    if (receivedBytes !== expectedBytes) {
      res.status(400).json({ error: `Chunk ${chunkIndex} received ${receivedBytes} bytes, expected ${expectedBytes}.` });
      return;
    }
    upload.receivedChunks[chunkIndex] = true;
    upload.status = upload.receivedChunks.every(Boolean) ? "assembled" : "uploading";
    await saveUpload(upload);
    res.json(publicUpload(upload));
  });
  req.pipe(output);
});

app.post("/api/uploads/:uploadId/complete", async (req, res) => {
  const upload = await loadUpload(req.params.uploadId);
  if (!upload) {
    res.status(404).json({ error: "Upload not found." });
    return;
  }
  if (upload.clientFingerprint && upload.clientFingerprint !== clientFingerprint(req)) {
    res.status(403).json({ error: "Upload belongs to a different client." });
    return;
  }
  if (!upload.receivedChunks.every(Boolean)) {
    res.status(409).json({ error: "Upload still has missing chunks.", upload: publicUpload(upload) });
    return;
  }

  const fileStat = await stat(upload.storedPath);
  if (fileStat.size !== upload.sizeBytes) {
    res.status(409).json({ error: "Assembled file size does not match declared size." });
    return;
  }

  upload.status = "complete";
  await saveUpload(upload);
  await notifyN8nUpload(upload);
  res.json(publicUpload(upload));
});

app.get("/api/uploads/:uploadId", async (req, res) => {
  const upload = await loadUpload(req.params.uploadId);
  if (!upload) {
    res.status(404).json({ error: "Upload not found." });
    return;
  }
  if (upload.clientFingerprint && upload.clientFingerprint !== clientFingerprint(req)) {
    res.status(403).json({ error: "Upload belongs to a different client." });
    return;
  }
  res.json(publicUpload(upload));
});

app.post("/api/validations", async (req, res) => {
  const {
    uploadId,
    calculateChecksum = true,
    calculateStats = true,
    analysisMode = calculateStats ? "complete" : "quick",
    maxVariantsToCheck = 20,
  } = req.body || {};
  if (!uploadId) {
    res.status(400).json({ error: "uploadId is required." });
    return;
  }

  const upload = await loadUpload(uploadId);
  if (!upload) {
    res.status(404).json({ error: "Upload not found." });
    return;
  }
  if (upload.clientFingerprint && upload.clientFingerprint !== clientFingerprint(req)) {
    res.status(403).json({ error: "Upload belongs to a different client." });
    return;
  }
  if (upload.status !== "complete") {
    res.status(409).json({ error: "Upload must be complete before validation." });
    return;
  }

  const resolvedUploadRoot = path.resolve(UPLOAD_ROOT);
  const resolvedStoredPath = path.resolve(upload.storedPath);
  if (!isPathInside(resolvedUploadRoot, resolvedStoredPath)) {
    res.status(400).json({ error: "storedPath is outside the allowed upload root." });
    return;
  }

  const job = {
    id: crypto.randomUUID(),
    uploadId,
    fileName: upload.fileName,
    sizeBytes: null,
    status: "running",
    progress: 8,
    message: "Preparing validation",
    analysisMode,
    result: null,
    error: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  jobs.set(job.id, job);

  stat(resolvedStoredPath)
    .then((fileStat) => {
      job.sizeBytes = fileStat.size;
      job.progress = 18;
      job.message = "Checking VCF headers";
      job.updatedAt = new Date().toISOString();

      const args = [
        VALIDATOR_SCRIPT,
        "--path",
        resolvedStoredPath,
        "--allowed-root",
        resolvedUploadRoot,
        "--max-variants",
        String(Math.min(100, Math.max(1, Number.parseInt(maxVariantsToCheck, 10) || 20))),
      ];
      if (calculateChecksum) args.push("--checksum");
      if (calculateStats) args.push("--stats");

      const child = spawn(PYTHON_EXE, args, {
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
      let stdout = "";
      let stderr = "";

      const timer = setInterval(() => {
        if (job.status !== "running") {
          clearInterval(timer);
          return;
        }
        job.progress = Math.min(92, job.progress + 3);
        job.message = job.progress < 50 ? "Streaming validation" : "Calculating metrics and checksum";
        job.updatedAt = new Date().toISOString();
      }, 650);

      child.stdout.on("data", (chunk) => {
        stdout += chunk.toString("utf8");
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString("utf8");
      });
      child.on("error", (error) => {
        clearInterval(timer);
        job.status = "failed";
        job.progress = 100;
        job.error = error.message;
        job.message = "Validator failed to start";
        job.updatedAt = new Date().toISOString();
      });
      child.on("close", async () => {
        clearInterval(timer);
        try {
          const result = sanitizeValidationResult(JSON.parse(stdout.trim()), upload);
          job.result = result;
          job.status = "complete";
          job.progress = 100;
          job.message =
            result.status === "valid"
              ? "Validation passed"
              : result.status === "warning"
                ? "Validation completed with warnings"
                : "Validation failed";
          await notifyN8nValidation(job, upload);
        } catch (error) {
          job.status = "failed";
          job.progress = 100;
          job.error = `Could not parse validator output: ${error.message}`;
          job.result = { stdout, stderr };
          job.message = "Validation failed";
        }
        job.updatedAt = new Date().toISOString();
      });
    })
    .catch((error) => {
      job.status = "failed";
      job.progress = 100;
      job.error = error.message;
      job.message = "Uploaded file was not accessible";
      job.updatedAt = new Date().toISOString();
    });

  res.status(202).json(publicJob(job));
});

app.get("/api/validations/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "Validation job not found." });
    return;
  }
  res.json(publicJob(job));
});

app.post("/api/vcf-canon-matches", async (req, res) => {
  const { uploadId } = req.body || {};
  if (!uploadId) {
    res.status(400).json({ error: "uploadId is required." });
    return;
  }

  const upload = await loadUpload(uploadId);
  if (!upload) {
    res.status(404).json({ error: "Upload not found." });
    return;
  }
  if (upload.clientFingerprint && upload.clientFingerprint !== clientFingerprint(req)) {
    res.status(403).json({ error: "Upload belongs to a different client." });
    return;
  }
  if (upload.status !== "complete") {
    res.status(409).json({ error: "Upload must be complete before VCF-canon matching." });
    return;
  }

  const resolvedUploadRoot = path.resolve(UPLOAD_ROOT);
  const resolvedStoredPath = path.resolve(upload.storedPath);
  if (!isPathInside(resolvedUploadRoot, resolvedStoredPath)) {
    res.status(400).json({ error: "storedPath is outside the allowed upload root." });
    return;
  }

  const canonRoot = path.resolve(CANON_ROOT);
  const canonManifest = await loadCurrentCanonManifest().catch(() => null);
  if (!canonManifest) {
    res.status(409).json({ error: "No canon is currently loaded." });
    return;
  }
  const canonSummaryPath = path.resolve(canonManifest.summaryPath || "");
  if (!isPathInside(canonRoot, canonSummaryPath)) {
    res.status(400).json({ error: "Current canon summary is outside the allowed root." });
    return;
  }
  const canonSummary = JSON.parse(await readFile(canonSummaryPath, "utf8"));
  const canonCleanPath = path.resolve(canonSummary.outputs?.cleanRowsCsv || "");
  if (!isPathInside(canonRoot, canonCleanPath)) {
    res.status(400).json({ error: "Current canon clean CSV is outside the allowed root." });
    return;
  }

  const resolutionPaths = rsidResolutionPaths();
  const resolutionManifest = await loadCurrentRsidResolutionManifest().catch(() => null);
  const rsidReadyPath = path.resolve(resolutionManifest?.rsidMatchReadyCsv || "");
  if (!resolutionManifest || !isPathInside(resolutionPaths.root, rsidReadyPath)) {
    res.status(409).json({ error: "The current canon does not have an rsID match-ready file yet." });
    return;
  }

  const job = {
    id: crypto.randomUUID(),
    uploadId,
    fileName: upload.fileName,
    sizeBytes: upload.sizeBytes,
    status: "running",
    progress: 8,
    message: "Preparing VCF-canon match",
    result: null,
    error: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  jobs.set(job.id, job);

  (async () => {
    try {
      const paths = vcfCanonMatchPaths();
      await mkdir(paths.runs, { recursive: true });
      const runId = crypto.randomUUID();
      const outputDir = path.join(paths.runs, runId);
      await mkdir(outputDir, { recursive: true });
      job.progress = 25;
      job.message = "Streaming VCF and matching canon targets";
      job.updatedAt = new Date().toISOString();

      const payload = {
        event: "heal.vcf_canon_match.requested",
        runId,
        uploadId: upload.uploadId,
        fileName: upload.fileName,
        canonRunId: canonManifest.runId,
        rsidResolutionRunId: resolutionManifest.runId,
        canonCleanPath,
        rsidReadyPath,
        vcfPath: resolvedStoredPath,
        outputDir,
        requestedAt: new Date().toISOString(),
      };
      const summary = await processVcfCanonMatch(payload);
      job.result = sanitizeVcfCanonMatchResult(summary, upload);
      job.status = "complete";
      job.progress = 100;
      job.message = "VCF-canon match completed";
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.error = error.message || String(error);
      job.message = "VCF-canon match failed";
    } finally {
      job.updatedAt = new Date().toISOString();
    }
  })();

  res.status(202).json(publicJob(job));
});

app.get("/api/vcf-canon-matches/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  res.json(publicJob(job));
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`HEAL local API listening on http://127.0.0.1:${PORT}`);
  console.log(`Upload root: ${UPLOAD_ROOT}`);
  console.log(`Chunk size: ${CHUNK_SIZE_BYTES}`);
});
