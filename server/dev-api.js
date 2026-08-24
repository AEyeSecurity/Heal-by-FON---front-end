import express from "express";
import { createWriteStream, existsSync } from "node:fs";
import { appendFile, mkdir, open, readFile, readdir, rename, rm, stat, statfs, unlink, utimes, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import {
  APP_ROOT,
  BACKUP_ROOT,
  CONFIG_ROOT,
  DATA_ROOT,
  HEAL_HOME,
  LOG_ROOT,
  RUNTIME_PATHS,
  SERVICE_SCRIPTS,
} from "./heal-runtime.js";

const app = express();
const PORT = Number(process.env.HEAL_API_PORT || 8787);
const UPLOAD_ROOT = RUNTIME_PATHS.uploads;
const VALIDATOR_SCRIPT = SERVICE_SCRIPTS.validator;
const CANON_ROOT = RUNTIME_PATHS.canon;
const CANON_PROCESSOR_SCRIPT = SERVICE_SCRIPTS.canonProcessor;
const RSID_RESOLUTION_ROOT = RUNTIME_PATHS.legacyRsid;
const VCF_CANON_MATCH_ROOT = RUNTIME_PATHS.match;
const VCF_NORMALIZATION_ROOT = RUNTIME_PATHS.normalization;
const REFERENCE_DATA_ROOT = RUNTIME_PATHS.references;
const GRCH38_REFERENCE_FASTA =
  process.env.HEAL_GRCH38_REFERENCE_FASTA || path.join(REFERENCE_DATA_ROOT, "GRCh38", "hg38.fa");
const GRCH38_REFERENCE_MANIFEST =
  process.env.HEAL_GRCH38_REFERENCE_MANIFEST || path.join(REFERENCE_DATA_ROOT, "GRCh38", "reference_manifest.json");
const GRCH37_REFERENCE_FASTA = process.env.HEAL_GRCH37_REFERENCE_FASTA || "";
const GRCH37_REFERENCE_MANIFEST = process.env.HEAL_GRCH37_REFERENCE_MANIFEST || "";
const MATCH_PREPARATION_ROOT = RUNTIME_PATHS.preparation;
const AI_TRIAGE_ROOT = RUNTIME_PATHS.triage;
const VARIANT_ENRICHMENT_ROOT = RUNTIME_PATHS.enrichment;
const EVIDENCE_REFINEMENT_ROOT = RUNTIME_PATHS.evidenceRefinement;
const GROUPED_INTERPRETATION_PREP_ROOT = RUNTIME_PATHS.groupedPrep;
const GROUPED_INDIVIDUAL_INTERPRETATION_ROOT = RUNTIME_PATHS.groupedInterpretation;
const GROUPED_PROTOTYPE_ROOT = RUNTIME_PATHS.groupedPrototype;
const INDIVIDUAL_INTERPRETATION_ROOT = RUNTIME_PATHS.individualInterpretation;
const INTERPRETATION_NORMALIZATION_ROOT = RUNTIME_PATHS.interpretationNormalization;
const GLOBAL_INTERPRETATION_ROOT = RUNTIME_PATHS.globalInterpretation;
const FINAL_REPORT_ROOT = RUNTIME_PATHS.finalReport;
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
const ALLOWED_VCF_PARSERS = new Set(["streaming", "pysam"]);
const REQUIRE_ORIGIN = process.env.HEAL_REQUIRE_ORIGIN !== "false";
const N8N_UPLOAD_WEBHOOK_URL = process.env.HEAL_N8N_UPLOAD_WEBHOOK_URL || "";
const N8N_VALIDATION_WEBHOOK_URL =
  process.env.HEAL_N8N_VALIDATION_WEBHOOK_URL || process.env.HEAL_N8N_WEBHOOK_URL || "";
const N8N_CANON_WEBHOOK_URL = process.env.HEAL_N8N_CANON_WEBHOOK_URL || "";
const N8N_RSID_RESOLUTION_WEBHOOK_URL = process.env.HEAL_N8N_RSID_RESOLUTION_WEBHOOK_URL || "";
const N8N_VCF_CANON_MATCH_WEBHOOK_URL = process.env.HEAL_N8N_VCF_CANON_MATCH_WEBHOOK_URL || "";
const N8N_VARIANT_ENRICHMENT_WEBHOOK_URL = process.env.HEAL_N8N_VARIANT_ENRICHMENT_WEBHOOK_URL || "";
const N8N_INDIVIDUAL_INTERPRETATION_WEBHOOK_URL =
  process.env.HEAL_N8N_INDIVIDUAL_INTERPRETATION_WEBHOOK_URL || "";
const N8N_GLOBAL_INTERPRETATION_WEBHOOK_URL = process.env.HEAL_N8N_GLOBAL_INTERPRETATION_WEBHOOK_URL || "";
const N8N_WEBHOOK_TOKEN = process.env.HEAL_N8N_WEBHOOK_TOKEN || "";
const LLM1_MODEL = process.env.HEAL_LLM1_MODEL || "gpt-5-mini";
const LLM1_PROMPT_PROFILE = process.env.HEAL_LLM1_PROMPT_PROFILE || "default_v6";
const LLM1_REASONING_EFFORT = process.env.HEAL_LLM1_REASONING_EFFORT || (LLM1_MODEL === "gpt-5-mini" ? "minimal" : "none");
const LLM2_QUICK_MODEL = process.env.HEAL_LLM2_QUICK_MODEL || "gpt-5-mini";
const LLM2_FULL_MODEL = process.env.HEAL_LLM2_FULL_MODEL || "gpt-5.2";
const LLM2_QA_DEFAULT_MODEL = process.env.HEAL_LLM2_QA_DEFAULT_MODEL || "gpt-5-mini";
const ALLOWED_LLM2_MODELS = new Set(
  (process.env.HEAL_LLM2_ALLOWED_MODELS || "gpt-5-mini,gpt-5,gpt-5.1,gpt-5.2")
    .split(",")
    .map((model) => model.trim())
    .filter(Boolean),
);
const ALLOW_LLM_DRY_RUN = process.env.HEAL_ALLOW_LLM_DRY_RUN === "true";
const HEAL_V2_LLM1_ENABLED = process.env.HEAL_V2_LLM1_ENABLED === "true";
const HEAL_GROUPED_PROTOTYPE_ENABLED = process.env.HEAL_GROUPED_PROTOTYPE_ENABLED === "true";
const HEAL_PROTOTYPE_SNAPSHOT_PATH =
  process.env.HEAL_PROTOTYPE_SNAPSHOT_PATH ||
  path.join(DATA_ROOT, "prototype", "tier1-105-human-signed-20260823", "tier1_prototype_snapshot_v1_human_signed.json");
const HEAL_PROTOTYPE_CANDIDATE_MANIFEST_PATH =
  process.env.HEAL_PROTOTYPE_CANDIDATE_MANIFEST_PATH ||
  path.join(DATA_ROOT, "prototype", "tier1-105-human-signed-20260823", "prototype_candidate_manifest.json");
const HEAL_PROTOTYPE_COVERAGE_MANIFEST_PATH =
  process.env.HEAL_PROTOTYPE_COVERAGE_MANIFEST_PATH ||
  path.join(DATA_ROOT, "prototype", "tier1-105-human-signed-20260823", "prototype_coverage_manifest_v1.json");
const HEAL_PROTOTYPE_LLM1_MODEL = process.env.HEAL_PROTOTYPE_LLM1_MODEL || "gpt-5.6-luna";
const HEAL_PROTOTYPE_LLM2_MODEL = process.env.HEAL_PROTOTYPE_LLM2_MODEL || "gpt-5.6-luna";
const HEAL_PROTOTYPE_EXECUTION_MODE = process.env.HEAL_PROTOTYPE_EXECUTION_MODE || "disabled";
const HEAL_PROTOTYPE_MAX_ESTIMATED_COST_USD = Number.parseFloat(process.env.HEAL_PROTOTYPE_MAX_ESTIMATED_COST_USD || "5") || 5;
const HEAL_PROTOTYPE_HARD_CAP_USD = Number.parseFloat(process.env.HEAL_PROTOTYPE_HARD_CAP_USD || "10") || 10;
const HEAL_V2_LLM1_PILOT_ENABLED = process.env.HEAL_V2_LLM1_PILOT_ENABLED === "true";
const HEAL_LLM1_EXECUTION_MODE = process.env.HEAL_LLM1_EXECUTION_MODE || "disabled";
const HEAL_LLM1_ACTIVE_TIERS = process.env.HEAL_LLM1_ACTIVE_TIERS || "T1";
const HEAL_LLM1_ACTIVE_AGE_BANDS = process.env.HEAL_LLM1_ACTIVE_AGE_BANDS || "age_0_7";
const HEAL_LLM1_EXPERIMENTAL_CANARIES = process.env.HEAL_LLM1_EXPERIMENTAL_CANARIES || "IFNG:T3.5";
const HEAL_LLM1_CURATION_SNAPSHOT_ID =
  process.env.HEAL_LLM1_CURATION_SNAPSHOT_ID || "internal-scientific-curation-20260728";
const HEAL_V2_EVIDENCE_DIGEST_ENABLED = process.env.HEAL_V2_EVIDENCE_DIGEST_ENABLED === "true";
const HEAL_V2_EVIDENCE_DIGEST_MODEL = process.env.HEAL_V2_EVIDENCE_DIGEST_MODEL || "";
const HEAL_CURATION_ACCESS_TOKEN = process.env.HEAL_CURATION_ACCESS_TOKEN || "";
const HEAL_TIER1_CURATION_V2_ENABLED = process.env.HEAL_TIER1_CURATION_V2_ENABLED === "true";
const HEAL_TIER1_CURATION_V2_ROOT = RUNTIME_PATHS.tier1CurationV2;
const HEAL_TIER1_CURATION_V2_MODEL = process.env.HEAL_TIER1_CURATION_V2_MODEL || "gpt-5.6-sol";
const HEAL_TIER1_CURATION_V2_CUTOFF = process.env.HEAL_TIER1_CURATION_V2_CUTOFF || "2026-07-28";
const HEAL_TIER1_CURATION_V2_CANON_RUN_ID = process.env.HEAL_TIER1_CURATION_V2_CANON_RUN_ID || "";
const HEAL_TIER1_HUMAN_REVIEW_FOUNDATION =
  process.env.HEAL_TIER1_HUMAN_REVIEW_FOUNDATION ||
  path.join(DATA_ROOT, "curation-candidates", "tier1-human-review-20260810", "foundation-v1", "tier1_human_review_foundation.json");
const HEAL_MECHANISM_REGISTRY_PATH =
  process.env.HEAL_MECHANISM_REGISTRY_PATH || path.join(RUNTIME_PATHS.canonCuration, "mechanism_registry_v1.csv");
const HEAL_GWAS_TRAIT_MODULE_MAP_PATH =
  process.env.HEAL_GWAS_TRAIT_MODULE_MAP_PATH || path.join(RUNTIME_PATHS.canonCuration, "gwas_module_relevance_registry_v1.csv");
const HEAL_V2_MIN_VEP_COVERAGE = Math.min(
  1,
  Math.max(0, Number.parseFloat(process.env.HEAL_V2_MIN_VEP_COVERAGE || "0.90") || 0.90),
);
const MAINTENANCE_MODE = process.env.HEAL_MAINTENANCE_MODE === "true";
const DEPLOYMENT_SHA = process.env.HEAL_DEPLOYMENT_SHA || "unknown";
const NORMALIZER_IMAGE = process.env.HEAL_VCF_NORMALIZER_IMAGE || "heal-vcf-normalizer:1.0.0";
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
const tier1CurationV2Jobs = new Map();
const jobLogQueues = new Map();
const canonJobs = new Map();
const uploads = new Map();
const initRateLimits = new Map();
const CANON_STAGE_ORDER = [
  "schema_detection",
  "row_normalization",
  "gene_resolution",
  "artifact_build",
  "activation",
];

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
    "Content-Type, X-Chunk-Index, X-Upload-Id, X-Canon-File-Name, X-Canon-Assembly, X-Turnstile-Token, X-HEAL-Access-Token, X-HEAL-Curation-Token",
  );
  if (req.method === "OPTIONS") {
    res.status(204).send();
    return;
  }
  if (MAINTENANCE_MODE && !["GET", "OPTIONS"].includes(req.method)) {
    res.status(503).json({ error: "HEAL is temporarily in maintenance mode. Please retry shortly." });
    return;
  }
  next();
});

// Professional GWAS curation registries can exceed 1 MB while remaining bounded CSV inputs.
app.use(express.json({ limit: "4mb" }));

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
    runs: root,
    jobs: path.resolve(RUNTIME_PATHS.jobs),
  };
}

function vcfNormalizationPaths() {
  const root = path.resolve(VCF_NORMALIZATION_ROOT);
  return {
    root,
    runs: root,
  };
}

function matchPreparationPaths() {
  const root = path.resolve(MATCH_PREPARATION_ROOT);
  return {
    root,
    runs: root,
  };
}

function variantEnrichmentPaths() {
  const root = path.resolve(VARIANT_ENRICHMENT_ROOT);
  const cache = path.resolve(RUNTIME_PATHS.enrichmentCache);
  return {
    root,
    runs: root,
    cache,
    cacheV2: path.join(cache, "enrichment_cache_v2.sqlite"),
    legacyCache: path.join(cache, "enrichment_cache.sqlite"),
  };
}

function evidenceRefinementPaths() {
  const root = path.resolve(EVIDENCE_REFINEMENT_ROOT);
  const cache = path.resolve(RUNTIME_PATHS.enrichmentCache);
  return {
    root,
    runs: root,
    cache,
    cachePath: path.join(cache, "evidence_refinement_cache.sqlite"),
  };
}

function aiTriagePaths() {
  const root = path.resolve(AI_TRIAGE_ROOT);
  return {
    root,
    runs: root,
  };
}

function groupedInterpretationPrepPaths() {
  const root = path.resolve(GROUPED_INTERPRETATION_PREP_ROOT);
  return {
    root,
    runs: root,
  };
}

function groupedIndividualInterpretationPaths() {
  const root = path.resolve(GROUPED_INDIVIDUAL_INTERPRETATION_ROOT);
  return {
    root,
    runs: root,
  };
}

function individualInterpretationPaths() {
  const root = path.resolve(INDIVIDUAL_INTERPRETATION_ROOT);
  return {
    root,
    runs: root,
  };
}

function interpretationNormalizationPaths() {
  const root = path.resolve(INTERPRETATION_NORMALIZATION_ROOT);
  return {
    root,
    runs: root,
  };
}

function globalInterpretationPaths() {
  const root = path.resolve(GLOBAL_INTERPRETATION_ROOT);
  return {
    root,
    runs: root,
  };
}

function finalReportPaths() {
  const root = path.resolve(FINAL_REPORT_ROOT);
  return {
    root,
    runs: root,
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

function requestAccessToken(req) {
  return String(req.headers["x-heal-access-token"] || req.body?.accessToken || req.query?.accessToken || "");
}

function tokenMatches(expected, actual) {
  if (!expected || !actual) return false;
  const expectedBuffer = Buffer.from(String(expected));
  const actualBuffer = Buffer.from(String(actual));
  return expectedBuffer.length === actualBuffer.length && crypto.timingSafeEqual(expectedBuffer, actualBuffer);
}

function canAccessUpload(req, upload) {
  if (!upload) return false;
  if (tokenMatches(upload.accessToken, requestAccessToken(req))) return true;
  return !upload.clientFingerprint || upload.clientFingerprint === clientFingerprint(req);
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

function resolveStoredPath(root, storedPath) {
  if (!storedPath) return "";
  return path.resolve(path.isAbsolute(storedPath) ? storedPath : path.join(root, storedPath));
}

function jobStageDirectory(jobId, stage) {
  return path.join(path.resolve(RUNTIME_PATHS.runs), safeFileName(jobId), safeFileName(stage));
}

function storeRelativePath(root, candidatePath) {
  if (!candidatePath) return "";
  const resolved = path.resolve(candidatePath);
  return isPathInside(root, resolved) ? path.relative(root, resolved) : resolved;
}

function hydrateJobArtifacts(job) {
  if (!job?.artifacts || typeof job.artifacts !== "object") return job;
  for (const [key, value] of Object.entries(job.artifacts)) {
    if (typeof value === "string" && value) {
      job.artifacts[key] = resolveStoredPath(DATA_ROOT, value);
    }
  }
  return job;
}

function serializeJobForStorage(job) {
  const stored = JSON.parse(JSON.stringify(job));
  if (!stored?.artifacts || typeof stored.artifacts !== "object") return stored;
  for (const [key, value] of Object.entries(stored.artifacts)) {
    if (typeof value === "string" && value) {
      stored.artifacts[key] = storeRelativePath(DATA_ROOT, value);
    }
  }
  return stored;
}

function manifestPath(uploadDir) {
  return path.join(uploadDir, "upload.json");
}

function publicUpload(upload) {
  const receivedChunks = upload.receivedChunks.filter(Boolean).length;
  return {
    uploadId: upload.uploadId,
    accessToken: upload.accessToken || null,
    fileName: upload.fileName,
    sizeBytes: upload.sizeBytes,
    chunkSizeBytes: upload.chunkSizeBytes,
    totalChunks: upload.totalChunks,
    receivedChunks,
    uploadedBytes: Math.min(upload.sizeBytes, receivedChunks * upload.chunkSizeBytes),
    progress: upload.totalChunks > 0 ? Math.round((receivedChunks / upload.totalChunks) * 100) : 0,
    status: upload.status,
    validation: upload.validation || null,
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
      schemaVersion: summary.schemaVersion || manifest?.schemaVersion || null,
      adapter: summary.adapter || manifest?.adapter || null,
      assembly: summary.assembly || manifest?.assembly || null,
      activationStatus: summary.activationStatus || manifest?.activationStatus || null,
      warningsSummary: summary.warningsSummary || manifest?.warningsSummary || {},
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

function publicCanonJob(job) {
  return {
    id: job.id,
    status: job.status,
    progress: job.progress,
    message: job.message,
    sourceFileName: job.sourceFileName,
    assembly: job.assembly,
    schemaDetected: job.schemaDetected || null,
    stages: CANON_STAGE_ORDER.map((key) => job.stages?.[key]).filter(Boolean),
    result: job.result || null,
    error: job.error || null,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
  };
}

function createCanonStageState() {
  return {
    schema_detection: { key: "schema_detection", status: "pending", progress: 0, message: "" },
    row_normalization: { key: "row_normalization", status: "pending", progress: 0, message: "" },
    gene_resolution: { key: "gene_resolution", status: "pending", progress: 0, message: "" },
    artifact_build: { key: "artifact_build", status: "pending", progress: 0, message: "" },
    activation: { key: "activation", status: "pending", progress: 0, message: "" },
  };
}

function updateCanonStage(job, stageKey, patch) {
  if (!job.stages) {
    job.stages = createCanonStageState();
  }
  const current = job.stages[stageKey] || { key: stageKey, status: "pending", progress: 0, message: "" };
  const next = {
    ...current,
    ...patch,
    key: stageKey,
  };
  next.progress = Math.max(0, Math.min(100, Number(next.progress || 0)));
  next.message = next.message || current.message || "";
  job.stages[stageKey] = next;
}

async function refreshCanonJobProgress(job) {
  if (!job?.progressPath || job.status !== "running") return;
  const raw = await readFile(job.progressPath, "utf8").catch(() => null);
  if (!raw) return;
  let payload = null;
  try {
    payload = JSON.parse(raw);
  } catch {
    return;
  }
  if (payload.schemaVersion && !job.schemaDetected) {
    job.schemaDetected = payload.schemaVersion;
  }
  if (payload.message) {
    job.message = payload.message;
  }
  if (payload.stages && typeof payload.stages === "object") {
    for (const stageKey of Object.keys(payload.stages)) {
      if (!CANON_STAGE_ORDER.includes(stageKey)) continue;
      updateCanonStage(job, stageKey, payload.stages[stageKey]);
    }
  }
  const progressValue = Number(payload.progress || 0);
  if (Number.isFinite(progressValue) && progressValue > job.progress) {
    job.progress = Math.min(98, progressValue);
  }
  job.updatedAt = new Date().toISOString();
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

function sanitizeVcfNormalizationResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.normalizedVcfPath;
  delete publicResult.normalizedVariantsCsv;
  delete publicResult.normalizationExcludedAuditCsv;
  delete publicResult.normalizationSummaryJson;
  if (publicResult.bcftools) delete publicResult.bcftools.command;
  return publicResult;
}

function sanitizeMatchPreparationResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeVariantEnrichmentResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.cacheDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeEvidenceRefinementResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPaths;
  delete publicResult.outputDir;
  delete publicResult.cachePath;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeAiTriageResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeGroupedInterpretationPrepResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeGroupedIndividualInterpretationResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeIndividualInterpretationResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeInterpretationNormalizationResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeGlobalInterpretationResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function sanitizeFinalReportResult(result) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  delete publicResult.inputPath;
  delete publicResult.outputDir;
  delete publicResult.outputs;
  return publicResult;
}

function publicArtifactsReady(job) {
  const artifacts = job.artifacts || {};
  const artifactExists = (value) => Boolean(value && existsSync(value));
  return {
    matches: artifactExists(artifacts.sheetFinalConsolidatedCsv),
    normalization: artifactExists(artifacts.normalizedVariantsCsv || artifacts.normalizedVcfPath),
    normalizationAudit: artifactExists(artifacts.normalizationExcludedAuditCsv),
    debug: Boolean(
      artifactExists(artifacts.vcfCandidatesCsv) ||
        artifactExists(artifacts.sheetFinalMatchStrictCsv) ||
        artifactExists(artifacts.sheetFinalMatchLikelyNeedsAltReviewCsv) ||
        artifactExists(artifacts.sheetFinalMatchByPositionNeedsReviewCsv) ||
        artifactExists(artifacts.sheetFinalNoVcfMatchByChrPosCsv),
    ),
    preparation: artifactExists(artifacts.deliverableAuditCsv || artifacts.deliverableMinCsv),
    aiTriage: artifactExists(artifacts.aiTriageCsv),
    enrichment: artifactExists(artifacts.observedVariantEnrichmentCsv),
    enrichmentInterpretive: artifactExists(artifacts.observedVariantInterpretiveCsv),
    enrichmentPlus: artifactExists(artifacts.observedVariantEnrichmentPlusCsv),
    enrichmentQuality: artifactExists(artifacts.enrichmentQualitySummaryJson),
    enrichmentVepBase: artifactExists(artifacts.v2EnrichmentVepBaseCsv),
    enrichmentResolutionAudit: artifactExists(artifacts.v2EnrichmentResolutionAuditJsonl),
    enrichmentComplete: artifactExists(artifacts.v2EnrichmentCompleteCsv),
    enrichmentVepOnly: artifactExists(artifacts.v2EnrichmentVepOnlyAuditCsv),
    enrichmentPhysicalMatrix: artifactExists(artifacts.v2EnrichmentPhysicalMatrixCsv),
    enrichmentPhysicalEvidenceAudit: artifactExists(artifacts.v2EnrichmentPhysicalEvidenceAuditJsonlGz),
    enrichmentModuleProjection: artifactExists(artifacts.v2EnrichmentModuleProjectionCsv),
    enrichmentRetryQueue: artifactExists(artifacts.enrichmentRetryQueueJsonl),
    enrichmentIdentitySummary: artifactExists(artifacts.enrichmentIdentityResolutionSummaryJson),
    enrichmentPerformance: artifactExists(artifacts.enrichmentPerformanceSummaryJson),
    curatedPhysicalMatrix: artifactExists(artifacts.curatedPhysicalMatrixCsv),
    curatedPhysicalRegistry: artifactExists(artifacts.curatedPhysicalRegistryCsv),
    curatedModuleProjection: artifactExists(artifacts.curatedGeneModuleProjectionCsv),
    canonicalGeneModuleStatus: artifactExists(artifacts.canonicalGeneModuleStatusCsv),
    clinvarAggregate: artifactExists(artifacts.clinvarVariantAggregateCsv),
    clinvarAssertions: artifactExists(artifacts.clinvarSubmitterAssertionsCsv),
    clinpgxClinicalAnnotations: artifactExists(artifacts.clinpgxClinicalAnnotationsCsv),
    clinpgxVariantAnnotations: artifactExists(artifacts.clinpgxVariantAnnotationsCsv),
    gwasAssociations: artifactExists(artifacts.gwasVariantAssociationsCsv),
    gwasVariantTraitSummary: artifactExists(artifacts.gwasVariantTraitSummaryCsv),
    gwasGeneModuleSummary: artifactExists(artifacts.gwasGeneModuleSummaryCsv),
    gwasEvidenceClusters: artifactExists(artifacts.gwasEvidenceClustersCsv),
    gwasTraitModuleRelevanceTemplate: artifactExists(artifacts.gwasTraitModuleRelevanceTemplateCsv),
    gwasMetadataRetryQueue: artifactExists(artifacts.gwasMetadataRetryQueueJsonl),
    publicationEvidence: artifactExists(artifacts.publicationEvidenceCsv),
    evidenceRefinementRaw: artifactExists(artifacts.evidenceRefinementRawJsonlGz),
    evidenceRefinementRetryQueue: artifactExists(artifacts.evidenceRefinementRetryQueueJsonl),
    evidenceRefinementSummary: artifactExists(artifacts.evidenceRefinementSummaryJson),
    groupedPayloads: artifactExists(artifacts.groupPayloadsCsv || artifacts.groupPayloadsJsonl),
    groupedPayloadsV4: artifactExists(artifacts.groupPayloadsCsvV4 || artifacts.groupPayloadsJsonlV4),
    groupedPayloadsV5: artifactExists(artifacts.groupPayloadsCsvV5 || artifacts.groupPayloadsJsonlV5),
    groupedPayloadsV6: artifactExists(artifacts.groupPayloadsCsvV6 || artifacts.groupPayloadsJsonlV6),
    groupedPayloadsV7: artifactExists(artifacts.groupPayloadsCsvV7 || artifacts.groupPayloadsJsonlV7),
    groupEvidencePackets: artifactExists(artifacts.groupEvidencePacketsJsonlGz),
    groupEvidenceDigests: artifactExists(artifacts.groupEvidenceDigestsJsonl),
    groupEvidenceDigestErrors: artifactExists(artifacts.groupEvidenceDigestErrorsCsv),
    groupTokenBudgetAudit: artifactExists(artifacts.groupTokenBudgetAuditCsv),
    groupEvidenceCoverageAudit: artifactExists(artifacts.groupEvidenceCoverageAuditCsv),
    groupCompressionErrors: artifactExists(artifacts.groupCompressionErrorsCsv),
    groupCompressionSummary: artifactExists(artifacts.groupCompressionSummaryJson),
    groupPayloadSchemaV5: artifactExists(artifacts.groupPayloadSchemaV5Json),
    groupPayloadSchemaV6: artifactExists(artifacts.groupPayloadSchemaV6Json),
    groupPayloadSchemaV7: artifactExists(artifacts.groupPayloadSchemaV7Json),
    groupPreflightV7: artifactExists(artifacts.groupPreflightV7Csv),
    groupPayloadV7Summary: artifactExists(artifacts.groupPayloadV7SummaryJson),
    groupCardsV7: artifactExists(artifacts.groupCardsJson || artifacts.groupCardsV7Json),
    groupQuarantine: artifactExists(artifacts.groupQuarantineJsonl),
    llm1InternalReview: artifactExists(artifacts.llm1InternalReviewJson),
    targetGeneConsequenceAudit: artifactExists(artifacts.targetGeneConsequenceAuditCsv),
    alleleSpecificFrequencyAudit: artifactExists(artifacts.alleleSpecificFrequencyAuditCsv),
    clinvarConditionConflictAudit: artifactExists(artifacts.clinvarConditionConflictAuditCsv),
    groupTokenBudgetAuditV6: artifactExists(artifacts.groupTokenBudgetAuditV6Csv),
    groupPayloadV6Summary: artifactExists(artifacts.groupPayloadV6SummaryJson),
    groupPayloadV6Errors: artifactExists(artifacts.groupPayloadV6ErrorsCsv),
    persistentMechanismRegistry: artifactExists(artifacts.persistentMechanismRegistryCsv),
    persistentGwasRegistry: artifactExists(artifacts.persistentGwasRegistryCsv),
    mechanismRegistry: artifactExists(artifacts.mechanismRegistryV1Csv),
    llm1PilotManifest: artifactExists(artifacts.llm1PilotManifestCsv),
    llm1PilotCandidateManifestV2: artifactExists(artifacts.llm1PilotCandidateManifestV2Csv),
    llm1PilotCandidateManifestV3: artifactExists(artifacts.llm1PilotCandidateManifestV3Csv),
    groupedVariantDetail: artifactExists(artifacts.groupVariantDetailCsv),
    groupedInterpretation: artifactExists(artifacts.groupInterpretationsCsv),
    llm1PilotApprovedPayloads: artifactExists(artifacts.llm1PilotApprovedPayloadsJsonl),
    groupedInterpretationRawResponses: artifactExists(artifacts.groupInterpretationRawResponsesJsonl),
    groupedInterpretationCallAudit: artifactExists(artifacts.groupInterpretationCallAuditCsv),
    llm1PilotPromptSnapshot: artifactExists(artifacts.llm1PilotPromptSnapshotMd),
    llm1PilotResponseSchemaSnapshot: artifactExists(artifacts.llm1PilotResponseSchemaSnapshotJson),
    individualInterpretation: artifactExists(artifacts.individualVariantInterpretationsCsv),
    interpretationNormalization: artifactExists(artifacts.individualVariantInterpretationsNormalizedCsv),
    globalInterpretation: artifactExists(artifacts.globalInterpretationJson || artifacts.globalInterpretationSectionsCsv),
    finalReport: artifactExists(artifacts.finalReportDocx),
    groupedPrototype: artifactExists(artifacts.groupedPrototypeSummaryJson),
    groupedPrototypeDocx: artifactExists(artifacts.groupedPrototypeDocx),
    groupedPrototypePdf: artifactExists(artifacts.groupedPrototypePdf),
    groupedPrototypeCards: artifactExists(artifacts.groupedPrototypeCardsCsv),
    groupedPrototypeAudit: artifactExists(artifacts.groupedPrototypeTechnicalAuditJson),
  };
}

function normalizeVcfParser(value) {
  const parser = String(value || "streaming").trim().toLowerCase();
  return ALLOWED_VCF_PARSERS.has(parser) ? parser : "streaming";
}

function normalizeLanguageMode(value) {
  const mode = String(value || "es").trim().toLowerCase();
  return ["es", "en", "both"].includes(mode) ? mode : "es";
}

function normalizeAudienceMode(value) {
  const mode = String(value || "all").trim().toLowerCase();
  return ["technical", "health_professional", "family", "all"].includes(mode) ? mode : "all";
}

function normalizeAnalysisMode(value) {
  const mode = String(value || "quick").trim().toLowerCase();
  return ["quick", "complete", "qa"].includes(mode) ? mode : "quick";
}

function normalizeAssembly(value) {
  const assembly = String(value || "GRCh38").trim().toUpperCase();
  return assembly === "GRCH37" ? "GRCh37" : "GRCh38";
}

function normalizeOptionalAssembly(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text || text === "auto") return "";
  if (["grch38", "hg38", "b38"].includes(text)) return "GRCh38";
  if (["grch37", "hg19", "b37"].includes(text)) return "GRCh37";
  return null;
}

function managedReferenceForAssembly(assembly) {
  if (assembly === "GRCh38") {
    return { fasta: GRCH38_REFERENCE_FASTA, manifest: GRCH38_REFERENCE_MANIFEST };
  }
  if (assembly === "GRCh37" && GRCH37_REFERENCE_FASTA) {
    return { fasta: GRCH37_REFERENCE_FASTA, manifest: GRCH37_REFERENCE_MANIFEST };
  }
  return null;
}

function metadataCount(summary, key) {
  return Number(summary?.metadata?.[key] || 0);
}

function resolveLlm2Model({ analysisMode, requestedModel }) {
  const mode = normalizeAnalysisMode(analysisMode);
  if (mode === "complete") return LLM2_FULL_MODEL;
  if (mode === "qa") {
    const selected = String(requestedModel || "").trim();
    return ALLOWED_LLM2_MODELS.has(selected) ? selected : LLM2_QA_DEFAULT_MODEL;
  }
  return LLM2_QUICK_MODEL;
}

async function saveUpload(upload) {
  upload.updatedAt = new Date().toISOString();
  uploads.set(upload.uploadId, upload);
  await writeFile(manifestPath(upload.uploadDir), JSON.stringify(upload, null, 2), "utf8");
}

async function refreshUploadRetention(upload) {
  await saveUpload(upload);
  const now = new Date();
  await Promise.all([
    utimes(upload.uploadDir, now, now).catch(() => {}),
    utimes(upload.storedPath, now, now).catch(() => {}),
  ]);
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
    child.on("error", (error) => {
      if (progressTimer) clearInterval(progressTimer);
      reject(error);
    });
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

function runCanonSchemaProbe(inputPath) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_EXE, [CANON_PROCESSOR_SCRIPT, "--input", inputPath, "--detect-schema"], {
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
      try {
        const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
        const payload = JSON.parse(lines[lines.length - 1] || "{}");
        if (code !== 0 && !payload.schemaVersion) {
          reject(new Error(stderr || "Could not detect canon schema."));
          return;
        }
        resolve(payload.schemaVersion || null);
      } catch (error) {
        reject(new Error(`Canon schema probe returned invalid JSON. ${stderr || error.message}`));
      }
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

async function updateJobProgressFromFile(job, progressPath, fallbackStage, { final = false } = {}) {
  if (!job || !progressPath) return;
  const raw = await readFile(progressPath, "utf8").catch(() => null);
  if (!raw) return;
  let progress;
  try {
    progress = JSON.parse(raw);
  } catch {
    return;
  }
  const processed = Number(progress.processed || 0);
  const total = Number(progress.total || 0);
  const percent = total > 0 ? Math.round((processed / total) * 100) : null;
  job.stage = progress.stage || fallbackStage || job.stage;
  if (percent !== null) {
    job.stageProgress = final ? Math.min(100, Math.max(0, percent)) : Math.min(98, Math.max(0, percent));
  }
  job.stageProgressDetail = {
    substage: progress.substage || null,
    processed,
    total,
    unit: progress.unit || "items",
    message: progress.message || null,
    metrics: progress.metrics || {},
    updatedAt: progress.updatedAt || null,
  };
  job.message = progress.message || job.message;
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
}

function runBase64JsonScript(scriptPath, payload, progressOptions = null) {
  return new Promise((resolve, reject) => {
    const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
    const child = spawn(PYTHON_EXE, [scriptPath, "--input-json-base64", encoded], {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    appendVcfCanonJobLog(progressOptions?.job, "process_started", {
      script: path.basename(scriptPath),
      service: path.basename(path.dirname(scriptPath)),
    }).catch(() => {});
    let stdout = "";
    let stderr = "";
    let progressUpdating = false;
    const progressTimer = progressOptions?.job && progressOptions?.progressPath
      ? setInterval(() => {
          if (progressUpdating) return;
          progressUpdating = true;
          updateJobProgressFromFile(
            progressOptions.job,
            progressOptions.progressPath,
            progressOptions.stage,
          )
            .catch(() => {})
            .finally(() => {
              progressUpdating = false;
            });
        }, 1000)
      : null;
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      const text = chunk.toString("utf8");
      stderr += text;
      for (const line of text.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)) {
        appendVcfCanonJobLog(progressOptions?.job, "process_stderr", {
          message: line.slice(0, 2000),
        }).catch(() => {});
      }
    });
    child.on("error", (error) => {
      if (progressTimer) clearInterval(progressTimer);
      reject(error);
    });
    child.on("close", async (code) => {
      if (progressTimer) clearInterval(progressTimer);
      appendVcfCanonJobLog(progressOptions?.job, "process_exit", {
        script: path.basename(scriptPath),
        exitCode: code,
        stderr: stderr.trim().slice(-2000) || null,
      }).catch(() => {});
      if (progressOptions?.job && progressOptions?.progressPath) {
        await updateJobProgressFromFile(progressOptions.job, progressOptions.progressPath, progressOptions.stage, { final: true }).catch(() => {});
      }
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
      appendVcfCanonJobLog(progressOptions?.job, "process_result", {
        processorStatus: result.status || null,
        outputKeys: result.outputs ? Object.keys(result.outputs) : [],
      }).catch(() => {});
      resolve(result);
    });
  });
}

function runPythonJsonCommand(scriptPath, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_EXE, [scriptPath, ...args], {
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
      try {
        const result = JSON.parse(lastLine);
        if (code !== 0) {
          reject(new Error(result.error || stderr || `Processor exited with code ${code}.`));
          return;
        }
        resolve(result);
      } catch (error) {
        reject(new Error(`Processor returned invalid JSON. ${stderr || error.message}`));
      }
    });
  });
}

async function probeVcfAssembly(inputPath) {
  return await runPythonJsonCommand(SERVICE_SCRIPTS.vcfNormalization, [
    "--probe-assembly",
    "--input",
    inputPath,
  ]);
}

async function updateIndividualInterpretationJobProgress(payload, job, { final = false } = {}) {
  const progressPath = path.join(payload.outputDir, "individual_variant_interpretation_progress.json");
  const raw = await readFile(progressPath, "utf8").catch(() => null);
  if (!raw) return;
  const progress = JSON.parse(raw);
  const totalRows = Number(progress.totalRows || 0);
  const completedRows = Number(progress.completedRows || 0);
  const percent = totalRows > 0 ? Math.round((completedRows / totalRows) * 100) : 8;
  job.stage = "individual_interpretation";
  job.stageProgress = final ? Math.min(100, Math.max(8, percent)) : Math.min(98, Math.max(8, percent));
  job.message =
    totalRows > 0
      ? `Interpreting observed variants (${completedRows}/${totalRows})`
      : "Interpreting observed variants";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
}

async function updateGroupedIndividualInterpretationJobProgress(payload, job, { final = false } = {}) {
  const progressPath = path.join(payload.outputDir, "gene_module_group_interpretation_progress.json");
  const raw = await readFile(progressPath, "utf8").catch(() => null);
  if (!raw) return;
  const progress = JSON.parse(raw);
  const totalGroups = Number(progress.totalGroups || 0);
  const completedGroups = Number(progress.completedGroups || 0);
  const percent = totalGroups > 0 ? Math.round((completedGroups / totalGroups) * 100) : 8;
  job.stage = "grouped_individual_interpretation";
  job.stageProgress = final ? Math.min(100, Math.max(8, percent)) : Math.min(98, Math.max(8, percent));
  job.message =
    totalGroups > 0
      ? `Interpreting grouped gene-module payloads (${completedGroups}/${totalGroups})`
      : "Interpreting grouped gene-module payloads";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
}

function runIndividualInterpretationScript(payload, job) {
  return new Promise((resolve, reject) => {
    const scriptPath = SERVICE_SCRIPTS.individualInterpretation;
    const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
    const child = spawn(PYTHON_EXE, [scriptPath, "--input-json-base64", encoded], {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let progressUpdating = false;
    const progressTimer = setInterval(() => {
      if (progressUpdating) return;
      progressUpdating = true;
      updateIndividualInterpretationJobProgress(payload, job)
        .catch(() => {})
        .finally(() => {
          progressUpdating = false;
        });
    }, 2500);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      clearInterval(progressTimer);
      reject(error);
    });
    child.on("close", async (code) => {
      clearInterval(progressTimer);
      await updateIndividualInterpretationJobProgress(payload, job, { final: true }).catch(() => {});
      const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
      const lastLine = lines[lines.length - 1] || "{}";
      let result;
      try {
        result = JSON.parse(lastLine);
      } catch (error) {
        reject(new Error(`Individual interpretation returned invalid JSON. ${stderr || error.message}`));
        return;
      }
      if (code !== 0 && result.status !== "warning") {
        reject(new Error(result.errors?.[0] || stderr || `Individual interpretation exited with code ${code}.`));
        return;
      }
      resolve(result);
    });
  });
}

function runGroupedIndividualInterpretationScript(payload, job) {
  return new Promise((resolve, reject) => {
    const scriptPath = SERVICE_SCRIPTS.groupedInterpretation;
    const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
    const child = spawn(PYTHON_EXE, [scriptPath, "--input-json-base64", encoded], {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let progressUpdating = false;
    const progressTimer = setInterval(() => {
      if (progressUpdating) return;
      progressUpdating = true;
      updateGroupedIndividualInterpretationJobProgress(payload, job)
        .catch(() => {})
        .finally(() => {
          progressUpdating = false;
        });
    }, 2500);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      clearInterval(progressTimer);
      reject(error);
    });
    child.on("close", async (code) => {
      clearInterval(progressTimer);
      await updateGroupedIndividualInterpretationJobProgress(payload, job, { final: true }).catch(() => {});
      const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
      const lastLine = lines[lines.length - 1] || "{}";
      let result;
      try {
        result = JSON.parse(lastLine);
      } catch (error) {
        reject(new Error(`Grouped interpretation returned invalid JSON. ${stderr || error.message}`));
        return;
      }
      if (code !== 0 && result.status !== "warning") {
        reject(new Error(result.errors?.[0] || stderr || `Grouped interpretation exited with code ${code}.`));
        return;
      }
      resolve(result);
    });
  });
}

async function processRsidResolution(payload) {
  return (
    (await postWorkflowForSummary(N8N_RSID_RESOLUTION_WEBHOOK_URL, payload, "n8n rsID resolution")) ||
    (await runBase64JsonScript(SERVICE_SCRIPTS.rsidResolution, payload))
  );
}

async function processVcfCanonMatch(payload, job) {
  if (payload.adapter === "gene_module_canon_adapter") {
    return await runBase64JsonScript(SERVICE_SCRIPTS.geneModuleMatcher, payload, {
      job,
      stage: "matching",
      progressPath: path.join(payload.outputDir, "match_progress.json"),
    });
  }
  return (
    (await postWorkflowForSummary(N8N_VCF_CANON_MATCH_WEBHOOK_URL, payload, "n8n VCF-canon match")) ||
    (await runBase64JsonScript(SERVICE_SCRIPTS.legacyMatcher, payload))
  );
}

async function processVcfNormalization(payload, job) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.vcfNormalization, payload, {
    job,
    stage: "normalizing",
    progressPath: path.join(payload.outputDir, "normalization_progress.json"),
  });
}

async function processMatchPreparation(payload, job) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.matchPreparation, payload, {
    job,
    stage: "preparing",
    progressPath: path.join(payload.outputDir, "preparation_progress.json"),
  });
}

async function processAiTriage(payload, job) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.aiTriage, payload, {
    job,
    stage: "triaging",
    progressPath: path.join(payload.outputDir, "triage_progress.json"),
  });
}

async function processVariantEnrichment(payload, job) {
  if (payload.schemaVersion === "gene_module_v2") {
    return await runBase64JsonScript(SERVICE_SCRIPTS.geneModuleEnrichment, payload, {
      job,
      stage: "enriching",
      progressPath: path.join(payload.outputDir, "enrichment_progress.json"),
    });
  }
  const webhookResult = await postWorkflowForSummary(
    N8N_VARIANT_ENRICHMENT_WEBHOOK_URL,
    payload,
    "n8n variant enrichment",
  );
  if (webhookResult?.outputs?.observedVariantEnrichmentPlusCsv) {
    return webhookResult;
  }
  return await runBase64JsonScript(SERVICE_SCRIPTS.legacyEnrichment, payload);
}

function jobArtifactKeyForCards(jobId) {
  return jobs.get(jobId)?.artifacts?.groupCardsJson ? "groupCardsJson" : "groupCardsV7Json";
}

async function processEvidenceRefinement(payload, job) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.evidenceRefinement, payload, {
    job,
    stage: "evidence_refinement",
    progressPath: path.join(payload.outputDir, "evidence_refinement_progress.json"),
  });
}

function variantEnrichmentOutputs(summary) {
  const outputs = summary?.outputs && typeof summary.outputs === "object" ? summary.outputs : summary || {};
  return {
    observedVariantEnrichmentCsv: outputs.observedVariantEnrichmentCsv || "",
    observedVariantInterpretiveCsv: outputs.observedVariantInterpretiveCsv || "",
    observedVariantEnrichmentPlusCsv: outputs.observedVariantEnrichmentPlusCsv || "",
    v2EnrichmentVariantMasterCsv: outputs.v2EnrichmentVariantMasterCsv || "",
    v2EnrichmentEvidenceAuditJsonl: outputs.v2EnrichmentEvidenceAuditJsonl || "",
    enrichmentQualitySummaryJson: outputs.enrichmentQualitySummaryJson || "",
    v2EnrichmentVepBaseCsv: outputs.v2EnrichmentVepBaseCsv || "",
    v2EnrichmentResolutionAuditJsonl: outputs.v2EnrichmentResolutionAuditJsonl || "",
    v2EnrichmentCompleteCsv: outputs.v2EnrichmentCompleteCsv || "",
    v2EnrichmentVepOnlyAuditCsv: outputs.v2EnrichmentVepOnlyAuditCsv || "",
    v2EnrichmentPhysicalMatrixCsv: outputs.v2EnrichmentPhysicalMatrixCsv || "",
    v2EnrichmentPhysicalEvidenceAuditJsonlGz: outputs.v2EnrichmentPhysicalEvidenceAuditJsonlGz || "",
    v2EnrichmentModuleProjectionCsv: outputs.v2EnrichmentModuleProjectionCsv || "",
    enrichmentRetryQueueJsonl: outputs.enrichmentRetryQueueJsonl || "",
    enrichmentIdentityResolutionSummaryJson: outputs.enrichmentIdentityResolutionSummaryJson || "",
    enrichmentPerformanceSummaryJson: outputs.enrichmentPerformanceSummaryJson || "",
  };
}

function evidenceRefinementOutputs(summary) {
  const outputs = summary?.outputs && typeof summary.outputs === "object" ? summary.outputs : summary || {};
  return {
    curatedPhysicalMatrixCsv: outputs.curatedPhysicalMatrixCsv || "",
    curatedPhysicalRegistryCsv: outputs.curatedPhysicalRegistryCsv || "",
    curatedGeneModuleProjectionCsv: outputs.curatedGeneModuleProjectionCsv || "",
    canonicalGeneModuleStatusCsv: outputs.canonicalGeneModuleStatusCsv || "",
    clinvarVariantAggregateCsv: outputs.clinvarVariantAggregateCsv || "",
    clinvarSubmitterAssertionsCsv: outputs.clinvarSubmitterAssertionsCsv || "",
    clinpgxClinicalAnnotationsCsv: outputs.clinpgxClinicalAnnotationsCsv || "",
    clinpgxVariantAnnotationsCsv: outputs.clinpgxVariantAnnotationsCsv || "",
    gwasVariantAssociationsCsv: outputs.gwasVariantAssociationsCsv || "",
    gwasVariantTraitSummaryCsv: outputs.gwasVariantTraitSummaryCsv || "",
    gwasGeneModuleSummaryCsv: outputs.gwasGeneModuleSummaryCsv || "",
    gwasEvidenceClustersCsv: outputs.gwasEvidenceClustersCsv || "",
    gwasTraitModuleRelevanceTemplateCsv: outputs.gwasTraitModuleRelevanceTemplateCsv || "",
    gwasMetadataRetryQueueJsonl: outputs.gwasMetadataRetryQueueJsonl || "",
    publicationEvidenceCsv: outputs.publicationEvidenceCsv || "",
    evidenceRefinementRawJsonlGz: outputs.evidenceRefinementRawJsonlGz || "",
    evidenceRefinementRetryQueueJsonl: outputs.evidenceRefinementRetryQueueJsonl || "",
    evidenceRefinementSummaryJson: outputs.evidenceRefinementSummaryJson || "",
  };
}

async function processGroupedInterpretationPrep(payload) {
  return await runBase64JsonScript(
    SERVICE_SCRIPTS.groupedPrep,
    payload,
  );
}

async function processGroupedIndividualInterpretation(payload, job) {
  return await runGroupedIndividualInterpretationScript(payload, job);
}

async function processIndividualInterpretation(payload, job) {
  return (
    (await postWorkflowForSummary(
      N8N_INDIVIDUAL_INTERPRETATION_WEBHOOK_URL,
      payload,
      "n8n individual variant interpretation",
    )) ||
    (await runIndividualInterpretationScript(payload, job))
  );
}

async function processInterpretationNormalization(payload) {
  return await runBase64JsonScript(
    SERVICE_SCRIPTS.interpretationNormalization,
    payload,
  );
}

async function processGlobalInterpretation(payload) {
  return (
    (await postWorkflowForSummary(N8N_GLOBAL_INTERPRETATION_WEBHOOK_URL, payload, "n8n global interpretation")) ||
    (await runBase64JsonScript(SERVICE_SCRIPTS.globalInterpretation, payload))
  );
}

async function processFinalReport(payload) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.finalReport, payload);
}

async function processGroupedPrototype(payload, progressOptions = null) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.groupedPrototype, payload, progressOptions);
}

async function processVariantEnrichmentWithRetry(payload, job, attempts = 3) {
  const errors = [];
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      job.stage = "enriching";
      job.stageProgress = Math.max(job.stageProgress || 12, attempt === 1 ? 12 : 18);
      job.message =
        attempt === 1
          ? "Enriching observed variants with external sources"
          : `Retrying variant enrichment (${attempt}/${attempts})`;
      job.updatedAt = new Date().toISOString();
      return await processVariantEnrichment(payload, job);
    } catch (error) {
      errors.push(error.message || String(error));
      if (attempt >= attempts) break;
      await new Promise((resolve) => setTimeout(resolve, 1200 * attempt));
    }
  }
  throw new Error(`Variant enrichment failed after ${attempts} attempts: ${errors.join(" | ")}`);
}

async function processGroupedPayloadV5(payload) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.groupedPayloadV5, payload);
}

async function processGroupedPayloadV6(payload) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.groupedPayloadV6, payload);
}

async function processGroupedPayloadV7(payload) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.groupedPayloadV7, payload);
}

async function processEvidenceDigest(payload) {
  return await runBase64JsonScript(SERVICE_SCRIPTS.evidenceDigest, payload);
}

async function runEvidenceRefinementForJob({
  job,
  runId,
  analysisMode,
  assembly,
  physicalMatrixPath,
  matchPath,
  triagePath,
  triageExcludedPath,
  canonCleanPath,
  normalizedVariantsPath,
}) {
  const refinementPaths = evidenceRefinementPaths();
  await mkdir(refinementPaths.runs, { recursive: true });
  await mkdir(refinementPaths.cache, { recursive: true });
  const outputDir = jobStageDirectory(job.id, "evidence-refinement");
  await mkdir(outputDir, { recursive: true });
  const inputs = [physicalMatrixPath, matchPath, triagePath, triageExcludedPath, normalizedVariantsPath]
    .filter(Boolean)
    .map((value) => path.resolve(value));
  if (inputs.some((value) => !isPathInside(RUNTIME_PATHS.runs, value))) {
    throw new Error("Evidence refinement input is outside the allowed HEAL run root.");
  }
  const resolvedCanonPath = path.resolve(canonCleanPath || "");
  if (!isPathInside(canonPaths().root, resolvedCanonPath)) {
    throw new Error("Evidence refinement canon input is outside the allowed canon root.");
  }
  job.artifacts = job.artifacts || {};
  Object.assign(job.artifacts, {
    curatedPhysicalMatrixCsv: path.join(outputDir, "v2_curated_physical_variant_matrix.csv"),
    curatedPhysicalRegistryCsv: path.join(outputDir, "v2_curated_physical_variant_registry.csv"),
    curatedGeneModuleProjectionCsv: path.join(outputDir, "v2_curated_gene_module_projection.csv"),
    canonicalGeneModuleStatusCsv: path.join(outputDir, "v2_canonical_gene_module_status.csv"),
    clinvarVariantAggregateCsv: path.join(outputDir, "clinvar_variant_aggregate.csv"),
    clinvarSubmitterAssertionsCsv: path.join(outputDir, "clinvar_submitter_assertions.csv"),
    clinpgxClinicalAnnotationsCsv: path.join(outputDir, "clinpgx_clinical_annotations.csv"),
    clinpgxVariantAnnotationsCsv: path.join(outputDir, "clinpgx_variant_annotations.csv"),
    gwasVariantAssociationsCsv: path.join(outputDir, "gwas_variant_associations.csv"),
    gwasVariantTraitSummaryCsv: path.join(outputDir, "gwas_variant_trait_summary.csv"),
    gwasGeneModuleSummaryCsv: path.join(outputDir, "gwas_gene_module_summary.csv"),
    gwasEvidenceClustersCsv: path.join(outputDir, "gwas_evidence_clusters.csv"),
    gwasTraitModuleRelevanceTemplateCsv: path.join(outputDir, "gwas_trait_module_relevance_template.csv"),
    gwasMetadataRetryQueueJsonl: path.join(outputDir, "gwas_metadata_retry_queue.jsonl"),
    publicationEvidenceCsv: path.join(outputDir, "publication_evidence.csv"),
    evidenceRefinementRawJsonlGz: path.join(outputDir, "evidence_refinement_raw.jsonl.gz"),
    evidenceRefinementRetryQueueJsonl: path.join(outputDir, "evidence_refinement_retry_queue.jsonl"),
    evidenceRefinementSummaryJson: path.join(outputDir, "evidence_refinement_summary.json"),
  });
  job.stage = "evidence_refinement";
  job.stageProgress = 2;
  job.message = "Curating ClinVar, PharmGKB context, GWAS evidence clusters and selected publications";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
  const summary = await processEvidenceRefinement(
    {
      event: "heal.evidence_refinement.requested",
      runId: `evidence-refinement-${runId}`,
      matchRunId: runId,
      schemaVersion: "gene_module_v2",
      analysisMode: normalizeAnalysisMode(analysisMode),
      assembly,
      physicalMatrixPath,
      matchPath,
      triagePath,
      triageExcludedPath: triageExcludedPath || "",
      canonCleanPath: resolvedCanonPath,
      normalizedVariantsPath: normalizedVariantsPath || "",
      gwasTraitModuleMapPath: existsSync(HEAL_GWAS_TRAIT_MODULE_MAP_PATH) ? HEAL_GWAS_TRAIT_MODULE_MAP_PATH : "",
      outputDir,
      cachePath: refinementPaths.cachePath,
      requestedAt: new Date().toISOString(),
    },
    job,
  );
  const outputs = evidenceRefinementOutputs(summary);
  Object.assign(job.artifacts, outputs);
  const required = [
    outputs.curatedPhysicalMatrixCsv,
    outputs.curatedPhysicalRegistryCsv,
    outputs.curatedGeneModuleProjectionCsv,
    outputs.canonicalGeneModuleStatusCsv,
    outputs.evidenceRefinementSummaryJson,
  ];
  if (required.some((artifactPath) => !artifactPath || !existsSync(artifactPath))) {
    throw new Error("Evidence refinement did not produce its required conservation artifacts.");
  }
  job.result = {
    ...(job.result || {}),
    evidenceRefinement: sanitizeEvidenceRefinementResult(summary),
  };
  await persistVcfCanonJob(job);
  return summary;
}

async function processIndividualInterpretationWithRetry(payload, job, attempts = 2) {
  const errors = [];
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      job.stage = "individual_interpretation";
      job.stageProgress = Math.max(job.stageProgress || 8, attempt === 1 ? 8 : 16);
      job.message =
        attempt === 1
          ? "Interpreting observed variants individually"
          : `Retrying individual interpretation (${attempt}/${attempts})`;
      job.updatedAt = new Date().toISOString();
      return await processIndividualInterpretation(payload, job);
    } catch (error) {
      errors.push(error.message || String(error));
      if (attempt >= attempts) break;
      await new Promise((resolve) => setTimeout(resolve, 1500 * attempt));
    }
  }
  throw new Error(`Individual interpretation failed after ${attempts} attempts: ${errors.join(" | ")}`);
}

async function processGroupedIndividualInterpretationWithRetry(payload, job, attempts = 2) {
  const errors = [];
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      job.stage = "grouped_individual_interpretation";
      job.stageProgress = Math.max(job.stageProgress || 8, attempt === 1 ? 8 : 16);
      job.message =
        attempt === 1
          ? "Interpreting grouped gene-module payloads"
          : `Retrying grouped interpretation (${attempt}/${attempts})`;
      job.updatedAt = new Date().toISOString();
      return await processGroupedIndividualInterpretation(payload, job);
    } catch (error) {
      errors.push(error.message || String(error));
      if (attempt >= attempts) break;
      await new Promise((resolve) => setTimeout(resolve, 1500 * attempt));
    }
  }
  throw new Error(`Grouped interpretation failed after ${attempts} attempts: ${errors.join(" | ")}`);
}

async function loadCurrentCanon() {
  const manifest = await loadCurrentCanonManifest();
  if (!manifest) return publicCanon(null, null, null);
  const paths = canonPaths();
  const summaryPath = resolveStoredPath(paths.root, manifest.summaryPath);
  const previewPath = resolveStoredPath(paths.root, manifest.previewPath);
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
  const manifest = JSON.parse(raw);
  return {
    ...manifest,
    summaryPath: resolveStoredPath(paths.root, manifest.summaryPath),
    previewPath: resolveStoredPath(paths.root, manifest.previewPath),
  };
}

async function loadCurrentRsidResolutionManifest() {
  const paths = rsidResolutionPaths();
  const raw = await readFile(paths.currentManifest, "utf8").catch(() => null);
  if (!raw) return null;
  const manifest = JSON.parse(raw);
  return {
    ...manifest,
    summaryPath: resolveStoredPath(paths.root, manifest.summaryPath),
    rsidMatchReadyCsv: resolveStoredPath(paths.root, manifest.rsidMatchReadyCsv),
  };
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
  await writeFile(
    paths.currentManifest,
    JSON.stringify(
      {
        ...manifest,
        summaryPath: storeRelativePath(paths.root, manifest.summaryPath),
        rsidMatchReadyCsv: storeRelativePath(paths.root, manifest.rsidMatchReadyCsv),
      },
      null,
      2,
    ),
    "utf8",
  );
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
    schemaVersion: summary.schemaVersion || null,
    adapter: summary.adapter || null,
    assembly: summary.assembly || null,
    activationStatus: summary.activationStatus || null,
    warningsSummary: summary.warningsSummary || {},
    summaryPath: path.join(paths.runs, runId, "canon_summary.json"),
    previewPath: path.join(paths.runs, runId, "canon_preview.json"),
    createdAt: new Date().toISOString(),
  };
  await writeFile(
    paths.currentManifest,
    JSON.stringify(
      {
        ...manifest,
        summaryPath: storeRelativePath(paths.root, manifest.summaryPath),
        previewPath: storeRelativePath(paths.root, manifest.previewPath),
      },
      null,
      2,
    ),
    "utf8",
  );
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
    artifactsReady: publicArtifactsReady(job),
    stage: job.stage || null,
    stageProgress: job.stageProgress ?? null,
    stageProgressDetail: job.stageProgressDetail || null,
    error: job.error,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
  };
}

function downstreamBlockedForJob(job) {
  return job?.result?.metadata?.downstream_supported === false;
}

function isVariantEnrichmentStage(stage) {
  return [
    "enriching",
    "enrichment_vep",
    "enrichment_identity",
    "enrichment_complete",
    "enrichment_vep_only",
    "enrichment_quality_gate",
    "evidence_refinement",
    "evidence_refinement_quality_gate",
  ].includes(stage || "");
}

function downstreamBlockedMessage(job) {
  return (
    job?.result?.metadata?.downstream_message ||
    "Downstream interpretation is blocked for this canon schema. Supported handoff is grouped_individual_interpretation."
  );
}

function shouldPersistVcfCanonJob(job) {
  return Boolean(
    job?.artifacts ||
      [
        "matching",
        "normalizing",
        "preparing",
        "triaging",
        "enriching",
        "enrichment_vep",
        "enrichment_identity",
        "enrichment_complete",
        "enrichment_vep_only",
        "enrichment_quality_gate",
        "grouping_preparation",
        "grouped_individual_interpretation",
        "individual_interpretation",
        "interpretation_normalization",
        "global_interpretation",
        "final_report",
      ].includes(job?.stage || ""),
  );
}

function vcfCanonJobPath(jobId) {
  return path.join(vcfCanonMatchPaths().jobs, `${safeFileName(jobId)}.json`);
}

function vcfCanonJobLogPath(jobId) {
  return path.join(vcfCanonMatchPaths().jobs, `${safeFileName(jobId)}.jsonl`);
}

function appendVcfCanonJobLog(job, event, details = {}) {
  if (!job?.id) return Promise.resolve();
  const entry = {
    timestamp: new Date().toISOString(),
    event,
    stage: job.stage || null,
    status: job.status || null,
    progress: Number.isFinite(Number(job.progress)) ? Number(job.progress) : null,
    stageProgress: Number.isFinite(Number(job.stageProgress)) ? Number(job.stageProgress) : null,
    message: job.message || null,
    ...details,
  };
  const logPath = vcfCanonJobLogPath(job.id);
  const previous = jobLogQueues.get(job.id) || Promise.resolve();
  const next = previous
    .catch(() => {})
    .then(async () => {
      await mkdir(path.dirname(logPath), { recursive: true });
      await appendFile(logPath, `${JSON.stringify(entry, null, 0)}\n`, "utf8");
    });
  jobLogQueues.set(job.id, next);
  return next;
}

async function persistVcfCanonJob(job) {
  if (!shouldPersistVcfCanonJob(job)) return;
  const paths = vcfCanonMatchPaths();
  await mkdir(paths.jobs, { recursive: true });
  const target = vcfCanonJobPath(job.id);
  const temporary = `${target}.${process.pid}.${crypto.randomUUID()}.tmp`;
  await writeFile(temporary, JSON.stringify(serializeJobForStorage(job), null, 2), "utf8");
  await rename(temporary, target);
  appendVcfCanonJobLog(job, "job_state", {
    stageProgressDetail: job.stageProgressDetail || null,
  }).catch(() => {});
}

async function loadPersistedVcfCanonJobs() {
  const paths = vcfCanonMatchPaths();
  await mkdir(paths.jobs, { recursive: true });
  const entries = await readdir(paths.jobs, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    try {
      const job = hydrateJobArtifacts(JSON.parse(await readFile(path.join(paths.jobs, entry.name), "utf8")));
      if (job?.id && shouldPersistVcfCanonJob(job)) {
        if (job.status === "running") {
          job.status = "failed";
          job.progress = 100;
          job.stageProgress = 100;
          job.error = "Job was interrupted by an API restart. Please retry this stage.";
          job.message = "Interrupted job can be retried";
          job.updatedAt = new Date().toISOString();
          await persistVcfCanonJob(job);
        }
        jobs.set(job.id, job);
      }
    } catch {
      // Ignore corrupt historical job manifests; active runs can create a fresh one.
    }
  }
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

function runCommand(command, args, { timeoutMs = 10_000 } = {}) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      resolve(result);
    };
    const timeout = setTimeout(() => {
      child.kill();
      finish({ ok: false, timedOut: true, error: `${command} timed out after ${timeoutMs}ms.`, stdout, stderr });
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => finish({ ok: false, error: error.message, stdout, stderr }));
    child.on("close", (code) => finish({ ok: code === 0, code, stdout, stderr }));
  });
}

async function runtimeHealth() {
  const [storage, reference, referenceIndex, docker, canonManifest] = await Promise.all([
    statfs(DATA_ROOT).catch(() => null),
    stat(GRCH38_REFERENCE_FASTA).catch(() => null),
    stat(`${GRCH38_REFERENCE_FASTA}.fai`).catch(() => null),
    runCommand("docker", ["image", "inspect", NORMALIZER_IMAGE], { timeoutMs: 5_000 }).catch(() => ({ ok: false })),
    loadCurrentCanonManifest().catch(() => null),
  ]);
  const canonSummaryPath = canonManifest ? resolveStoredPath(canonPaths().root, canonManifest.summaryPath) : "";
  const canonSummary =
    canonSummaryPath && isPathInside(canonPaths().root, canonSummaryPath)
      ? await stat(canonSummaryPath).catch(() => null)
      : null;
  const bytesFree = storage ? Number(storage.bsize) * Number(storage.bavail) : null;
  return {
    deployment: { sha: DEPLOYMENT_SHA, homeConfigured: Boolean(process.env.HEAL_HOME), maintenanceMode: MAINTENANCE_MODE },
    storage: { configured: Boolean(DATA_ROOT), available: Boolean(storage), bytesFree },
    runtime: {
      appConfigured: Boolean(APP_ROOT),
      configConfigured: Boolean(CONFIG_ROOT),
      logsConfigured: Boolean(LOG_ROOT),
      backupsConfigured: Boolean(BACKUP_ROOT),
    },
    canon: { active: Boolean(canonManifest), artifactsReady: Boolean(canonSummary) },
    reference: { grch38Ready: Boolean(reference && referenceIndex) },
    docker: { normalizerImage: NORMALIZER_IMAGE, ready: Boolean(docker.ok) },
  };
}

async function v2NormalizationPreflight(uploadSizeBytes) {
  const requiredBytes = Math.max(20 * 1024 * 1024 * 1024, Number(uploadSizeBytes || 0) * 10);
  const [workspace, docker] = await Promise.all([
    statfs(VCF_NORMALIZATION_ROOT).catch(() => null),
    runCommand("docker", ["image", "inspect", NORMALIZER_IMAGE], { timeoutMs: 5_000 }).catch(() => ({ ok: false })),
  ]);
  const availableBytes = workspace ? Number(workspace.bsize) * Number(workspace.bavail) : 0;
  if (!workspace || availableBytes < requiredBytes) {
    return {
      ok: false,
      code: "normalization_workspace_insufficient",
      error: "Insufficient HEAL workspace capacity for VCF normalization.",
      requiredBytes,
      availableBytes,
    };
  }
  if (!docker.ok) {
    return {
      ok: false,
      code: "normalizer_image_unavailable",
      error: "The HEAL VCF normalizer image is unavailable. Deploy health must rebuild it before matching.",
      requiredBytes,
      availableBytes,
    };
  }
  return { ok: true, requiredBytes, availableBytes };
}

app.get("/api/health", async (_req, res) => {
  const runtime = await runtimeHealth();
  res.json({
    ok: true,
    storageConfigured: runtime.storage.available,
    validatorConfigured: Boolean(VALIDATOR_SCRIPT),
    canonProcessorConfigured: Boolean(CANON_PROCESSOR_SCRIPT),
    runtime,
    individualInterpretationConfigured: Boolean(process.env.HEAL_OPENAI_API_KEY || process.env.OPENAI_API_KEY),
    individualInterpretationModel: LLM1_MODEL,
    individualInterpretationPromptProfile: LLM1_PROMPT_PROFILE,
    individualInterpretationReasoningEffort: LLM1_REASONING_EFFORT,
    globalInterpretationConfigured: Boolean(process.env.HEAL_OPENAI_API_KEY || process.env.OPENAI_API_KEY),
    globalInterpretationModels: {
      quick: LLM2_QUICK_MODEL,
      complete: LLM2_FULL_MODEL,
      qaDefault: LLM2_QA_DEFAULT_MODEL,
      allowed: Array.from(ALLOWED_LLM2_MODELS),
    },
    finalReportConfigured: Boolean(FINAL_REPORT_ROOT),
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
    n8nVariantEnrichmentWebhookConfigured: Boolean(N8N_VARIANT_ENRICHMENT_WEBHOOK_URL),
    v2Llm1Enabled: HEAL_V2_LLM1_ENABLED,
    groupedPrototypeEnabled: HEAL_GROUPED_PROTOTYPE_ENABLED,
    groupedPrototypeConfigured:
      existsSync(HEAL_PROTOTYPE_SNAPSHOT_PATH) &&
      existsSync(HEAL_PROTOTYPE_CANDIDATE_MANIFEST_PATH) &&
      existsSync(HEAL_PROTOTYPE_COVERAGE_MANIFEST_PATH),
    groupedPrototypeExecutionMode: HEAL_PROTOTYPE_EXECUTION_MODE,
    groupedPrototypeModels: { llm1: HEAL_PROTOTYPE_LLM1_MODEL, llm2: HEAL_PROTOTYPE_LLM2_MODEL },
    v2Llm1PilotEnabled: HEAL_V2_LLM1_PILOT_ENABLED,
    llm1ExecutionMode: HEAL_LLM1_EXECUTION_MODE,
    llm1ActiveTiers: HEAL_LLM1_ACTIVE_TIERS.split(",").map((value) => value.trim()).filter(Boolean),
    llm1ActiveAgeBands: HEAL_LLM1_ACTIVE_AGE_BANDS.split(",").map((value) => value.trim()).filter(Boolean),
    llm1ExperimentalCanaries: HEAL_LLM1_EXPERIMENTAL_CANARIES.split(",").map((value) => value.trim()).filter(Boolean),
    llm1CurationSnapshotId: HEAL_LLM1_CURATION_SNAPSHOT_ID,
    v2EvidenceDigestEnabled: HEAL_V2_EVIDENCE_DIGEST_ENABLED,
    v2EvidenceDigestModelConfigured: Boolean(HEAL_V2_EVIDENCE_DIGEST_MODEL),
    v2CurationUploadConfigured: Boolean(HEAL_CURATION_ACCESS_TOKEN),
    v2MechanismRegistryConfigured: existsSync(HEAL_MECHANISM_REGISTRY_PATH),
    v2GwasTraitModuleMapConfigured: existsSync(HEAL_GWAS_TRAIT_MODULE_MAP_PATH),
    v2MinVepCoverage: HEAL_V2_MIN_VEP_COVERAGE,
    n8nIndividualInterpretationWebhookConfigured: Boolean(N8N_INDIVIDUAL_INTERPRETATION_WEBHOOK_URL),
    n8nGlobalInterpretationWebhookConfigured: Boolean(N8N_GLOBAL_INTERPRETATION_WEBHOOK_URL),
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
  if (summary.schemaVersion === "gene_module_v2") {
    const geneMasterPath = path.resolve(summary.outputs?.geneMasterCsv || "");
    if (!isPathInside(paths.root, geneMasterPath)) {
      res.status(400).json({ error: "Current gene master CSV is outside the allowed root." });
      return;
    }
    const geneMasterStat = await stat(geneMasterPath).catch(() => null);
    if (!geneMasterStat || geneMasterStat.size <= 0) {
      res.status(404).json({ error: "Current gene master CSV was not found." });
      return;
    }
    const baseName = safeFileName(String(summary.sourceFileName || "heal-canon").replace(/\.(csv|xlsx)$/i, ""));
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.download(geneMasterPath, `${baseName}_gene_master.csv`);
    return;
  }
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

app.get("/api/canon/current/debug/:artifact", async (req, res) => {
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
  const artifactMap =
    summary.schemaVersion === "gene_module_v2"
      ? {
          gene_master: "geneMasterCsv",
          preprocessing_warnings: "preprocessingWarningsCsv",
          clean_rows: "cleanRowsCsv",
        }
      : {
          targets_ok: "targetsOkCsv",
          targets_repeated_rsids: "targetsRepeatedRsidsCsv",
          targets_manual_review: "targetsManualReviewCsv",
          rsids_long: "rsidsLongCsv",
          rsid_master_raw: "rsidMasterCsv",
        };
  const artifactKey = artifactMap[req.params.artifact];
  if (!artifactKey) {
    res.status(404).json({ error: "Unknown canon debug artifact." });
    return;
  }
  const csvPath = path.resolve(summary.outputs?.[artifactKey] || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "Canon debug CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "Canon debug CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(summary.sourceFileName || "heal-canon").replace(/\.(csv|xlsx)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_${req.params.artifact}.csv`);
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
  const assembly = normalizeAssembly(req.headers["x-canon-assembly"]);

  await cleanupOldCanons();
  const paths = canonPaths();
  await mkdir(paths.incoming, { recursive: true });
  await mkdir(paths.runs, { recursive: true });
  const runId = crypto.randomUUID();
  const stagingDir = path.join(paths.incoming, runId);
  const outputDir = path.join(paths.runs, runId);
  const inputPath = path.join(stagingDir, fileName);
  const progressPath = path.join(outputDir, "canon_progress.json");
  await mkdir(stagingDir, { recursive: true });
  await mkdir(outputDir, { recursive: true });
  await writeFile(inputPath, body);
  let schemaDetected = null;
  try {
    schemaDetected = await runCanonSchemaProbe(inputPath);
  } catch {
    schemaDetected = null;
  }

  const job = {
    id: runId,
    status: "queued",
    progress: 5,
    message: "Queued canon preprocessing",
    sourceFileName: fileName,
    assembly,
    schemaDetected,
    progressPath,
    stages: createCanonStageState(),
    result: null,
    error: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  updateCanonStage(job, "schema_detection", {
    status: schemaDetected ? "complete" : "running",
    progress: schemaDetected ? 100 : 25,
    message: schemaDetected ? `Detected canon schema: ${schemaDetected}` : "Detecting canon schema",
  });
  canonJobs.set(job.id, job);

  (async () => {
    try {
      job.status = "running";
      job.progress = 15;
      job.message = "Detecting canon schema";
      job.updatedAt = new Date().toISOString();

      const payload = {
        event: "heal.canon.sheet_intake.requested",
        runId,
        fileName,
        sizeBytes: body.length,
        inputPath,
        outputDir,
        progressPath,
        assembly,
        requestedAt: new Date().toISOString(),
      };
      let summary;
      if (schemaDetected === "gene_module_v2") {
        job.progress = 35;
        job.message = "Resolving genes, transcripts, and features";
        job.updatedAt = new Date().toISOString();
        updateCanonStage(job, "gene_resolution", {
          status: "running",
          progress: 5,
          message: "Resolving genes, transcripts, and features",
        });
        summary = await runBase64JsonScript(CANON_PROCESSOR_SCRIPT, payload);
      } else {
        job.progress = 35;
        job.message = "Processing legacy canon";
        job.updatedAt = new Date().toISOString();
        updateCanonStage(job, "row_normalization", {
          status: "running",
          progress: 5,
          message: "Processing legacy canon",
        });
        summary = (await processCanonWithN8n(payload)) || (await runBase64JsonScript(CANON_PROCESSOR_SCRIPT, payload));
      }
      if (summary.activationStatus === "blocked") {
        throw new Error("Canon preprocessing completed but activation is blocked by missing required runtime artifacts or unresolved genes.");
      }
      await refreshCanonJobProgress(job);

      let rsidResolution = null;
      if (summary.schemaVersion !== "gene_module_v2") {
        job.progress = 75;
        job.message = "Resolving rsID coordinates";
        job.updatedAt = new Date().toISOString();
        updateCanonStage(job, "activation", {
          status: "running",
          progress: 35,
          message: "Resolving rsID coordinates",
        });
        rsidResolution = await resolveRsidForCanon(runId, summary);
      }

      const current = await saveCurrentCanon(runId, fileName, summary);
      if (rsidResolution) {
        current.current.rsidResolution = {
          status: rsidResolution.summary.status,
          runId: rsidResolution.manifest.runId,
          metadata: rsidResolution.summary.metadata || {},
          createdAt: rsidResolution.manifest.createdAt,
        };
      }

      job.status = "complete";
      job.progress = 100;
      job.message = "Canon preprocessing completed";
      job.result = current;
      job.schemaDetected = summary.schemaVersion || schemaDetected;
      updateCanonStage(job, "activation", {
        status: "complete",
        progress: 100,
        message: "Canon published as current version",
      });
      job.updatedAt = new Date().toISOString();
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.error = error.message || String(error);
      job.message = "Canon preprocessing failed";
      updateCanonStage(job, "activation", {
        status: "failed",
        progress: Math.max(0, job.stages?.activation?.progress || 0),
        message: job.error,
      });
      job.updatedAt = new Date().toISOString();
    }
  })();

  res.status(202).json(publicCanonJob(job));
});

app.get("/api/canon/jobs/:jobId", async (req, res) => {
  const job = canonJobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "Canon job not found." });
    return;
  }
  await refreshCanonJobProgress(job);
  res.json(publicCanonJob(job));
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
      accessToken: upload.accessToken || null,
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
    accessToken: crypto.randomBytes(32).toString("base64url"),
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
  if (!canAccessUpload(req, upload)) {
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
  if (!canAccessUpload(req, upload)) {
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
  if (!canAccessUpload(req, upload)) {
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
    vcfParser = "streaming",
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
  if (!canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Upload belongs to a different client." });
    return;
  }
  if (upload.status !== "complete") {
    res.status(409).json({ error: "Upload must be complete before validation." });
    return;
  }
  await refreshUploadRetention(upload);

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
      args.push("--vcf-parser", normalizeVcfParser(vcfParser));

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
          upload.validation = {
            jobId: job.id,
            status: result.status,
            completedAt: new Date().toISOString(),
          };
          await refreshUploadRetention(upload);
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
  const { uploadId, vcfParser = "streaming", analysisMode = "quick", vcfAssembly } = req.body || {};
  if (!uploadId) {
    res.status(400).json({ error: "uploadId is required." });
    return;
  }

  const upload = await loadUpload(uploadId);
  if (!upload) {
    res.status(404).json({ error: "Upload not found." });
    return;
  }
  if (!canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Upload belongs to a different client." });
    return;
  }
  if (upload.status !== "complete") {
    res.status(409).json({ error: "Upload must be complete before VCF-canon matching." });
    return;
  }
  await refreshUploadRetention(upload);

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
  const isGeneModuleV2 = canonSummary.schemaVersion === "gene_module_v2";
  let resolvedVcfAssembly = "";
  let vcfAssemblySource = "";
  let vcfAssemblyProbe = null;
  let normalizationReference = null;
  if (isGeneModuleV2) {
    const requestedVcfAssembly = normalizeOptionalAssembly(vcfAssembly);
    if (requestedVcfAssembly === null) {
      res.status(400).json({ error: "VCF assembly must be Auto-detect, GRCh38, or GRCh37." });
      return;
    }
    vcfAssemblyProbe = await probeVcfAssembly(resolvedStoredPath).catch((error) => ({ status: "unknown", error: error.message }));
    const detectedVcfAssembly = vcfAssemblyProbe?.status === "detected" ? vcfAssemblyProbe.assembly : "";
    if (requestedVcfAssembly && detectedVcfAssembly && requestedVcfAssembly !== detectedVcfAssembly) {
      res.status(409).json({
        error: "The selected VCF assembly conflicts with the VCF header.",
        code: "vcf_assembly_selection_conflict",
        selectedAssembly: requestedVcfAssembly,
        detectedAssembly: detectedVcfAssembly,
      });
      return;
    }
    resolvedVcfAssembly = detectedVcfAssembly || requestedVcfAssembly;
    vcfAssemblySource = detectedVcfAssembly ? vcfAssemblyProbe?.source || "header" : requestedVcfAssembly ? "user_confirmation" : "";
    if (!resolvedVcfAssembly) {
      res.status(409).json({
        error: "VCF assembly could not be inferred. Select GRCh38 or GRCh37 before matching.",
        code: "vcf_assembly_confirmation_required",
        canonAssembly: canonSummary.assembly || "GRCh38",
        assemblyProbe: vcfAssemblyProbe,
      });
      return;
    }
    const canonAssembly = normalizeAssembly(canonSummary.assembly || "GRCh38");
    if (resolvedVcfAssembly !== canonAssembly) {
      res.status(409).json({
        error: "The VCF assembly does not match the active canon. Liftover is not available for this run.",
        code: "vcf_canon_assembly_mismatch",
        canonAssembly,
        detectedAssembly: resolvedVcfAssembly,
        assemblyProbe: vcfAssemblyProbe,
      });
      return;
    }
    normalizationReference = managedReferenceForAssembly(resolvedVcfAssembly);
    if (!normalizationReference) {
      res.status(409).json({
        error: `No managed ${resolvedVcfAssembly} reference is provisioned for VCF normalization.`,
        code: "normalization_reference_not_provisioned",
      });
      return;
    }
    const [referenceStat, referenceIndexStat] = await Promise.all([
      stat(normalizationReference.fasta).catch(() => null),
      stat(`${normalizationReference.fasta}.fai`).catch(() => null),
    ]);
    if (!referenceStat || !referenceIndexStat) {
      res.status(409).json({
        error: `The managed ${resolvedVcfAssembly} reference is not ready for VCF normalization.`,
        code: "normalization_reference_not_ready",
      });
      return;
    }
    const preflight = await v2NormalizationPreflight(upload.sizeBytes);
    if (!preflight.ok) {
      res.status(409).json(preflight);
      return;
    }
  }
  let resolutionManifest = null;
  let rsidReadyPath = "";
  let geneMasterPath = "";
  let geneEnvelopeIndexPath = "";
  let mergedFeatureIndexPath = "";
  if (isGeneModuleV2) {
    geneMasterPath = path.resolve(canonSummary.outputs?.geneMasterCsv || "");
    geneEnvelopeIndexPath = path.resolve(canonSummary.outputs?.geneEnvelopeIndexJson || "");
    mergedFeatureIndexPath = path.resolve(canonSummary.outputs?.mergedFeatureIndexJson || "");
    if (!isPathInside(canonRoot, geneMasterPath) || !isPathInside(canonRoot, geneEnvelopeIndexPath) || !isPathInside(canonRoot, mergedFeatureIndexPath)) {
      res.status(409).json({ error: "The current gene-module canon is missing required runtime artifacts." });
      return;
    }
  } else {
    const resolutionPaths = rsidResolutionPaths();
    resolutionManifest = await loadCurrentRsidResolutionManifest().catch(() => null);
    rsidReadyPath = path.resolve(resolutionManifest?.rsidMatchReadyCsv || "");
    if (!resolutionManifest || !isPathInside(resolutionPaths.root, rsidReadyPath)) {
      res.status(409).json({ error: "The current canon does not have an rsID match-ready file yet." });
      return;
    }
  }

  const job = {
    id: crypto.randomUUID(),
    uploadId,
    fileName: upload.fileName,
    sizeBytes: upload.sizeBytes,
    analysisMode: normalizeAnalysisMode(analysisMode),
    vcfAssembly: resolvedVcfAssembly || null,
    vcfAssemblySource: vcfAssemblySource || null,
    status: "running",
    progress: 8,
    stage: "matching",
    stageProgress: 8,
    message: "Preparing VCF-canon match",
    result: null,
    error: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  jobs.set(job.id, job);
  await persistVcfCanonJob(job);

  (async () => {
    try {
      const paths = vcfCanonMatchPaths();
      await mkdir(paths.runs, { recursive: true });
      const runId = isGeneModuleV2 ? job.id : crypto.randomUUID();
      const outputDir = isGeneModuleV2
        ? jobStageDirectory(job.id, "matching")
        : path.join(paths.runs, runId);
      await mkdir(outputDir, { recursive: true });
      let vcfPathForMatching = resolvedStoredPath;
      let normalizationSummary = null;
      const normalizationPaths = isGeneModuleV2 ? vcfNormalizationPaths() : null;
      if (isGeneModuleV2) {
        await mkdir(normalizationPaths.runs, { recursive: true });
        const normalizationRunId = `vcf-normalization-${runId}`;
        const normalizationOutputDir = jobStageDirectory(job.id, "normalization");
        await mkdir(normalizationOutputDir, { recursive: true });
        job.progress = 12;
        job.stage = "normalizing";
        job.stageProgress = 10;
        job.message = "Normalizing VCF alleles against the managed reference";
        job.updatedAt = new Date().toISOString();
        await persistVcfCanonJob(job);
        normalizationSummary = await processVcfNormalization({
          event: "heal.vcf_normalization.requested",
          runId: normalizationRunId,
          matchRunId: runId,
          inputPath: resolvedStoredPath,
          outputDir: normalizationOutputDir,
          assembly: resolvedVcfAssembly,
          referenceFasta: normalizationReference.fasta,
          referenceManifestPath: normalizationReference.manifest,
          geneEnvelopeIndexPath,
          requestedAt: new Date().toISOString(),
        }, job);
        const normalizedVcfPath = path.resolve(normalizationSummary.normalizedVcfPath || "");
        const normalizedVariantsCsv = path.resolve(normalizationSummary.normalizedVariantsCsv || "");
        const normalizationExcludedAuditCsv = path.resolve(normalizationSummary.normalizationExcludedAuditCsv || "");
        const normalizationSummaryJson = path.resolve(normalizationSummary.normalizationSummaryJson || "");
        if (
          !isPathInside(normalizationPaths.root, normalizedVcfPath) ||
          !isPathInside(normalizationPaths.root, normalizedVariantsCsv) ||
          !isPathInside(normalizationPaths.root, normalizationExcludedAuditCsv) ||
          !isPathInside(normalizationPaths.root, normalizationSummaryJson)
        ) {
          throw new Error("VCF normalization produced an artifact outside the allowed normalization root.");
        }
        vcfPathForMatching = normalizedVcfPath;
        job.artifacts = {
          normalizedVcfPath,
          normalizedVariantsCsv,
          normalizationExcludedAuditCsv,
          normalizationSummaryJson,
        };
        job.result = { vcfNormalization: sanitizeVcfNormalizationResult(normalizationSummary) };
      }
      job.progress = 25;
      job.stage = "matching";
      job.stageProgress = 25;
      job.message = "Streaming VCF and matching canon targets";
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);

      const payload = {
        event: "heal.vcf_canon_match.requested",
        runId,
        uploadId: upload.uploadId,
        fileName: upload.fileName,
        canonRunId: canonManifest.runId,
        rsidResolutionRunId: resolutionManifest?.runId || null,
        adapter: canonSummary.adapter || null,
        schemaVersion: canonSummary.schemaVersion || null,
        canonCleanPath,
        rsidReadyPath,
        geneMasterPath,
        geneEnvelopeIndexPath,
        mergedFeatureIndexPath,
        vcfPath: vcfPathForMatching,
        normalizedVariantsCsv: job.artifacts?.normalizedVariantsCsv || "",
        vcfAssembly: resolvedVcfAssembly || null,
        outputDir,
        vcfParser: normalizeVcfParser(vcfParser),
        requestedAt: new Date().toISOString(),
      };
      const summary = await processVcfCanonMatch(payload, job);
      job.artifacts = {
        ...(job.artifacts || {}),
        canonCleanPath,
        sheetFinalConsolidatedCsv: summary.outputs?.sheetFinalConsolidatedCsv || "",
        vcfCandidatesCsv: summary.outputs?.vcfCandidatesCsv || "",
        vcfJoinedChrPosCsv: summary.outputs?.vcfJoinedChrPosCsv || "",
        sheetFinalMatchStrictCsv: summary.outputs?.sheetFinalMatchStrictCsv || "",
        sheetFinalMatchLikelyNeedsAltReviewCsv: summary.outputs?.sheetFinalMatchLikelyNeedsAltReviewCsv || "",
        sheetFinalMatchByPositionNeedsReviewCsv: summary.outputs?.sheetFinalMatchByPositionNeedsReviewCsv || "",
          sheetFinalNoVcfMatchByChrPosCsv: summary.outputs?.sheetFinalNoVcfMatchByChrPosCsv || "",
        };
        job.result = { ...(job.result || {}), ...sanitizeVcfCanonMatchResult(summary, upload) };
        if (isGeneModuleV2 && job.artifacts.normalizedVcfPath) {
          const temporaryVcfPath = path.resolve(job.artifacts.normalizedVcfPath);
          const normalizationSummaryPath = path.resolve(job.artifacts.normalizationSummaryJson || "");
          if (isPathInside(normalizationPaths.root, temporaryVcfPath)) {
            try {
              await unlink(temporaryVcfPath);
              delete job.artifacts.normalizedVcfPath;
              normalizationSummary.normalizedVcfPath = "";
              normalizationSummary.temporaryNormalizedVcf = {
                status: "removed_after_match",
                removedAt: new Date().toISOString(),
              };
              if (isPathInside(normalizationPaths.root, normalizationSummaryPath)) {
                await writeFile(normalizationSummaryPath, JSON.stringify(normalizationSummary, null, 2), "utf8");
              }
            } catch (error) {
              job.result.vcfNormalization = {
                ...(job.result.vcfNormalization || {}),
                temporaryVcfCleanupWarning: error.message || String(error),
              };
            }
          }
        }
        const matchCsvPath = path.resolve(job.artifacts.sheetFinalConsolidatedCsv);
      if (!isPathInside(paths.root, matchCsvPath)) {
        throw new Error("Match preparation input is outside the allowed match root.");
      }
      job.progress = 72;
      job.stage = "preparing";
      job.stageProgress = 10;
      job.message = "Preparing audit-ready match CSVs";
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);

      let preparationSummary = summary.matchPreparation || summary.preparationSummary || null;
      if (!preparationSummary) {
        const preparationPaths = matchPreparationPaths();
        await mkdir(preparationPaths.runs, { recursive: true });
        const preparationRunId = `match-prep-${runId}`;
        const preparationOutputDir = isGeneModuleV2
          ? jobStageDirectory(job.id, "preparation")
          : path.join(preparationPaths.runs, preparationRunId);
        await mkdir(preparationOutputDir, { recursive: true });
        const preparationPayload = {
          event: "heal.match_preparation.requested",
          runId: preparationRunId,
          matchRunId: runId,
          inputPath: matchCsvPath,
          outputDir: preparationOutputDir,
          requestedAt: new Date().toISOString(),
        };
        preparationSummary = await processMatchPreparation(preparationPayload, job);
      }
      job.artifacts.deliverableMinCsv = preparationSummary.outputs?.deliverableMinCsv || "";
      job.artifacts.deliverableAuditCsv = preparationSummary.outputs?.deliverableAuditCsv || "";
      job.result = {
        ...(job.result || {}),
        ...sanitizeVcfCanonMatchResult(summary, upload),
        matchPreparation: sanitizeMatchPreparationResult(preparationSummary),
      };
      if (isGeneModuleV2) {
        const aiTriagePathsRoot = aiTriagePaths();
        await mkdir(aiTriagePathsRoot.runs, { recursive: true });
        const aiTriageRunId = `ai-triage-${runId}`;
        const aiTriageOutputDir = jobStageDirectory(job.id, "ai-triage");
        await mkdir(aiTriageOutputDir, { recursive: true });
        job.progress = 86;
        job.stage = "triaging";
        job.stageProgress = 12;
        job.message = "Applying deterministic AI triage";
        job.updatedAt = new Date().toISOString();
        await persistVcfCanonJob(job);
        const aiTriagePayload = {
          event: "heal.ai_triage.requested",
          runId: aiTriageRunId,
          matchRunId: runId,
          inputPath: matchCsvPath,
          outputDir: aiTriageOutputDir,
          requestedAt: new Date().toISOString(),
        };
        const aiTriageSummary = await processAiTriage(aiTriagePayload, job);
        if (metadataCount(aiTriageSummary, "included_for_ai") <= 0) {
          throw new Error("AI triage produced zero rows eligible for canon schema v2.");
        }
        job.artifacts.aiTriageCsv = aiTriageSummary.outputs?.aiTriageCsv || "";
        job.artifacts.aiTriageExcludedAuditCsv = aiTriageSummary.outputs?.aiTriageExcludedAuditCsv || "";
        job.artifacts.aiTriageSummaryJson = aiTriageSummary.outputs?.aiTriageSummaryJson || "";
        job.result = {
          ...job.result,
          aiTriage: sanitizeAiTriageResult(aiTriageSummary),
        };

        job.progress = 90;
        job.stage = "enriching";
        job.stageProgress = 12;
        job.message = "Enriching AI-triaged gene-module variants";
        job.updatedAt = new Date().toISOString();
        await persistVcfCanonJob(job);

        const enrichmentPaths = variantEnrichmentPaths();
        await mkdir(enrichmentPaths.runs, { recursive: true });
        await mkdir(enrichmentPaths.cache, { recursive: true });
        const enrichmentRunId = `variant-enrichment-${runId}`;
        const enrichmentOutputDir = jobStageDirectory(job.id, "enrichment");
        await mkdir(enrichmentOutputDir, { recursive: true });
        Object.assign(job.artifacts, {
          v2EnrichmentVepBaseCsv: path.join(enrichmentOutputDir, "v2_enrichment_vep_base.csv"),
          v2EnrichmentResolutionAuditJsonl: path.join(enrichmentOutputDir, "v2_enrichment_resolution_audit.jsonl"),
          v2EnrichmentCompleteCsv: path.join(enrichmentOutputDir, "v2_enrichment_complete.csv"),
          v2EnrichmentVepOnlyAuditCsv: path.join(enrichmentOutputDir, "v2_enrichment_vep_only_audit.csv"),
          v2EnrichmentPhysicalMatrixCsv: path.join(enrichmentOutputDir, "v2_enrichment_physical_matrix.csv"),
          v2EnrichmentPhysicalEvidenceAuditJsonlGz: path.join(enrichmentOutputDir, "v2_enrichment_physical_evidence_audit.jsonl.gz"),
          v2EnrichmentModuleProjectionCsv: path.join(enrichmentOutputDir, "v2_enrichment_module_projection.csv"),
          enrichmentRetryQueueJsonl: path.join(enrichmentOutputDir, "enrichment_retry_queue.jsonl"),
          enrichmentIdentityResolutionSummaryJson: path.join(enrichmentOutputDir, "enrichment_identity_resolution_summary.json"),
          enrichmentPerformanceSummaryJson: path.join(enrichmentOutputDir, "enrichment_performance_summary.json"),
        });
        await persistVcfCanonJob(job);
        const aiTriageCsvPath = path.resolve(job.artifacts.aiTriageCsv || "");
        if (!isPathInside(aiTriagePathsRoot.root, aiTriageCsvPath)) {
          throw new Error("Variant enrichment input is outside the allowed AI triage root.");
        }
        const enrichmentPayload = {
          event: "heal.variant_enrichment.requested",
          runId: enrichmentRunId,
          matchRunId: runId,
          uploadId: upload.uploadId,
          fileName: upload.fileName,
          schemaVersion: "gene_module_v2",
          analysisMode: normalizeAnalysisMode(analysisMode),
          assembly: resolvedVcfAssembly,
          inputPath: aiTriageCsvPath,
        outputDir: enrichmentOutputDir,
        cacheDir: enrichmentPaths.cache,
        cachePath: enrichmentPaths.cacheV2,
        legacyCachePath: enrichmentPaths.legacyCache,
        normalizationSummaryPath: job.artifacts.normalizationSummaryJson,
          requestedAt: new Date().toISOString(),
        };
        const enrichmentSummary = await processVariantEnrichmentWithRetry(enrichmentPayload, job, 3);
        const enrichmentOutputs = variantEnrichmentOutputs(enrichmentSummary);
        job.artifacts.observedVariantEnrichmentCsv = enrichmentOutputs.observedVariantEnrichmentCsv;
        job.artifacts.observedVariantInterpretiveCsv = enrichmentOutputs.observedVariantInterpretiveCsv;
        job.artifacts.observedVariantEnrichmentPlusCsv = enrichmentOutputs.observedVariantEnrichmentPlusCsv;
        job.artifacts.v2EnrichmentVariantMasterCsv = enrichmentOutputs.v2EnrichmentVariantMasterCsv;
        job.artifacts.v2EnrichmentEvidenceAuditJsonl = enrichmentOutputs.v2EnrichmentEvidenceAuditJsonl;
        job.artifacts.enrichmentQualitySummaryJson = enrichmentOutputs.enrichmentQualitySummaryJson;
        job.artifacts.v2EnrichmentVepBaseCsv = enrichmentOutputs.v2EnrichmentVepBaseCsv || job.artifacts.v2EnrichmentVepBaseCsv || "";
        job.artifacts.v2EnrichmentResolutionAuditJsonl = enrichmentOutputs.v2EnrichmentResolutionAuditJsonl || job.artifacts.v2EnrichmentResolutionAuditJsonl || "";
        job.artifacts.v2EnrichmentCompleteCsv = enrichmentOutputs.v2EnrichmentCompleteCsv || job.artifacts.v2EnrichmentCompleteCsv || "";
        job.artifacts.v2EnrichmentVepOnlyAuditCsv = enrichmentOutputs.v2EnrichmentVepOnlyAuditCsv || job.artifacts.v2EnrichmentVepOnlyAuditCsv || "";
        job.artifacts.v2EnrichmentPhysicalMatrixCsv = enrichmentOutputs.v2EnrichmentPhysicalMatrixCsv || job.artifacts.v2EnrichmentPhysicalMatrixCsv || "";
        job.artifacts.v2EnrichmentPhysicalEvidenceAuditJsonlGz = enrichmentOutputs.v2EnrichmentPhysicalEvidenceAuditJsonlGz || job.artifacts.v2EnrichmentPhysicalEvidenceAuditJsonlGz || "";
        job.artifacts.v2EnrichmentModuleProjectionCsv = enrichmentOutputs.v2EnrichmentModuleProjectionCsv || job.artifacts.v2EnrichmentModuleProjectionCsv || "";
        job.artifacts.enrichmentRetryQueueJsonl = enrichmentOutputs.enrichmentRetryQueueJsonl || job.artifacts.enrichmentRetryQueueJsonl || "";
        job.artifacts.enrichmentIdentityResolutionSummaryJson = enrichmentOutputs.enrichmentIdentityResolutionSummaryJson || job.artifacts.enrichmentIdentityResolutionSummaryJson || "";
        job.artifacts.enrichmentPerformanceSummaryJson = enrichmentOutputs.enrichmentPerformanceSummaryJson || job.artifacts.enrichmentPerformanceSummaryJson || "";
        const requiredV2Artifacts = [
          job.artifacts.observedVariantEnrichmentPlusCsv,
          job.artifacts.enrichmentQualitySummaryJson,
          job.artifacts.v2EnrichmentPhysicalMatrixCsv,
          job.artifacts.v2EnrichmentPhysicalEvidenceAuditJsonlGz,
          job.artifacts.v2EnrichmentModuleProjectionCsv,
          job.artifacts.enrichmentIdentityResolutionSummaryJson,
        ];
        if (requiredV2Artifacts.some((artifactPath) => !artifactPath || !existsSync(artifactPath))) {
          throw new Error("Coordinate enrichment did not produce its required v2 artifacts.");
        }
        job.result = {
          ...job.result,
          variantEnrichment: sanitizeVariantEnrichmentResult(enrichmentSummary),
        };

        const refinementSummary = await runEvidenceRefinementForJob({
          job,
          runId,
          analysisMode,
          assembly: resolvedVcfAssembly,
          physicalMatrixPath: job.artifacts.v2EnrichmentPhysicalMatrixCsv,
          matchPath: job.artifacts.sheetFinalConsolidatedCsv,
          triagePath: job.artifacts.aiTriageCsv,
          triageExcludedPath: job.artifacts.aiTriageExcludedAuditCsv,
          canonCleanPath,
          normalizedVariantsPath: job.artifacts.normalizedVariantsCsv,
        });

        const qualityGate = enrichmentSummary.metadata?.qualityGate || {};
        const technicalPassed = qualityGate.technicalGate
          ? qualityGate.technicalGate.status === "pass"
          : qualityGate.status === "pass";
        const evidenceReady = qualityGate.evidenceReadinessGate
          ? qualityGate.evidenceReadinessGate.status === "pass"
          : qualityGate.evidenceReady !== false;
        const refinementGates = refinementSummary.gates || {};
        const refinementTechnicalPassed =
          refinementGates.conservationGate?.status === "pass" &&
          refinementGates.attributionGate?.status === "pass";
        if (!refinementTechnicalPassed) {
          job.status = "complete";
          job.progress = 100;
          job.stage = "evidence_refinement_quality_gate";
          job.stageProgress = 100;
          job.message = "Evidence refinement contract failed; review conservation and attribution artifacts";
          job.result = {
            ...job.result,
            metadata: {
              ...(job.result?.metadata || {}),
              downstream_supported: false,
              downstream_input: "v2_curated_physical_variant_matrix",
              enrichment_quality_gate: qualityGate,
              evidence_refinement_gate: refinementGates,
              technical_gate: qualityGate.technicalGate || { status: technicalPassed ? "pass" : "fail" },
              evidence_readiness_gate: qualityGate.evidenceReadinessGate || { status: evidenceReady ? "pass" : "fail" },
              curation_conservation_gate: refinementGates.conservationGate || { status: "fail" },
              curation_attribution_gate: refinementGates.attributionGate || { status: "fail" },
              downstream_message: "Curated evidence contracts did not reconcile. Grouped LLM1 is blocked until conservation and attribution gates pass.",
            },
          };
          return;
        }

        job.progress = 94;
        job.stage = "grouping_preparation";
        job.stageProgress = 15;
        job.message = "Preparing grouped gene-module payloads";
        job.updatedAt = new Date().toISOString();
        const groupedPrepPathsRoot = groupedInterpretationPrepPaths();
        await mkdir(groupedPrepPathsRoot.runs, { recursive: true });
        const groupedPrepRunId = `group-prep-${runId}`;
        const groupedPrepOutputDir = jobStageDirectory(job.id, "group-prep");
        await mkdir(groupedPrepOutputDir, { recursive: true });
        const enrichmentPlusPath = path.resolve(job.artifacts.curatedGeneModuleProjectionCsv || "");
        if (!isPathInside(RUNTIME_PATHS.runs, enrichmentPlusPath)) {
          throw new Error("Grouped interpretation prep input is outside the allowed enrichment root.");
        }
        const groupedPrepPayload = {
          event: "heal.grouped_interpretation_prep.requested",
          runId: groupedPrepRunId,
          matchRunId: runId,
          inputPath: enrichmentPlusPath,
          physicalMatrixPath: job.artifacts.curatedPhysicalMatrixCsv,
          canonicalStatusPath: job.artifacts.canonicalGeneModuleStatusCsv,
          clinvarAssertionsPath: job.artifacts.clinvarSubmitterAssertionsCsv,
          gwasClustersPath: job.artifacts.gwasEvidenceClustersCsv,
          publicationsPath: job.artifacts.publicationEvidenceCsv,
          mechanismRegistryPath: existsSync(HEAL_MECHANISM_REGISTRY_PATH) ? HEAL_MECHANISM_REGISTRY_PATH : "",
          outputDir: groupedPrepOutputDir,
          requestedAt: new Date().toISOString(),
        };
        const groupedPrepSummary = await processGroupedInterpretationPrep(groupedPrepPayload);
        if (metadataCount(groupedPrepSummary, "total_groups") <= 0) {
          throw new Error("Grouped interpretation prep produced zero gene-module groups for canon schema v2.");
        }
        const requiredGroupedV4Artifacts = [
          groupedPrepSummary.outputs?.groupPayloadsJsonlV4,
          groupedPrepSummary.outputs?.groupPayloadsCsvV4,
          groupedPrepSummary.outputs?.groupingSummaryJsonV4,
          groupedPrepSummary.outputs?.mechanismRegistryV1Csv,
          groupedPrepSummary.outputs?.llm1PilotManifestCsv,
        ];
        if (requiredGroupedV4Artifacts.some((artifactPath) => !artifactPath || !existsSync(artifactPath))) {
          throw new Error("Grouped interpretation prep did not produce its required v4 dry-run artifacts.");
        }
        job.artifacts.groupPayloadsJsonl = groupedPrepSummary.outputs?.groupPayloadsJsonl || "";
        job.artifacts.groupPayloadsCsv = groupedPrepSummary.outputs?.groupPayloadsCsv || "";
        job.artifacts.groupVariantDetailCsv = groupedPrepSummary.outputs?.groupVariantDetailCsv || "";
        job.artifacts.groupingSummaryJson = groupedPrepSummary.outputs?.groupingSummaryJson || "";
        job.artifacts.groupPayloadsJsonlV4 = groupedPrepSummary.outputs?.groupPayloadsJsonlV4 || "";
        job.artifacts.groupPayloadsCsvV4 = groupedPrepSummary.outputs?.groupPayloadsCsvV4 || "";
        job.artifacts.groupVariantDetailCsvV4 = groupedPrepSummary.outputs?.groupVariantDetailCsvV4 || "";
        job.artifacts.groupingSummaryJsonV4 = groupedPrepSummary.outputs?.groupingSummaryJsonV4 || "";
        job.artifacts.mechanismRegistryV1Csv = groupedPrepSummary.outputs?.mechanismRegistryV1Csv || "";
        job.artifacts.llm1PilotManifestCsv = groupedPrepSummary.outputs?.llm1PilotManifestCsv || "";
        job.stageProgress = 70;
        job.message = "Building bounded LLM1 v5 payloads and evidence coverage audits";
        job.updatedAt = new Date().toISOString();
        await persistVcfCanonJob(job);
        const groupedV5Summary = await processGroupedPayloadV5({
          event: "heal.grouped_payload_v5.requested",
          runId: `group-payload-v5-${runId}`,
          detailPath: groupedPrepSummary.outputs?.groupVariantDetailCsvV4,
          canonicalStatusPath: job.artifacts.canonicalGeneModuleStatusCsv,
          mechanismRegistryPath: groupedPrepSummary.outputs?.mechanismRegistryV1Csv,
          clinvarAssertionsPath: job.artifacts.clinvarSubmitterAssertionsCsv,
          gwasClustersPath: job.artifacts.gwasEvidenceClustersCsv,
          publicationsPath: job.artifacts.publicationEvidenceCsv,
          outputDir: groupedPrepOutputDir,
          tokenizerModel: LLM1_MODEL,
          requestedAt: new Date().toISOString(),
        });
        const requiredGroupedV5Artifacts = [
          groupedV5Summary.outputs?.groupPayloadsJsonlV5,
          groupedV5Summary.outputs?.groupPayloadsCsvV5,
          groupedV5Summary.outputs?.groupEvidencePacketsJsonlGz,
          groupedV5Summary.outputs?.groupEvidenceDigestsJsonl,
          groupedV5Summary.outputs?.groupTokenBudgetAuditCsv,
          groupedV5Summary.outputs?.groupEvidenceCoverageAuditCsv,
          groupedV5Summary.outputs?.groupCompressionErrorsCsv,
          groupedV5Summary.outputs?.groupCompressionSummaryJson,
          groupedV5Summary.outputs?.llm1PilotCandidateManifestV2Csv,
          groupedV5Summary.outputs?.groupPayloadSchemaV5Json,
        ];
        if (requiredGroupedV5Artifacts.some((artifactPath) => !artifactPath || !existsSync(artifactPath))) {
          throw new Error("Grouped interpretation prep did not produce its required v5 compression artifacts.");
        }
        job.artifacts.groupPayloadsJsonlV5 = groupedV5Summary.outputs?.groupPayloadsJsonlV5 || "";
        job.artifacts.groupPayloadsCsvV5 = groupedV5Summary.outputs?.groupPayloadsCsvV5 || "";
        job.artifacts.groupEvidencePacketsJsonlGz = groupedV5Summary.outputs?.groupEvidencePacketsJsonlGz || "";
        job.artifacts.groupEvidenceDigestsJsonl = groupedV5Summary.outputs?.groupEvidenceDigestsJsonl || "";
        job.artifacts.groupTokenBudgetAuditCsv = groupedV5Summary.outputs?.groupTokenBudgetAuditCsv || "";
        job.artifacts.groupEvidenceCoverageAuditCsv = groupedV5Summary.outputs?.groupEvidenceCoverageAuditCsv || "";
        job.artifacts.groupCompressionErrorsCsv = groupedV5Summary.outputs?.groupCompressionErrorsCsv || "";
        job.artifacts.groupCompressionSummaryJson = groupedV5Summary.outputs?.groupCompressionSummaryJson || "";
        job.artifacts.llm1PilotCandidateManifestV2Csv = groupedV5Summary.outputs?.llm1PilotCandidateManifestV2Csv || "";
        job.artifacts.groupPayloadSchemaV5Json = groupedV5Summary.outputs?.groupPayloadSchemaV5Json || "";
        job.result = {
          ...job.result,
          groupPrep: {
            ...sanitizeGroupedInterpretationPrepResult(groupedPrepSummary),
            payloadV5: sanitizeGroupedInterpretationPrepResult(groupedV5Summary),
            metadata: {
              ...(groupedPrepSummary.metadata || {}),
              ...(groupedV5Summary.metadata || {}),
            },
          },
        };

        job.stageProgress = 86;
        job.message = "Building production-candidate LLM1 v7 payloads and 180-group preflight";
        job.updatedAt = new Date().toISOString();
        await persistVcfCanonJob(job);
        const groupedV7Summary = await processGroupedPayloadV7(
          groupedPayloadV7Request(job, groupedPrepOutputDir, "heal.grouped_payload_v7.requested"),
        );
        const requiredGroupedV6Artifacts = [
          groupedV7Summary.outputs?.groupPayloadsJsonlV6,
          groupedV7Summary.outputs?.groupPayloadsCsvV6,
          groupedV7Summary.outputs?.groupPayloadSchemaV6Json,
          groupedV7Summary.outputs?.groupPayloadsJsonlV7,
          groupedV7Summary.outputs?.groupPayloadsCsvV7,
          groupedV7Summary.outputs?.groupPayloadSchemaV7Json,
          groupedV7Summary.outputs?.groupPreflightV7Csv,
          groupedV7Summary.outputs?.groupCardsV7Json,
          groupedV7Summary.outputs?.targetGeneConsequenceAuditCsv,
          groupedV7Summary.outputs?.alleleSpecificFrequencyAuditCsv,
          groupedV7Summary.outputs?.clinvarConditionConflictAuditCsv,
          groupedV7Summary.outputs?.groupTokenBudgetAuditV6Csv,
          groupedV7Summary.outputs?.llm1PilotCandidateManifestV3Csv,
          groupedV7Summary.outputs?.groupPayloadV6SummaryJson,
        ];
        if (requiredGroupedV6Artifacts.some((artifactPath) => !artifactPath || !existsSync(artifactPath))) {
          throw new Error("Grouped interpretation prep did not produce its required v6 compatibility and v7 artifacts.");
        }
        assignGroupedPayloadV7Artifacts(job, groupedV7Summary);
        job.result.groupPrep = {
          ...(job.result.groupPrep || {}),
          payloadV6: sanitizeGroupedInterpretationPrepResult({ ...groupedV7Summary, payloadSchemaVersion: "llm1_group_payload_v6" }),
          payloadV7: sanitizeGroupedInterpretationPrepResult(groupedV7Summary),
          metadata: {
            ...(job.result.groupPrep?.metadata || {}),
            ...(groupedV7Summary.metadata || {}),
          },
        };

        job.result.llm1InternalAuto = await runInternalAutoLlm1(job, groupedV7Summary);
        job.result.groupedPrototype = await runGroupedPrototypeForJob(job, { dryRun: false });

        job.status = "complete";
        job.progress = 100;
        job.stage = job.result.groupedPrototype?.status?.startsWith("prototype_demo")
          ? "grouped_prototype"
          : "grouping_preparation";
        job.stageProgress = 100;
        job.message =
          job.result.groupedPrototype?.status === "prototype_demo_ready_automatic"
            ? "Grouped prototype report completed"
            : job.result.llm1InternalAuto?.status === "valid"
            ? "LLM1 v7 internal-auto completed"
            : "LLM1 v7 preflight completed; automatic execution remains gated";
        job.result = {
          ...job.result,
          metadata: {
            ...(job.result?.metadata || {}),
            downstream_supported: job.result.groupedPrototype?.status === "prototype_demo_ready_automatic",
            downstream_input: job.result.groupedPrototype?.status === "prototype_demo_ready_automatic"
              ? "grouped_prototype_snapshot_105_human_signed"
              : "llm1_group_payload_v6_dry_run",
            downstream_message: job.result.groupedPrototype?.status === "prototype_demo_ready_automatic"
              ? "Prototype-only grouped LLM1, grouped LLM2, and Spanish report completed; formal validation remains pending."
              : "All grouped payload v6 artifacts were generated without LLM calls. The production lane remains gated.",
          },
        };
        return;
      }
      job.progress = 86;
      job.stage = "enriching";
      job.stageProgress = 12;
      job.message = "Enriching observed variants with external sources";
      job.updatedAt = new Date().toISOString();

      const enrichmentPaths = variantEnrichmentPaths();
      await mkdir(enrichmentPaths.runs, { recursive: true });
      await mkdir(enrichmentPaths.cache, { recursive: true });
      const enrichmentRunId = `variant-enrichment-${runId}`;
      const enrichmentOutputDir = path.join(enrichmentPaths.runs, enrichmentRunId);
      await mkdir(enrichmentOutputDir, { recursive: true });
      const auditCsvPath = path.resolve(job.artifacts.deliverableAuditCsv || "");
      const preparationPaths = matchPreparationPaths();
      if (!isPathInside(preparationPaths.root, auditCsvPath)) {
        throw new Error("Variant enrichment input is outside the allowed match preparation root.");
      }
      const enrichmentPayload = {
        event: "heal.variant_enrichment.requested",
        runId: enrichmentRunId,
        matchRunId: runId,
        uploadId: upload.uploadId,
        fileName: upload.fileName,
        inputPath: auditCsvPath,
        outputDir: enrichmentOutputDir,
        cacheDir: enrichmentPaths.cache,
        requestedAt: new Date().toISOString(),
      };
      const enrichmentSummary = await processVariantEnrichmentWithRetry(enrichmentPayload, job, 3);
      const enrichmentOutputs = variantEnrichmentOutputs(enrichmentSummary);
      job.artifacts.observedVariantEnrichmentCsv = enrichmentOutputs.observedVariantEnrichmentCsv;
      job.artifacts.observedVariantInterpretiveCsv = enrichmentOutputs.observedVariantInterpretiveCsv;
      job.artifacts.observedVariantEnrichmentPlusCsv = enrichmentOutputs.observedVariantEnrichmentPlusCsv;
      job.result = {
        ...sanitizeVcfCanonMatchResult(summary, upload),
        matchPreparation: sanitizeMatchPreparationResult(preparationSummary),
        variantEnrichment: sanitizeVariantEnrichmentResult(enrichmentSummary),
      };
      job.status = "complete";
      job.progress = 100;
      job.stage = "enriching";
      job.stageProgress = 100;
      job.message = "Variant enrichment completed";
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.error = error.message || String(error);
      job.message =
        job.stage === "normalizing"
          ? "VCF normalization failed"
          : isVariantEnrichmentStage(job.stage)
          ? "Variant enrichment failed"
          : job.stage === "grouping_preparation"
            ? "Grouped payload preparation failed"
            : job.stage === "grouped_individual_interpretation"
              ? "Grouped individual interpretation failed"
          : job.stage === "triaging"
            ? "AI triage failed"
          : job.stage === "preparing"
            ? "Match preparation failed"
            : "VCF-canon match failed";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
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

app.get("/api/vcf-canon-matches/:jobId/logs", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const requestedLimit = Number.parseInt(String(req.query.limit || "250"), 10);
  const limit = Math.min(500, Math.max(1, Number.isFinite(requestedLimit) ? requestedLimit : 250));
  const logPath = vcfCanonJobLogPath(job.id);
  const raw = await readFile(logPath, "utf8").catch(() => "");
  const lines = raw.split(/\r?\n/).filter(Boolean).slice(-limit);
  const logs = lines.map((line) => {
    try {
      return JSON.parse(line);
    } catch {
      return { timestamp: null, event: "raw_log", message: line };
    }
  });
  if (logs.length === 0) {
    logs.push({
      timestamp: job.createdAt || null,
      event: "historical_run",
      status: job.status || null,
      stage: job.stage || null,
      progress: job.progress ?? null,
      stageProgress: job.stageProgress ?? null,
      message: "Structured execution logs were not enabled for this historical run; inspect its stage artifacts and progress files.",
    });
  }
  if (String(req.query.download || "") === "1") {
    res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="${safeFileName(job.id)}.jsonl"`);
    res.send(raw);
    return;
  }
  res.json({ jobId: job.id, logs });
});

function parseCsvRecords(raw) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < raw.length; index += 1) {
    const char = raw[index];
    if (char === '"') {
      if (quoted && raw[index + 1] === '"') {
        field += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === "," && !quoted) {
      row.push(field);
      field = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && raw[index + 1] === "\n") index += 1;
      row.push(field);
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  const headers = (rows.shift() || []).map((value) => value.replace(/^\uFEFF/, "").trim());
  return rows.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}

function requireCurationAccess(req, res) {
  if (!HEAL_CURATION_ACCESS_TOKEN) {
    res.status(503).json({ error: "Internal scientific curation is not configured on this deployment." });
    return false;
  }
  if (!tokenMatches(HEAL_CURATION_ACCESS_TOKEN, String(req.headers["x-heal-curation-token"] || ""))) {
    res.status(403).json({ error: "A valid internal scientific curation token is required." });
    return false;
  }
  return true;
}

function validateCurationCsv(kind, rows) {
  if (!rows.length) throw new Error("The uploaded curation CSV is empty.");
  const required = {
    mechanism: ["mechanism_registry_version", "gene", "module_id", "curation_status", "source_ids_or_urls", "reviewer", "reviewed_at"],
    gwas: ["registry_version", "gene", "approved_symbol", "module_id", "trait_id", "relevance_status", "relevance_reason", "source_ids_or_urls", "reviewer", "reviewed_at"],
    manifest: ["group_id", "approved_for_pilot", "approval_reviewer", "approval_timestamp"],
  }[kind];
  if (!required) throw new Error("Unsupported curation registry kind.");
  const headers = new Set(Object.keys(rows[0] || {}));
  const missing = required.filter((header) => !headers.has(header));
  if (missing.length) throw new Error(`Curation CSV is missing required columns: ${missing.join(", ")}`);
  if (kind === "manifest") {
    const allowed = new Set(["MTHFR:T1.1", "PEMT:T1.3", "IL6:T1.4", "ABCB1:T1.6", "IFNG:T3.5"]);
    const uploadedGroups = new Set(rows.map((row) => row.group_id));
    if (
      rows.length !== 5 ||
      uploadedGroups.size !== allowed.size ||
      rows.some((row) => !allowed.has(row.group_id))
    ) {
      throw new Error("The v6 canary manifest must contain exactly the five configured gene-module groups.");
    }
    if (rows.some((row) => !["true", "false"].includes(row.approved_for_pilot))) {
      throw new Error("approved_for_pilot must be either true or false for every canary group.");
    }
    for (const row of rows.filter((item) => item.approved_for_pilot === "true")) {
      if (!row.approval_reviewer || !row.approval_timestamp) {
        throw new Error(`Approved pilot group ${row.group_id} is missing reviewer or approval timestamp.`);
      }
    }
  } else {
    const statusField = kind === "mechanism" ? "curation_status" : "relevance_status";
    const allowedStatuses = kind === "mechanism"
      ? new Set(["draft", "approved", "approved_with_conflict", "withheld", "rejected"])
      : new Set(["unreviewed", "approved", "valid_but_excluded", "rejected"]);
    for (const row of rows) {
      if (!allowedStatuses.has(row[statusField])) throw new Error(`Unsupported ${statusField}: ${row[statusField]}`);
      if (!["draft", "unreviewed"].includes(row[statusField])) {
        const sourcesRequired = kind === "mechanism"
          ? ["approved", "approved_with_conflict", "rejected"].includes(row[statusField])
          : ["approved", "valid_but_excluded"].includes(row[statusField]);
        if (!row.reviewer || !row.reviewed_at || (sourcesRequired && !row.source_ids_or_urls)) {
          throw new Error(`Resolved ${kind} row is missing reviewer, reviewed_at, or required sources.`);
        }
        if (kind === "gwas" && !row.relevance_reason) {
          throw new Error("Professionally reviewed GWAS relevance rows require relevance_reason.");
        }
      }
    }
  }
}

function curationRowKey(kind, row) {
  if (kind === "mechanism") return `${row.gene || ""}:${row.module_id || ""}`;
  if (kind === "gwas") {
    return `${row.gene || row.approved_symbol || ""}:${row.module_id || ""}:${row.trait_id || ""}`;
  }
  return row.group_id || "";
}

async function validateCompleteCurationRegistry(kind, rows, expectedPath) {
  if (!expectedPath || !existsSync(expectedPath)) return;
  const expectedRows = parseCsvRecords(await readFile(expectedPath, "utf8"));
  const expectedKeys = new Set(expectedRows.map((row) => curationRowKey(kind, row)));
  const uploadedKeys = new Set(rows.map((row) => curationRowKey(kind, row)));
  if (
    rows.length !== uploadedKeys.size ||
    uploadedKeys.size !== expectedKeys.size ||
    [...expectedKeys].some((key) => !uploadedKeys.has(key))
  ) {
    throw new Error(
      `The ${kind} upload must preserve every row from the downloaded registry; edit review fields without removing, duplicating, or adding keys.`,
    );
  }
}

function groupedPayloadV7Request(job, outputDir, event) {
  return {
    event,
    runId: `group-payload-v7-${job.id}`,
    detailPath: job.artifacts.groupVariantDetailCsvV4,
    canonicalStatusPath: job.artifacts.canonicalGeneModuleStatusCsv,
    mechanismRegistryPath: existsSync(HEAL_MECHANISM_REGISTRY_PATH)
      ? HEAL_MECHANISM_REGISTRY_PATH
      : job.artifacts.mechanismRegistryV1Csv,
    persistentMechanismRegistryPath: HEAL_MECHANISM_REGISTRY_PATH,
    persistentGwasRegistryPath: HEAL_GWAS_TRAIT_MODULE_MAP_PATH,
    clinvarAssertionsPath: job.artifacts.clinvarSubmitterAssertionsCsv,
    gwasClustersPath: job.artifacts.gwasEvidenceClustersCsv,
    publicationsPath: job.artifacts.publicationEvidenceCsv,
    outputDir,
    tokenizerModel: LLM1_MODEL,
    assembly: job.vcfAssembly || "GRCh38",
    inputCompleteness: {
      mode: "observed_variants_only",
      source_type: "vcf",
      reference_build: job.vcfAssembly || "GRCh38",
      reference_version: "HEAL_GRCh38_runtime_reference",
      sample_count: 1,
      callability_method: "not_available",
      can_assert_hom_ref: false,
      can_assert_not_callable: false,
      absence_semantics: "not_observed_callability_unknown",
    },
    ageBand: "age_0_7",
    activeAgeBands: HEAL_LLM1_ACTIVE_AGE_BANDS,
    activeTiers: HEAL_LLM1_ACTIVE_TIERS,
    experimentalCanaryGroups: HEAL_LLM1_EXPERIMENTAL_CANARIES,
    curationSnapshotId: HEAL_LLM1_CURATION_SNAPSHOT_ID,
    humanReviewFoundationPath: HEAL_TIER1_HUMAN_REVIEW_FOUNDATION,
    requestedAt: new Date().toISOString(),
  };
}

function assignGroupedPayloadV7Artifacts(job, summary) {
  const outputs = summary.outputs || {};
  Object.assign(job.artifacts, {
    groupPayloadsJsonlV6: outputs.groupPayloadsJsonlV6 || "",
    groupPayloadsCsvV6: outputs.groupPayloadsCsvV6 || "",
    groupPayloadSchemaV6Json: outputs.groupPayloadSchemaV6Json || "",
    groupPayloadsJsonlV7: outputs.groupPayloadsJsonlV7 || "",
    groupPayloadsCsvV7: outputs.groupPayloadsCsvV7 || "",
    groupPayloadSchemaV7Json: outputs.groupPayloadSchemaV7Json || "",
    groupPreflightV7Csv: outputs.groupPreflightV7Csv || "",
    groupCardsV7Json: outputs.groupCardsV7Json || "",
    groupPayloadV7SummaryJson: outputs.groupPayloadV7SummaryJson || "",
    targetGeneConsequenceAuditCsv: outputs.targetGeneConsequenceAuditCsv || "",
    alleleSpecificFrequencyAuditCsv: outputs.alleleSpecificFrequencyAuditCsv || "",
    clinvarConditionConflictAuditCsv: outputs.clinvarConditionConflictAuditCsv || "",
    groupTokenBudgetAuditV6Csv: outputs.groupTokenBudgetAuditV6Csv || "",
    llm1PilotCandidateManifestV3Csv: outputs.llm1PilotCandidateManifestV3Csv || "",
    groupPayloadV6ErrorsCsv: outputs.groupPayloadV6ErrorsCsv || "",
    groupPayloadV6SummaryJson: outputs.groupPayloadV6SummaryJson || "",
    persistentMechanismRegistryCsv: outputs.persistentMechanismRegistryCsv || "",
    persistentGwasRegistryCsv: outputs.persistentGwasRegistryCsv || "",
  });
}

async function buildCombinedLlm1Cards(job, summary) {
  const basePath = path.resolve(job.artifacts?.groupCardsV7Json || "");
  const interpretationPath = path.resolve(summary.outputs?.groupInterpretationsJsonl || "");
  const errorPath = path.resolve(summary.outputs?.groupInterpretationErrorsCsv || "");
  const cards = existsSync(basePath) ? JSON.parse(await readFile(basePath, "utf8")) : [];
  const interpretations = existsSync(interpretationPath)
    ? (await readFile(interpretationPath, "utf8")).split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line))
    : [];
  const errors = existsSync(errorPath) ? parseCsvRecords(await readFile(errorPath, "utf8")) : [];
  const interpretationByGroup = new Map(interpretations.map((row) => [row.group_id, row]));
  const errorByGroup = new Map(errors.map((row) => [row.group_id, row]));
  const combined = cards.map((card) => {
    const interpretation = interpretationByGroup.get(card.group_id);
    const error = errorByGroup.get(card.group_id);
    if (interpretation) return { ...card, status: "valid", coverage_status: "interpretable", interpretation };
    if (error) {
      const quarantined = String(error.error || "").includes("critical_semantic_error:");
      return {
        ...card,
        status: quarantined ? "quarantined" : "technical_failure",
        coverage_status: "result_unavailable",
        interpretation: null,
        error_code: quarantined ? "critical_semantic_error" : "technical_failure",
      };
    }
    return { ...card, interpretation: null };
  });
  const outputPath = path.join(path.resolve(summary.outputDir), "llm1_group_cards_combined.json");
  await writeFile(outputPath, JSON.stringify(combined, null, 2), "utf8");
  job.artifacts.groupCardsJson = outputPath;
  return combined;
}

async function runInternalAutoLlm1(job, v7Summary) {
  const enabled = HEAL_V2_LLM1_ENABLED && HEAL_LLM1_EXECUTION_MODE === "internal_auto";
  if (!enabled) return { status: "disabled", reason: "internal_auto_not_enabled" };
  if (v7Summary.gates?.llm1InternalAutoReady !== "pass") {
    return { status: "blocked", reason: "v7_preflight_or_tier1_curation_incomplete" };
  }
  const payloadPath = path.resolve(v7Summary.outputs?.groupPayloadsJsonlV7 || "");
  if (!isPathInside(RUNTIME_PATHS.runs, payloadPath) || !existsSync(payloadPath)) {
    throw new Error("V7 payloads are missing or outside the HEAL run root.");
  }
  const allPayloads = (await readFile(payloadPath, "utf8"))
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const eligible = allPayloads
    .filter((item) => item.operational_state?.status === "eligible" && item.operational_state?.llm1_eligible === true)
    .map((item) => ({
      ...item,
      execution_mode: "internal_auto",
      operational_state: { ...item.operational_state, status: "llm1_running" },
    }));
  if (eligible.length === 0) return { status: "blocked", reason: "no_eligible_groups" };
  const outputDir = jobStageDirectory(job.id, "llm1-internal");
  await mkdir(outputDir, { recursive: true });
  const selectedPath = path.join(outputDir, "llm1_internal_selected_payloads_v7.jsonl");
  await writeFile(selectedPath, `${eligible.map((item) => JSON.stringify(item)).join("\n")}\n`, "utf8");
  job.artifacts.llm1InternalSelectedPayloadsJsonl = selectedPath;
  job.stage = "grouped_individual_interpretation";
  job.stageProgress = 1;
  job.message = `Running Luna for ${eligible.length} eligible internal groups`;
  await persistVcfCanonJob(job);
  const summary = await processGroupedIndividualInterpretationWithRetry(
    {
      event: "heal.llm1_internal_auto.requested",
      runId: `llm1-internal-${job.id}`,
      inputPath: selectedPath,
      outputDir,
      model: LLM1_MODEL,
      promptProfile: LLM1_PROMPT_PROFILE,
      reasoningEffort: LLM1_REASONING_EFFORT,
      dryRun: false,
      maxGroups: 0,
      maxWorkers: 2,
      groupAttempts: 2,
      requestedAt: new Date().toISOString(),
    },
    job,
    1,
  );
  const outputs = summary.outputs || {};
  Object.assign(job.artifacts, {
    groupInterpretationsJsonl: outputs.groupInterpretationsJsonl || "",
    groupInterpretationsCsv: outputs.groupInterpretationsCsv || "",
    groupInterpretationErrorsCsv: outputs.groupInterpretationErrorsCsv || "",
    groupInterpretationProgressJson: outputs.groupInterpretationProgressJson || "",
    groupInterpretationSummaryJson: outputs.groupInterpretationSummaryJson || "",
    groupInterpretationRawResponsesJsonl: outputs.groupInterpretationRawResponsesJsonl || "",
    groupInterpretationCallAuditCsv: outputs.groupInterpretationCallAuditCsv || "",
    groupQuarantineJsonl: outputs.groupQuarantineJsonl || "",
    llm1PilotPromptSnapshotMd: outputs.llm1PilotPromptSnapshotMd || "",
    llm1PilotResponseSchemaSnapshotJson: outputs.llm1PilotResponseSchemaSnapshotJson || "",
  });
  const cards = await buildCombinedLlm1Cards(job, summary);
  return {
    ...sanitizeGroupedIndividualInterpretationResult(summary),
    status: summary.metadata?.error_groups > 0 ? "partial" : "valid",
    card_count: cards.length,
    quarantined_groups: cards.filter((card) => card.status === "quarantined").length,
    technical_failure_groups: cards.filter((card) => card.status === "technical_failure").length,
  };
}

async function runGroupedPrototypeForJob(job, { dryRun = false } = {}) {
  if (!HEAL_GROUPED_PROTOTYPE_ENABLED || HEAL_PROTOTYPE_EXECUTION_MODE !== "internal_quick") {
    return { status: "disabled", reason: "grouped_prototype_not_enabled" };
  }
  if (HEAL_PROTOTYPE_LLM1_MODEL !== "gpt-5.6-luna" || HEAL_PROTOTYPE_LLM2_MODEL !== "gpt-5.6-luna") {
    throw new Error("The isolated grouped prototype requires Luna for both LLM1 and LLM2.");
  }
  if (normalizeAnalysisMode(job.analysisMode || "quick") !== "quick") {
    return { status: "blocked", reason: "prototype_is_available_only_in_quick_mode" };
  }
  const payloadPath = path.resolve(job.artifacts?.groupPayloadsJsonlV7 || "");
  if (!isPathInside(RUNTIME_PATHS.runs, payloadPath) || !existsSync(payloadPath)) {
    throw new Error("V7 grouped payloads are unavailable for the prototype.");
  }
  for (const artifactPath of [
    HEAL_PROTOTYPE_SNAPSHOT_PATH,
    HEAL_PROTOTYPE_CANDIDATE_MANIFEST_PATH,
    HEAL_PROTOTYPE_COVERAGE_MANIFEST_PATH,
  ]) {
    const resolved = path.resolve(artifactPath);
    if (!isPathInside(DATA_ROOT, resolved) || !existsSync(resolved)) {
      throw new Error("A sealed prototype snapshot artifact is missing or outside HEAL data.");
    }
  }
  const outputDir = jobStageDirectory(job.id, "grouped-prototype");
  await mkdir(outputDir, { recursive: true });
  const progressPath = path.join(outputDir, "grouped_prototype_progress.json");
  job.stage = "grouped_prototype";
  job.stageProgress = 5;
  job.message = "Running isolated grouped prototype with Luna";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
  const summary = await processGroupedPrototype({
    event: "heal.grouped_prototype.requested",
    runId: `grouped-prototype-${job.id}`,
    payloadPath,
    outputDir,
    snapshotPath: HEAL_PROTOTYPE_SNAPSHOT_PATH,
    candidateManifestPath: HEAL_PROTOTYPE_CANDIDATE_MANIFEST_PATH,
    coverageManifestPath: HEAL_PROTOTYPE_COVERAGE_MANIFEST_PATH,
    progressPath,
    maxEstimatedCostUsd: HEAL_PROTOTYPE_MAX_ESTIMATED_COST_USD,
    hardCapUsd: HEAL_PROTOTYPE_HARD_CAP_USD,
    fileName: job.fileName || `${job.id}.vcf`,
    dryRun,
    requestedAt: new Date().toISOString(),
  }, { job, progressPath, stage: "grouped_prototype" });
  Object.assign(job.artifacts, {
    groupedPrototypeSummaryJson: path.join(outputDir, "grouped_prototype_run_summary.json"),
    groupedPrototypeDocx: path.join(outputDir, "HEAL_prototipo_desarrollo.docx"),
    groupedPrototypePdf: path.join(outputDir, "HEAL_prototipo_desarrollo.pdf"),
    groupedPrototypeCardsCsv: path.join(outputDir, "cards.csv"),
    groupedPrototypeCoverageCsv: path.join(outputDir, "coverage.csv"),
    groupedPrototypeTelemetryCsv: path.join(outputDir, "telemetry_costs.csv"),
    groupedPrototypeTechnicalAuditJson: path.join(outputDir, "raw_responses_audit.json"),
    groupedPrototypeCardsJson: path.join(outputDir, "llm1_cards.json"),
  });
  job.result = { ...job.result, groupedPrototype: summary };
  job.stageProgress = 100;
  return summary;
}

async function regenerateGroupedPayloadV6(job) {
  const outputDir = path.dirname(path.resolve(job.artifacts?.groupPayloadsJsonlV5 || ""));
  const requiredInputs = [
    job.artifacts?.groupVariantDetailCsvV4,
    job.artifacts?.canonicalGeneModuleStatusCsv,
    job.artifacts?.clinvarSubmitterAssertionsCsv,
    job.artifacts?.gwasEvidenceClustersCsv,
    job.artifacts?.publicationEvidenceCsv,
  ].map((value) => path.resolve(value || ""));
  if (!isPathInside(RUNTIME_PATHS.runs, outputDir) || requiredInputs.some((value) => !isPathInside(RUNTIME_PATHS.runs, value) || !existsSync(value))) {
    throw new Error("V6 regeneration inputs are incomplete or outside the HEAL run root.");
  }
  const summary = await processGroupedPayloadV7(groupedPayloadV7Request(job, outputDir, "heal.grouped_payload_v7.regenerate"));
  assignGroupedPayloadV7Artifacts(job, summary);
  job.result = {
    ...job.result,
    groupPrep: {
      ...(job.result?.groupPrep || {}),
      payloadV6: sanitizeGroupedInterpretationPrepResult({ ...summary, payloadSchemaVersion: "llm1_group_payload_v6" }),
      payloadV7: sanitizeGroupedInterpretationPrepResult(summary),
      metadata: { ...(job.result?.groupPrep?.metadata || {}), ...(summary.metadata || {}) },
    },
  };
  job.result.llm1InternalAuto = await runInternalAutoLlm1(job, summary);
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
  return summary;
}

app.post("/api/vcf-canon-matches/:jobId/grouped-prototype", async (req, res) => {
  if (!HEAL_GROUPED_PROTOTYPE_ENABLED || HEAL_PROTOTYPE_EXECUTION_MODE !== "internal_quick") {
    res.status(409).json({ error: "The grouped prototype is disabled." });
    return;
  }
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  const dryRun = req.body?.dryRun === true;
  if (dryRun && !ALLOW_LLM_DRY_RUN) {
    res.status(409).json({ error: "LLM dry-run mode is disabled." });
    return;
  }
  job.status = "running";
  job.progress = 90;
  job.error = null;
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
  (async () => {
    try {
      const summary = await runGroupedPrototypeForJob(job, { dryRun });
      job.status = summary.status === "prototype_demo_ready_automatic" ? "complete" : "failed";
      job.progress = 100;
      job.stageProgress = 100;
      job.message = summary.status === "prototype_demo_ready_automatic"
        ? "Grouped prototype report completed"
        : "Grouped prototype completed with isolated failures";
      if (job.status === "failed") job.error = summary.status;
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.stage = "grouped_prototype";
      job.stageProgress = 100;
      job.error = error.message || String(error);
      job.message = "Grouped prototype failed";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();
  res.status(202).json(publicJob(job));
});

app.get("/api/vcf-canon-matches/:jobId/grouped-prototype/download/:artifact", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  const definitions = {
    docx: ["groupedPrototypeDocx", "HEAL_prototipo_desarrollo.docx"],
    pdf: ["groupedPrototypePdf", "HEAL_prototipo_desarrollo.pdf"],
    cards: ["groupedPrototypeCardsCsv", "HEAL_prototipo_tarjetas.csv"],
    coverage: ["groupedPrototypeCoverageCsv", "HEAL_prototipo_cobertura.csv"],
    telemetry: ["groupedPrototypeTelemetryCsv", "HEAL_prototipo_telemetria.csv"],
  };
  const definition = definitions[String(req.params.artifact || "").toLowerCase()];
  if (!definition) {
    res.status(404).json({ error: "Unknown grouped prototype artifact." });
    return;
  }
  const artifactPath = path.resolve(job.artifacts?.[definition[0]] || "");
  if (!isPathInside(GROUPED_PROTOTYPE_ROOT, artifactPath) || !existsSync(artifactPath)) {
    res.status(404).json({ error: "Grouped prototype artifact is not ready." });
    return;
  }
  res.download(artifactPath, definition[1]);
});

app.get("/api/vcf-canon-matches/:jobId/grouped-prototype/cards", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  const cardsPath = path.resolve(job.artifacts?.groupedPrototypeCardsJson || "");
  if (!isPathInside(GROUPED_PROTOTYPE_ROOT, cardsPath) || !existsSync(cardsPath)) {
    res.status(404).json({ error: "Grouped prototype cards are not ready." });
    return;
  }
  const cards = JSON.parse(await readFile(cardsPath, "utf8"));
  res.json(cards.map((card) => ({
    ...card,
    tier: String(card.module_id || "").startsWith("T1.") ? "T1" : "inactive",
    client_visible: card.status === "valid",
    experimental_canary: false,
    interpretation: card.status === "valid" ? card : null,
    input_completeness: { mode: card.input_completeness_mode || "observed_variants_only" },
    focus_variant_count: (card.focus_variant_refs || []).length,
    focus_variants: card.focus_variant_refs || [],
    decision_reason: card.interpretation_one_sentence_es || "",
  })));
});

app.post("/api/vcf-canon-matches/:jobId/curation/:kind", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  const kind = String(req.params.kind || "").toLowerCase();
  try {
    const raw = Buffer.from(String(req.body?.csvBase64 || ""), "base64").toString("utf8");
    const rows = parseCsvRecords(raw);
    validateCurationCsv(kind, rows);
    if (kind === "mechanism") {
      await validateCompleteCurationRegistry(
        kind,
        rows,
        job.artifacts?.persistentMechanismRegistryCsv || job.artifacts?.mechanismRegistryV1Csv,
      );
    } else if (kind === "gwas") {
      await validateCompleteCurationRegistry(
        kind,
        rows,
        job.artifacts?.persistentGwasRegistryCsv || job.artifacts?.gwasTraitModuleRelevanceTemplateCsv,
      );
    }
    let destination;
    if (kind === "mechanism") destination = HEAL_MECHANISM_REGISTRY_PATH;
    else if (kind === "gwas") destination = HEAL_GWAS_TRAIT_MODULE_MAP_PATH;
    else if (kind === "manifest") destination = job.artifacts?.llm1PilotCandidateManifestV3Csv;
    else throw new Error("Unsupported curation registry kind.");
    const resolvedDestination = path.resolve(destination || "");
    const allowedRoot = kind === "manifest" ? RUNTIME_PATHS.runs : RUNTIME_PATHS.canonCuration;
    if (!isPathInside(allowedRoot, resolvedDestination)) throw new Error("Curation destination is outside the allowed HEAL root.");
    await mkdir(path.dirname(resolvedDestination), { recursive: true });
    const temporary = `${resolvedDestination}.${process.pid}.tmp`;
    await writeFile(temporary, raw, "utf8");
    if (kind !== "manifest") {
      const revisionHash = crypto.createHash("sha256").update(raw).digest("hex").slice(0, 16);
      const revisionTimestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const historyDir = path.join(RUNTIME_PATHS.canonCuration, "history");
      await mkdir(historyDir, { recursive: true });
      await writeFile(path.join(historyDir, `${kind}-${revisionTimestamp}-${revisionHash}.csv`), raw, "utf8");
    }
    await rename(temporary, resolvedDestination);
    const summary = kind === "manifest" ? null : await regenerateGroupedPayloadV6(job);
    res.json({ status: "accepted", kind, regenerated: Boolean(summary), metadata: summary?.metadata || null });
  } catch (error) {
    res.status(400).json({ error: error.message || String(error) });
  }
});

app.post("/api/vcf-canon-matches/:jobId/llm1-preflight-v7", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  try {
    const summary = await regenerateGroupedPayloadV6(job);
    res.json({
      status: summary.status,
      payloadSchemaVersion: summary.payloadSchemaVersion,
      metadata: summary.metadata,
      gates: summary.gates,
      llm1InternalAuto: job.result?.llm1InternalAuto || null,
    });
  } catch (error) {
    res.status(400).json({ error: error.message || String(error) });
  }
});

app.post("/api/vcf-canon-matches/:jobId/llm1-pilot", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }
  if (!HEAL_V2_LLM1_PILOT_ENABLED) {
    res.status(409).json({ error: "The v2 LLM1 pilot is disabled. Set HEAL_V2_LLM1_PILOT_ENABLED=true only for an approved pilot." });
    return;
  }
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  const payloadPath = path.resolve(job.artifacts?.groupPayloadsJsonlV6 || "");
  const manifestPath = path.resolve(job.artifacts?.llm1PilotCandidateManifestV3Csv || "");
  const groupedRoot = groupedInterpretationPrepPaths().root;
  if (!isPathInside(groupedRoot, payloadPath) || !existsSync(payloadPath) || !isPathInside(groupedRoot, manifestPath) || !existsSync(manifestPath)) {
    res.status(409).json({ error: "Grouped payload v6 or its five-group pilot manifest is not available." });
    return;
  }
  const manifestRows = parseCsvRecords(await readFile(manifestPath, "utf8"));
  const approvedIds = new Set(
    manifestRows
      .filter((row) => row.approved_for_pilot === "true" && row.approval_reviewer && row.approval_timestamp)
      .map((row) => row.group_id),
  );
  const requestedIds = new Set(Array.isArray(req.body?.groupIds) ? req.body.groupIds.map(String) : []);
  const payloads = (await readFile(payloadPath, "utf8"))
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const selected = payloads.filter(
    (payload) =>
      approvedIds.has(payload.group_id) &&
      payload.payload_schema_version === "llm1_group_payload_v6" &&
      payload.execution_mode === "dry_run" &&
      Number(payload.compression_metadata?.estimated_tokens || 0) <= 25000 &&
      payload.gates?.group_payload_ready === true &&
      (requestedIds.size === 0 || requestedIds.has(payload.group_id)),
  ).map((payload) => ({
    ...payload,
    execution_mode: "pilot",
    gates: { ...payload.gates, llm1_pilot_ready: true },
  }));
  if (selected.length === 0) {
    res.status(409).json({ error: "No professionally approved, payload-ready groups are present in the pilot manifest." });
    return;
  }
  if (selected.length > 5) {
    res.status(409).json({ error: "The controlled LLM1 canary is limited to five approved groups." });
    return;
  }
  const outputDir = jobStageDirectory(job.id, "llm1-pilot");
  await mkdir(outputDir, { recursive: true });
  const selectedPath = path.join(outputDir, "llm1_pilot_selected_payloads_v6.jsonl");
  await writeFile(selectedPath, `${selected.map((payload) => JSON.stringify(payload)).join("\n")}\n`, "utf8");
  job.artifacts.llm1PilotApprovedPayloadsJsonl = selectedPath;
  job.stage = "grouped_individual_interpretation";
  job.stageProgress = 1;
  job.message = `Starting controlled LLM1 pilot for ${selected.length} approved groups`;
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
  void (async () => {
    try {
      const summary = await processGroupedIndividualInterpretationWithRetry(
        {
          event: "heal.llm1_pilot.requested",
          runId: `llm1-pilot-${job.id}`,
          inputPath: selectedPath,
          outputDir,
          model: LLM1_MODEL,
          promptProfile: LLM1_PROMPT_PROFILE,
          reasoningEffort: LLM1_REASONING_EFFORT,
          dryRun: false,
          maxGroups: 5,
          maxWorkers: 2,
          groupAttempts: 2,
          requestedAt: new Date().toISOString(),
        },
        job,
        1,
      );
      job.artifacts.groupInterpretationsJsonl = summary.outputs?.groupInterpretationsJsonl || "";
      job.artifacts.groupInterpretationsCsv = summary.outputs?.groupInterpretationsCsv || "";
      job.artifacts.groupInterpretationErrorsCsv = summary.outputs?.groupInterpretationErrorsCsv || "";
      job.artifacts.groupInterpretationProgressJson = summary.outputs?.groupInterpretationProgressJson || "";
      job.artifacts.groupInterpretationSummaryJson = summary.outputs?.groupInterpretationSummaryJson || "";
      job.artifacts.groupInterpretationRawResponsesJsonl = summary.outputs?.groupInterpretationRawResponsesJsonl || "";
      job.artifacts.groupInterpretationCallAuditCsv = summary.outputs?.groupInterpretationCallAuditCsv || "";
      job.artifacts.llm1PilotPromptSnapshotMd = summary.outputs?.llm1PilotPromptSnapshotMd || "";
      job.artifacts.llm1PilotResponseSchemaSnapshotJson = summary.outputs?.llm1PilotResponseSchemaSnapshotJson || "";
      job.result = { ...job.result, groupedIndividualInterpretation: sanitizeGroupedIndividualInterpretationResult(summary) };
      job.status = "complete";
      job.stageProgress = 100;
      job.message = "Controlled LLM1 pilot completed; downstream v2 stages remain blocked";
    } catch (error) {
      job.status = "complete";
      job.result = {
        ...job.result,
        llm1Pilot: { status: "failed", error: error.message || String(error) },
      };
      job.message = "Controlled LLM1 pilot failed; deterministic v2 artifacts remain valid";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();
  res.status(202).json({ jobId: job.id, status: "running", selectedGroups: selected.length });
});

app.post("/api/vcf-canon-matches/:jobId/llm1-internal-review", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const decision = String(req.body?.decision || "").trim().toLowerCase();
  const reviewer = String(req.body?.reviewer || "").trim();
  const notes = String(req.body?.notes || "").trim();
  if (!new Set(["approved", "rejected"]).has(decision) || !reviewer) {
    res.status(400).json({ error: "decision=approved|rejected and reviewer are required." });
    return;
  }
  const cardsPath = path.resolve(job.artifacts?.groupCardsJson || job.artifacts?.groupCardsV7Json || "");
  const summaryPath = path.resolve(job.artifacts?.groupPayloadV7SummaryJson || "");
  if (
    !isPathInside(RUNTIME_PATHS.runs, cardsPath) ||
    !isPathInside(RUNTIME_PATHS.runs, summaryPath) ||
    !existsSync(cardsPath) ||
    !existsSync(summaryPath)
  ) {
    res.status(409).json({ error: "V7 cards or preflight summary are not available." });
    return;
  }
  const cards = JSON.parse(await readFile(cardsPath, "utf8"));
  const preflight = JSON.parse(await readFile(summaryPath, "utf8"));
  const releaseBlockingCards = cards.filter(
    (card) =>
      ["quarantined", "technical_failure", "preflight_blocked", "llm1_running"].includes(card.status) ||
      (card.client_visible === true && card.status !== "valid"),
  );
  const acceptanceReady =
    preflight.gates?.all180StructurallyPresent === "pass" &&
    preflight.gates?.tier1CurationComplete === "pass" &&
    releaseBlockingCards.length === 0;
  if (decision === "approved" && !acceptanceReady) {
    res.status(409).json({
      error: "The run cannot be approved until all 180 groups pass preflight, Tier 1 is classified, and every visible card is valid.",
      blockingGroups: releaseBlockingCards.map((card) => card.group_id),
    });
    return;
  }
  const review = {
    schema_version: "llm1_internal_review_v1",
    job_id: job.id,
    decision,
    reviewer,
    notes,
    reviewed_at: new Date().toISOString(),
    review_scope: "all_enabled_cards",
    acceptance_ready: acceptanceReady,
    total_cards: cards.length,
    client_visible_cards: cards.filter((card) => card.client_visible === true).length,
    experimental_canaries: cards.filter((card) => card.experimental_canary === true).length,
    blocking_groups: releaseBlockingCards.map((card) => card.group_id),
    payload_sha256: preflight.metadata?.payload_sha256 || "",
    curation_snapshot_id: HEAL_LLM1_CURATION_SNAPSHOT_ID,
  };
  const reviewPath = path.join(jobStageDirectory(job.id, "llm1-internal"), "llm1_internal_review.json");
  await mkdir(path.dirname(reviewPath), { recursive: true });
  await writeFile(reviewPath, JSON.stringify(review, null, 2), "utf8");
  job.artifacts.llm1InternalReviewJson = reviewPath;
  job.result = {
    ...job.result,
    llm1InternalReview: review,
    llm1Maturity: decision === "approved" ? "internal_stable" : "development_review_rejected",
  };
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
  res.json(review);
});

app.post("/api/vcf-canon-matches/:jobId/evidence-digest", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }
  if (!HEAL_V2_EVIDENCE_DIGEST_ENABLED || !HEAL_V2_EVIDENCE_DIGEST_MODEL) {
    res.status(409).json({
      error: "Evidence digest is disabled. Configure HEAL_V2_EVIDENCE_DIGEST_ENABLED=true and an explicit HEAL_V2_EVIDENCE_DIGEST_MODEL.",
    });
    return;
  }
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  const groupedRoot = groupedInterpretationPrepPaths().root;
  const packetsPath = path.resolve(job.artifacts?.groupEvidencePacketsJsonlGz || "");
  const tokenAuditPath = path.resolve(job.artifacts?.groupTokenBudgetAuditCsv || "");
  const detailPath = path.resolve(job.artifacts?.groupVariantDetailCsvV4 || "");
  if (
    ![packetsPath, tokenAuditPath, detailPath].every((artifactPath) => isPathInside(groupedRoot, artifactPath) && existsSync(artifactPath))
  ) {
    res.status(409).json({ error: "V5 evidence packets, token audit, or grouped detail are unavailable." });
    return;
  }
  const outputDir = path.dirname(packetsPath);
  const requestedGroupIds = Array.isArray(req.body?.groupIds) ? req.body.groupIds.map(String) : [];
  job.status = "running";
  job.stage = "evidence_digest";
  job.stageProgress = 1;
  job.message = "Generating citation-bound public evidence digests";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);
  void (async () => {
    try {
      const digestSummary = await processEvidenceDigest({
        packetsPath,
        tokenAuditPath,
        outputDir,
        groupIds: requestedGroupIds,
        model: HEAL_V2_EVIDENCE_DIGEST_MODEL,
        cachePath: path.join(RUNTIME_PATHS.enrichmentCache, "evidence_digest_cache.sqlite"),
      });
      const digestPath = digestSummary.outputs?.groupEvidenceDigestsJsonl || job.artifacts.groupEvidenceDigestsJsonl;
      const v5Summary = await processGroupedPayloadV5({
        detailPath,
        canonicalStatusPath: job.artifacts.canonicalGeneModuleStatusCsv,
        mechanismRegistryPath: job.artifacts.mechanismRegistryV1Csv,
        clinvarAssertionsPath: job.artifacts.clinvarSubmitterAssertionsCsv,
        gwasClustersPath: job.artifacts.gwasEvidenceClustersCsv,
        publicationsPath: job.artifacts.publicationEvidenceCsv,
        digestPath,
        outputDir,
        tokenizerModel: LLM1_MODEL,
      });
      job.artifacts.groupPayloadsJsonlV5 = v5Summary.outputs?.groupPayloadsJsonlV5 || job.artifacts.groupPayloadsJsonlV5;
      job.artifacts.groupPayloadsCsvV5 = v5Summary.outputs?.groupPayloadsCsvV5 || job.artifacts.groupPayloadsCsvV5;
      job.artifacts.groupEvidenceDigestsJsonl = digestPath || "";
      job.artifacts.groupEvidenceDigestErrorsCsv = digestSummary.outputs?.groupEvidenceDigestErrorsCsv || "";
      job.artifacts.groupCompressionSummaryJson = v5Summary.outputs?.groupCompressionSummaryJson || "";
      job.artifacts.groupTokenBudgetAuditCsv = v5Summary.outputs?.groupTokenBudgetAuditCsv || "";
      job.artifacts.groupEvidenceCoverageAuditCsv = v5Summary.outputs?.groupEvidenceCoverageAuditCsv || "";
      job.artifacts.llm1PilotCandidateManifestV2Csv = v5Summary.outputs?.llm1PilotCandidateManifestV2Csv || "";
      job.artifacts.groupPayloadSchemaV5Json = v5Summary.outputs?.groupPayloadSchemaV5Json || "";
      job.result = {
        ...job.result,
        groupPrep: {
          ...(job.result?.groupPrep || {}),
          payloadV5: sanitizeGroupedInterpretationPrepResult(v5Summary),
          evidenceDigest: digestSummary,
          metadata: { ...(job.result?.groupPrep?.metadata || {}), ...(v5Summary.metadata || {}) },
        },
      };
      job.status = "complete";
      job.stage = "grouping_preparation";
      job.stageProgress = 100;
      job.message = "Evidence digest and bounded v5 payload regeneration completed";
    } catch (error) {
      job.status = "complete";
      job.stageProgress = 100;
      job.message = "Evidence digest failed; deterministic evidence and v5 payloads remain valid";
      job.result = { ...job.result, evidenceDigest: { status: "failed", error: error.message || String(error) } };
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();
  res.status(202).json({ jobId: job.id, status: "running", requestedGroups: requestedGroupIds.length });
});

app.post("/api/vcf-canon-matches/:jobId/retry-enrichment", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  const isGeneModuleV2 = job?.result?.schemaVersion === "gene_module_v2";
  if (downstreamBlockedForJob(job) && !isGeneModuleV2) {
    res.status(409).json({ error: downstreamBlockedMessage(job) });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  if (isGeneModuleV2 ? !job.artifacts?.aiTriageCsv : !job.artifacts?.deliverableAuditCsv) {
    res.status(409).json({
      error: isGeneModuleV2
        ? "AI triage CSV is required before retrying coordinate enrichment."
        : "Match preparation audit CSV is required before retrying enrichment.",
    });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const enrichmentInputRoot = isGeneModuleV2 ? aiTriagePaths().root : matchPreparationPaths().root;
  const enrichmentInputPath = path.resolve(
    isGeneModuleV2 ? job.artifacts.aiTriageCsv || "" : job.artifacts.deliverableAuditCsv || "",
  );
  if (!isPathInside(enrichmentInputRoot, enrichmentInputPath)) {
    res.status(400).json({ error: "Variant enrichment input is outside its allowed root." });
    return;
  }
  const inputStat = await stat(enrichmentInputPath).catch(() => null);
  if (!inputStat || inputStat.size <= 0) {
    res.status(404).json({ error: "Variant enrichment input CSV was not found." });
    return;
  }

  job.status = "running";
  job.progress = Math.max(job.progress || 0, 86);
  job.stage = "enriching";
  job.stageProgress = 8;
  job.error = null;
  job.message = "Retrying variant enrichment";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);

  (async () => {
    try {
      const enrichmentPaths = variantEnrichmentPaths();
      await mkdir(enrichmentPaths.runs, { recursive: true });
      await mkdir(enrichmentPaths.cache, { recursive: true });
      const enrichmentRunId = `variant-enrichment-retry-${crypto.randomUUID()}`;
      const enrichmentOutputDir = isGeneModuleV2
        ? jobStageDirectory(job.id, "enrichment-retry")
        : path.join(enrichmentPaths.runs, enrichmentRunId);
      await mkdir(enrichmentOutputDir, { recursive: true });
      if (isGeneModuleV2) {
        Object.assign(job.artifacts, {
          v2EnrichmentVepBaseCsv: path.join(enrichmentOutputDir, "v2_enrichment_vep_base.csv"),
          v2EnrichmentResolutionAuditJsonl: path.join(enrichmentOutputDir, "v2_enrichment_resolution_audit.jsonl"),
          v2EnrichmentCompleteCsv: path.join(enrichmentOutputDir, "v2_enrichment_complete.csv"),
          v2EnrichmentVepOnlyAuditCsv: path.join(enrichmentOutputDir, "v2_enrichment_vep_only_audit.csv"),
          v2EnrichmentPhysicalMatrixCsv: path.join(enrichmentOutputDir, "v2_enrichment_physical_matrix.csv"),
          v2EnrichmentPhysicalEvidenceAuditJsonlGz: path.join(enrichmentOutputDir, "v2_enrichment_physical_evidence_audit.jsonl.gz"),
          v2EnrichmentModuleProjectionCsv: path.join(enrichmentOutputDir, "v2_enrichment_module_projection.csv"),
          enrichmentRetryQueueJsonl: path.join(enrichmentOutputDir, "enrichment_retry_queue.jsonl"),
          enrichmentIdentityResolutionSummaryJson: path.join(enrichmentOutputDir, "enrichment_identity_resolution_summary.json"),
          enrichmentPerformanceSummaryJson: path.join(enrichmentOutputDir, "enrichment_performance_summary.json"),
        });
        await persistVcfCanonJob(job);
      }
      const enrichmentPayload = {
        event: "heal.variant_enrichment.retry_requested",
        runId: enrichmentRunId,
        matchRunId: job.id,
        uploadId: job.uploadId,
        fileName: job.fileName,
        schemaVersion: isGeneModuleV2 ? "gene_module_v2" : undefined,
        analysisMode: isGeneModuleV2 ? normalizeAnalysisMode(job.analysisMode || "quick") : undefined,
        assembly: isGeneModuleV2 ? job.vcfAssembly || "GRCh38" : undefined,
        inputPath: enrichmentInputPath,
        outputDir: enrichmentOutputDir,
        cacheDir: enrichmentPaths.cache,
        cachePath: enrichmentPaths.cacheV2,
        legacyCachePath: enrichmentPaths.legacyCache,
        normalizationSummaryPath: isGeneModuleV2 ? job.artifacts.normalizationSummaryJson || "" : undefined,
        requestedAt: new Date().toISOString(),
      };
      const enrichmentSummary = await processVariantEnrichmentWithRetry(enrichmentPayload, job, 3);
      const enrichmentOutputs = variantEnrichmentOutputs(enrichmentSummary);
      job.artifacts.observedVariantEnrichmentCsv = enrichmentOutputs.observedVariantEnrichmentCsv;
      job.artifacts.observedVariantInterpretiveCsv = enrichmentOutputs.observedVariantInterpretiveCsv;
      job.artifacts.observedVariantEnrichmentPlusCsv = enrichmentOutputs.observedVariantEnrichmentPlusCsv;
      job.artifacts.v2EnrichmentVariantMasterCsv = enrichmentOutputs.v2EnrichmentVariantMasterCsv;
      job.artifacts.v2EnrichmentEvidenceAuditJsonl = enrichmentOutputs.v2EnrichmentEvidenceAuditJsonl;
      job.artifacts.enrichmentQualitySummaryJson = enrichmentOutputs.enrichmentQualitySummaryJson;
      job.artifacts.v2EnrichmentVepBaseCsv = enrichmentOutputs.v2EnrichmentVepBaseCsv || job.artifacts.v2EnrichmentVepBaseCsv || "";
      job.artifacts.v2EnrichmentResolutionAuditJsonl = enrichmentOutputs.v2EnrichmentResolutionAuditJsonl || job.artifacts.v2EnrichmentResolutionAuditJsonl || "";
      job.artifacts.v2EnrichmentCompleteCsv = enrichmentOutputs.v2EnrichmentCompleteCsv || job.artifacts.v2EnrichmentCompleteCsv || "";
      job.artifacts.v2EnrichmentVepOnlyAuditCsv = enrichmentOutputs.v2EnrichmentVepOnlyAuditCsv || job.artifacts.v2EnrichmentVepOnlyAuditCsv || "";
      job.artifacts.v2EnrichmentPhysicalMatrixCsv = enrichmentOutputs.v2EnrichmentPhysicalMatrixCsv || job.artifacts.v2EnrichmentPhysicalMatrixCsv || "";
      job.artifacts.v2EnrichmentPhysicalEvidenceAuditJsonlGz = enrichmentOutputs.v2EnrichmentPhysicalEvidenceAuditJsonlGz || job.artifacts.v2EnrichmentPhysicalEvidenceAuditJsonlGz || "";
      job.artifacts.v2EnrichmentModuleProjectionCsv = enrichmentOutputs.v2EnrichmentModuleProjectionCsv || job.artifacts.v2EnrichmentModuleProjectionCsv || "";
      job.artifacts.enrichmentRetryQueueJsonl = enrichmentOutputs.enrichmentRetryQueueJsonl || job.artifacts.enrichmentRetryQueueJsonl || "";
      job.artifacts.enrichmentIdentityResolutionSummaryJson = enrichmentOutputs.enrichmentIdentityResolutionSummaryJson || job.artifacts.enrichmentIdentityResolutionSummaryJson || "";
      job.artifacts.enrichmentPerformanceSummaryJson = enrichmentOutputs.enrichmentPerformanceSummaryJson || job.artifacts.enrichmentPerformanceSummaryJson || "";
      job.result = {
        ...(job.result || {}),
        variantEnrichment: sanitizeVariantEnrichmentResult(enrichmentSummary),
      };
      let refinementSummary = null;
      if (isGeneModuleV2) {
        if (!job.artifacts.canonCleanPath) {
          throw new Error("The original canon clean artifact is required to retry v2 evidence refinement safely.");
        }
        refinementSummary = await runEvidenceRefinementForJob({
          job,
          runId: job.id,
          analysisMode: job.analysisMode || "quick",
          assembly: job.vcfAssembly || "GRCh38",
          physicalMatrixPath: job.artifacts.v2EnrichmentPhysicalMatrixCsv,
          matchPath: job.artifacts.sheetFinalConsolidatedCsv,
          triagePath: job.artifacts.aiTriageCsv,
          triageExcludedPath: job.artifacts.aiTriageExcludedAuditCsv,
          canonCleanPath: job.artifacts.canonCleanPath,
          normalizedVariantsPath: job.artifacts.normalizedVariantsCsv,
        });
      }
      job.status = "complete";
      job.progress = 100;
      job.stage = isGeneModuleV2 ? "evidence_refinement_quality_gate" : "enriching";
      job.stageProgress = 100;
      const qualityGate = enrichmentSummary.metadata?.qualityGate || {};
      const technicalPassed = qualityGate.technicalGate
        ? qualityGate.technicalGate.status === "pass"
        : qualityGate.status === "pass";
      const evidenceReady = qualityGate.evidenceReadinessGate
        ? qualityGate.evidenceReadinessGate.status === "pass"
        : qualityGate.evidenceReady !== false;
      job.message = isGeneModuleV2
        ? refinementSummary?.gates?.conservationGate?.status === "pass" &&
          refinementSummary?.gates?.attributionGate?.status === "pass"
          ? "Curated evidence contracts completed; grouped LLM1 remains blocked pending professional approval"
          : "Evidence refinement requires contract review"
        : "Variant enrichment completed";
      if (isGeneModuleV2) {
        job.result.metadata = {
          ...(job.result.metadata || {}),
          downstream_supported: false,
          downstream_input: "v2_curated_physical_variant_matrix",
          enrichment_quality_gate: qualityGate,
          evidence_refinement_gate: refinementSummary?.gates || {},
          technical_gate: qualityGate.technicalGate || { status: technicalPassed ? "pass" : "fail" },
          evidence_readiness_gate: qualityGate.evidenceReadinessGate || { status: evidenceReady ? "pass" : "fail" },
          downstream_message:
            "All variants remain available in curated contracts. V2 grouped LLM1 is paused until professional approval and explicit enablement.",
        };
      }
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      if (!isGeneModuleV2) job.stage = "enriching";
      job.stageProgress = 100;
      job.error = error.message || String(error);
      job.message = job.stage === "evidence_refinement" ? "Evidence refinement failed" : "Variant enrichment failed";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();

  res.status(202).json(publicJob(job));
});

app.post("/api/vcf-canon-matches/:jobId/individual-interpretation", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (downstreamBlockedForJob(job)) {
    res.status(409).json({ error: downstreamBlockedMessage(job) });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  if (!job.artifacts?.observedVariantEnrichmentPlusCsv) {
    res.status(409).json({ error: "Enrichment Plus CSV is required before individual interpretation." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const enrichmentPaths = variantEnrichmentPaths();
  const plusCsvPath = path.resolve(job.artifacts.observedVariantEnrichmentPlusCsv || "");
  if (!isPathInside(enrichmentPaths.root, plusCsvPath)) {
    res.status(400).json({ error: "Enrichment Plus CSV is outside the allowed root." });
    return;
  }
  const plusStat = await stat(plusCsvPath).catch(() => null);
  if (!plusStat || plusStat.size <= 0) {
    res.status(404).json({ error: "Enrichment Plus CSV was not found." });
    return;
  }

  job.status = "running";
  job.progress = Math.max(job.progress || 0, 96);
  job.stage = "individual_interpretation";
  job.stageProgress = 5;
  job.error = null;
  job.message = "Starting individual variant interpretation";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);

  (async () => {
    try {
      const interpretationPaths = individualInterpretationPaths();
      await mkdir(interpretationPaths.runs, { recursive: true });
      const interpretationRunId = `individual-interpretation-${crypto.randomUUID()}`;
      const interpretationOutputDir = path.join(interpretationPaths.runs, interpretationRunId);
      await mkdir(interpretationOutputDir, { recursive: true });
      const requestDryRun = Boolean(req.body?.dryRun) && ALLOW_LLM_DRY_RUN;
      const interpretationPayload = {
        event: "heal.individual_variant_interpretation.requested",
        runId: interpretationRunId,
        matchJobId: job.id,
        uploadId: job.uploadId,
        fileName: job.fileName,
        inputPath: plusCsvPath,
        outputDir: interpretationOutputDir,
        model: LLM1_MODEL,
        dryRun: requestDryRun,
        requestedAt: new Date().toISOString(),
      };
      const interpretationSummary = await processIndividualInterpretationWithRetry(interpretationPayload, job, 2);
      job.artifacts.variantInterpretationPayloadsJsonl =
        interpretationSummary.outputs?.variantInterpretationPayloadsJsonl || "";
      job.artifacts.variantInterpretationPayloadsCsv =
        interpretationSummary.outputs?.variantInterpretationPayloadsCsv || "";
      job.artifacts.individualVariantInterpretationProgressJson =
        interpretationSummary.outputs?.individualVariantInterpretationProgressJson || "";
      job.artifacts.individualVariantInterpretationsJsonl =
        interpretationSummary.outputs?.individualVariantInterpretationsJsonl || "";
      job.artifacts.individualVariantInterpretationsCsv =
        interpretationSummary.outputs?.individualVariantInterpretationsCsv || "";
      job.artifacts.individualVariantInterpretationErrorsCsv =
        interpretationSummary.outputs?.individualVariantInterpretationErrorsCsv || "";
      job.artifacts.individualVariantInterpretationSummaryJson =
        interpretationSummary.outputs?.individualVariantInterpretationSummaryJson || "";
      job.result = {
        ...(job.result || {}),
        individualInterpretation: sanitizeIndividualInterpretationResult(interpretationSummary),
      };
      job.status = interpretationSummary.status === "invalid" ? "failed" : "complete";
      job.progress = 100;
      job.stage = "individual_interpretation";
      job.stageProgress = 100;
      job.message =
        interpretationSummary.status === "warning"
          ? "Individual interpretation completed with row warnings"
          : "Individual interpretation completed";
      if (interpretationSummary.status === "invalid") {
        job.error = interpretationSummary.errors?.[0] || "Individual interpretation failed.";
      }
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.stage = "individual_interpretation";
      job.stageProgress = 100;
      job.error = error.message || String(error);
      job.message = "Individual interpretation failed";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();

  res.status(202).json(publicJob(job));
});

app.post("/api/vcf-canon-matches/:jobId/interpretation-normalization", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (downstreamBlockedForJob(job)) {
    res.status(409).json({ error: downstreamBlockedMessage(job) });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  if (!job.artifacts?.individualVariantInterpretationsCsv) {
    res.status(409).json({ error: "Individual interpretation CSV is required before normalization." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const interpretationPaths = individualInterpretationPaths();
  const inputCsvPath = path.resolve(job.artifacts.individualVariantInterpretationsCsv || "");
  if (!isPathInside(interpretationPaths.root, inputCsvPath)) {
    res.status(400).json({ error: "Individual interpretation CSV is outside the allowed root." });
    return;
  }
  const inputStat = await stat(inputCsvPath).catch(() => null);
  if (!inputStat || inputStat.size <= 0) {
    res.status(404).json({ error: "Individual interpretation CSV was not found." });
    return;
  }

  job.status = "running";
  job.progress = Math.max(job.progress || 0, 98);
  job.stage = "interpretation_normalization";
  job.stageProgress = 10;
  job.error = null;
  job.message = "Normalizing individual interpretation QA";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);

  (async () => {
    try {
      const normalizationPaths = interpretationNormalizationPaths();
      await mkdir(normalizationPaths.runs, { recursive: true });
      const normalizationRunId = `interpretation-normalization-${crypto.randomUUID()}`;
      const normalizationOutputDir = path.join(normalizationPaths.runs, normalizationRunId);
      await mkdir(normalizationOutputDir, { recursive: true });
      const normalizationPayload = {
        event: "heal.individual_variant_interpretation.normalization_requested",
        runId: normalizationRunId,
        matchJobId: job.id,
        uploadId: job.uploadId,
        fileName: job.fileName,
        inputPath: inputCsvPath,
        outputDir: normalizationOutputDir,
        requestedAt: new Date().toISOString(),
      };
      job.stageProgress = 40;
      job.message = "Applying deterministic interpretation QA rules";
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);

      const normalizationSummary = await processInterpretationNormalization(normalizationPayload);
      job.artifacts.individualVariantInterpretationsNormalizedCsv =
        normalizationSummary.outputs?.individualInterpretationsNormalizedCsv || "";
      job.artifacts.individualVariantInterpretationNormalizationWarningsCsv =
        normalizationSummary.outputs?.individualInterpretationNormalizationWarningsCsv || "";
      job.artifacts.individualVariantInterpretationNormalizationSummaryJson =
        normalizationSummary.outputs?.individualInterpretationNormalizationSummaryJson || "";
      job.result = {
        ...(job.result || {}),
        interpretationNormalization: sanitizeInterpretationNormalizationResult(normalizationSummary),
      };
      job.status = normalizationSummary.status === "invalid" ? "failed" : "complete";
      job.progress = 100;
      job.stage = "interpretation_normalization";
      job.stageProgress = 100;
      job.message = "Interpretation QA normalization completed";
      if (normalizationSummary.status === "invalid") {
        job.error = normalizationSummary.errors?.[0] || "Interpretation normalization failed.";
      }
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.stage = "interpretation_normalization";
      job.stageProgress = 100;
      job.error = error.message || String(error);
      job.message = "Interpretation QA normalization failed";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();

  res.status(202).json(publicJob(job));
});

app.post("/api/vcf-canon-matches/:jobId/global-interpretation", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (downstreamBlockedForJob(job)) {
    res.status(409).json({ error: downstreamBlockedMessage(job) });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  if (!job.artifacts?.individualVariantInterpretationsNormalizedCsv) {
    res.status(409).json({ error: "Normalized individual interpretation CSV is required before global interpretation." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const normalizationPaths = interpretationNormalizationPaths();
  const inputCsvPath = path.resolve(job.artifacts.individualVariantInterpretationsNormalizedCsv || "");
  if (!isPathInside(normalizationPaths.root, inputCsvPath)) {
    res.status(400).json({ error: "Normalized individual interpretation CSV is outside the allowed root." });
    return;
  }
  const inputStat = await stat(inputCsvPath).catch(() => null);
  if (!inputStat || inputStat.size <= 0) {
    res.status(404).json({ error: "Normalized individual interpretation CSV was not found." });
    return;
  }

  const analysisMode = normalizeAnalysisMode(req.body?.analysisMode || job.analysisMode);
  const languageMode = normalizeLanguageMode(req.body?.languageMode);
  const audienceMode = normalizeAudienceMode(req.body?.audienceMode);
  const model = resolveLlm2Model({ analysisMode, requestedModel: req.body?.model });

  job.status = "running";
  job.progress = 100;
  job.stage = "global_interpretation";
  job.stageProgress = 12;
  job.error = null;
  job.message = `Starting global interpretation with ${model}`;
  job.analysisMode = analysisMode;
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);

  (async () => {
    try {
      const globalPaths = globalInterpretationPaths();
      await mkdir(globalPaths.runs, { recursive: true });
      const globalRunId = `global-interpretation-${crypto.randomUUID()}`;
      const globalOutputDir = path.join(globalPaths.runs, globalRunId);
      await mkdir(globalOutputDir, { recursive: true });
      const globalPayload = {
        event: "heal.global_interpretation.requested",
        runId: globalRunId,
        matchJobId: job.id,
        uploadId: job.uploadId,
        fileName: job.fileName,
        inputPath: inputCsvPath,
        outputDir: globalOutputDir,
        model,
        analysisMode,
        languageMode,
        audienceMode,
        dryRun: Boolean(req.body?.dryRun) && ALLOW_LLM_DRY_RUN,
        requestedAt: new Date().toISOString(),
      };
      job.stageProgress = 35;
      job.message = "Building deterministic global interpretation payload";
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);

      const globalSummary = await processGlobalInterpretation(globalPayload);
      job.artifacts.globalInterpretationPayloadJson = globalSummary.outputs?.globalInterpretationPayloadJson || "";
      job.artifacts.globalInterpretationDeterministicSummaryJson = globalSummary.outputs?.deterministicSummaryJson || "";
      job.artifacts.globalInterpretationJson = globalSummary.outputs?.globalInterpretationJson || "";
      job.artifacts.globalInterpretationEsSourceJson = globalSummary.outputs?.globalInterpretationEsSourceJson || "";
      job.artifacts.globalInterpretationSectionsCsv = globalSummary.outputs?.globalInterpretationSectionsCsv || "";
      job.artifacts.globalInterpretationSummaryJson = globalSummary.outputs?.globalInterpretationSummaryJson || "";
      job.result = {
        ...(job.result || {}),
        globalInterpretation: sanitizeGlobalInterpretationResult(globalSummary),
      };
      job.status = globalSummary.status === "invalid" ? "failed" : "complete";
      job.progress = 100;
      job.stage = "global_interpretation";
      job.stageProgress = 100;
      job.message = "Global interpretation completed";
      if (globalSummary.status === "invalid") {
        job.error = globalSummary.errors?.[0] || "Global interpretation failed.";
      }
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.stage = "global_interpretation";
      job.stageProgress = 100;
      job.error = error.message || String(error);
      job.message = "Global interpretation failed";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();

  res.status(202).json(publicJob(job));
});

app.post("/api/vcf-canon-matches/:jobId/final-report", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (downstreamBlockedForJob(job)) {
    res.status(409).json({ error: downstreamBlockedMessage(job) });
    return;
  }
  if (job.status === "running") {
    res.status(409).json({ error: "This job is already running." });
    return;
  }
  if (!job.artifacts?.globalInterpretationJson) {
    res.status(409).json({ error: "Global interpretation JSON is required before final report rendering." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const globalPaths = globalInterpretationPaths();
  const inputJsonPath = path.resolve(job.artifacts.globalInterpretationJson || "");
  if (!isPathInside(globalPaths.root, inputJsonPath)) {
    res.status(400).json({ error: "Global interpretation JSON is outside the allowed root." });
    return;
  }
  const inputStat = await stat(inputJsonPath).catch(() => null);
  if (!inputStat || inputStat.size <= 0) {
    res.status(404).json({ error: "Global interpretation JSON was not found." });
    return;
  }

  const languageMode = normalizeLanguageMode(req.body?.languageMode || job.result?.globalInterpretation?.metadata?.language_mode);
  const audienceMode = normalizeAudienceMode(req.body?.audienceMode || job.result?.globalInterpretation?.metadata?.audience_mode);

  job.status = "running";
  job.progress = 100;
  job.stage = "final_report";
  job.stageProgress = 15;
  job.error = null;
  job.message = "Rendering final user report";
  job.updatedAt = new Date().toISOString();
  await persistVcfCanonJob(job);

  (async () => {
    try {
      const reportPaths = finalReportPaths();
      await mkdir(reportPaths.runs, { recursive: true });
      const reportRunId = `final-report-${crypto.randomUUID()}`;
      const reportOutputDir = path.join(reportPaths.runs, reportRunId);
      await mkdir(reportOutputDir, { recursive: true });
      const reportPayload = {
        event: "heal.final_report.requested",
        runId: reportRunId,
        matchJobId: job.id,
        uploadId: job.uploadId,
        fileName: job.fileName,
        inputPath: inputJsonPath,
        outputDir: reportOutputDir,
        languageMode,
        audienceMode,
        requestedAt: new Date().toISOString(),
      };

      job.stageProgress = 55;
      job.message = "Formatting final DOCX report";
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);

      const reportSummary = await processFinalReport(reportPayload);
      job.artifacts.finalReportDocx = reportSummary.outputs?.finalReportDocx || "";
      job.artifacts.finalReportSummaryJson = reportSummary.outputs?.finalReportSummaryJson || "";
      job.result = {
        ...(job.result || {}),
        finalReport: sanitizeFinalReportResult(reportSummary),
      };
      job.status = reportSummary.status === "invalid" ? "failed" : "complete";
      job.progress = 100;
      job.stage = "final_report";
      job.stageProgress = 100;
      job.message = "Final report completed";
      if (reportSummary.status === "invalid") {
        job.error = reportSummary.errors?.[0] || "Final report rendering failed.";
      }
    } catch (error) {
      job.status = "failed";
      job.progress = 100;
      job.stage = "final_report";
      job.stageProgress = 100;
      job.error = error.message || String(error);
      job.message = "Final report rendering failed";
    } finally {
      job.updatedAt = new Date().toISOString();
      await persistVcfCanonJob(job);
    }
  })();

  res.status(202).json(publicJob(job));
});

app.get("/api/vcf-canon-matches/:jobId/download", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.sheetFinalConsolidatedCsv) {
    res.status(409).json({ error: "VCF-canon match CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = vcfCanonMatchPaths();
  const matchCsvPath = path.resolve(job.artifacts?.sheetFinalConsolidatedCsv || "");
  if (!isPathInside(paths.root, matchCsvPath)) {
    res.status(400).json({ error: "Match CSV is outside the allowed root." });
    return;
  }
  const matchCsvStat = await stat(matchCsvPath).catch(() => null);
  if (!matchCsvStat || matchCsvStat.size <= 0) {
    res.status(404).json({ error: "Match CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(matchCsvPath, `${baseName}_vcf_canon_matches.csv`);
});

app.get("/api/vcf-canon-matches/:jobId/debug/:artifact", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const artifactMap = {
    vcf_candidates: "vcfCandidatesCsv",
    vcf_joined_chr_pos: "vcfJoinedChrPosCsv",
    match_strict: "sheetFinalMatchStrictCsv",
    alt_review: "sheetFinalMatchLikelyNeedsAltReviewCsv",
    position_review: "sheetFinalMatchByPositionNeedsReviewCsv",
    no_vcf_match: "sheetFinalNoVcfMatchByChrPosCsv",
  };
  const artifactKey = artifactMap[req.params.artifact];
  if (!artifactKey) {
    res.status(404).json({ error: "Unknown VCF-canon debug artifact." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.[artifactKey]) {
    res.status(409).json({ error: "VCF-canon debug CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = vcfCanonMatchPaths();
  const csvPath = path.resolve(job.artifacts?.[artifactKey] || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "VCF-canon debug CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "VCF-canon debug CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_${req.params.artifact}.csv`);
});

async function downloadMatchPreparationArtifact(req, res, artifactKey, suffix) {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.[artifactKey]) {
    res.status(409).json({ error: "Match preparation CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = matchPreparationPaths();
  const csvPath = path.resolve(job.artifacts?.[artifactKey] || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "Prepared match CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "Prepared match CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_${suffix}.csv`);
}

async function downloadAiTriageArtifact(req, res, artifactKey, suffix, { json = false } = {}) {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.[artifactKey]) {
    res.status(409).json({ error: "AI triage artifact is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = aiTriagePaths();
  const artifactPath = path.resolve(job.artifacts?.[artifactKey] || "");
  if (!isPathInside(paths.root, artifactPath)) {
    res.status(400).json({ error: "AI triage artifact is outside the allowed root." });
    return;
  }
  const artifactStat = await stat(artifactPath).catch(() => null);
  if (!artifactStat || artifactStat.size <= 0) {
    res.status(404).json({ error: "AI triage artifact was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  if (json) {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.download(artifactPath, `${baseName}_${suffix}.json`);
    return;
  }
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(artifactPath, `${baseName}_${suffix}.csv`);
}

async function downloadGroupedArtifact(req, res, artifactKey, suffix, { json = false, jsonl = false, text = false } = {}) {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.[artifactKey]) {
    res.status(409).json({ error: "Grouped interpretation artifact is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const roots = [groupedInterpretationPrepPaths().root, groupedIndividualInterpretationPaths().root];
  const artifactPath = path.resolve(job.artifacts?.[artifactKey] || "");
  if (!roots.some((root) => isPathInside(root, artifactPath))) {
    res.status(400).json({ error: "Grouped interpretation artifact is outside the allowed roots." });
    return;
  }
  const artifactStat = await stat(artifactPath).catch(() => null);
  if (!artifactStat || (artifactStat.size <= 0 && artifactKey !== "groupEvidenceDigestsJsonl")) {
    res.status(404).json({ error: "Grouped interpretation artifact was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  const gzipEncoded = artifactPath.toLowerCase().endsWith(".gz");
  if (gzipEncoded) {
    res.setHeader("Content-Type", "application/gzip");
    res.download(artifactPath, `${baseName}_${suffix}.${jsonl ? "jsonl" : json ? "json" : "csv"}.gz`);
    return;
  }
  if (json) {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.download(artifactPath, `${baseName}_${suffix}.json`);
    return;
  }
  if (jsonl) {
    res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
    res.download(artifactPath, `${baseName}_${suffix}.jsonl`);
    return;
  }
  if (text) {
    res.setHeader("Content-Type", "text/markdown; charset=utf-8");
    res.download(artifactPath, `${baseName}_${suffix}.md`);
    return;
  }
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(artifactPath, `${baseName}_${suffix}.csv`);
}

async function downloadRuntimeArtifact(req, res, artifactKey, suffix, pathsForArtifact, { json = false, jsonl = false } = {}) {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.[artifactKey]) {
    res.status(409).json({ error: "Requested artifact is not ready yet." });
    return;
  }
  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }
  const paths = pathsForArtifact();
  const artifactPath = path.resolve(job.artifacts[artifactKey]);
  if (!isPathInside(paths.root, artifactPath)) {
    res.status(400).json({ error: "Artifact is outside the allowed runtime root." });
    return;
  }
  const artifactStat = await stat(artifactPath).catch(() => null);
  if (!artifactStat || artifactStat.size <= 0) {
    res.status(404).json({ error: "Artifact was not found." });
    return;
  }
  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  const extension = json ? "json" : jsonl ? "jsonl" : "csv";
  const gzipEncoded = artifactPath.toLowerCase().endsWith(".gz");
  res.setHeader(
    "Content-Type",
    gzipEncoded ? "application/gzip" : json ? "application/json; charset=utf-8" : "text/plain; charset=utf-8",
  );
  res.download(artifactPath, `${baseName}_${suffix}.${extension}${gzipEncoded ? ".gz" : ""}`);
}

app.get("/api/vcf-canon-matches/:jobId/preparation-audit", async (req, res) => {
  await downloadMatchPreparationArtifact(req, res, "deliverableAuditCsv", "match_preparation_audit");
});

app.get("/api/vcf-canon-matches/:jobId/preparation-minimal", async (req, res) => {
  await downloadMatchPreparationArtifact(req, res, "deliverableMinCsv", "match_preparation_minimal");
});

app.get("/api/vcf-canon-matches/:jobId/ai-triage", async (req, res) => {
  await downloadAiTriageArtifact(req, res, "aiTriageCsv", "ai_triage");
});

app.get("/api/vcf-canon-matches/:jobId/ai-triage-excluded", async (req, res) => {
  await downloadAiTriageArtifact(req, res, "aiTriageExcludedAuditCsv", "ai_triage_excluded_audit");
});

app.get("/api/vcf-canon-matches/:jobId/ai-triage-summary", async (req, res) => {
  await downloadAiTriageArtifact(req, res, "aiTriageSummaryJson", "ai_triage_summary", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/normalized-variants", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "normalizedVariantsCsv", "normalized_variants", vcfNormalizationPaths);
});

app.get("/api/vcf-canon-matches/:jobId/normalization-excluded-audit", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "normalizationExcludedAuditCsv",
    "normalization_excluded_audit",
    vcfNormalizationPaths,
  );
});

app.get("/api/vcf-canon-matches/:jobId/normalization-summary", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "normalizationSummaryJson",
    "normalization_summary",
    vcfNormalizationPaths,
    { json: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-variant-master", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "v2EnrichmentVariantMasterCsv", "v2_enrichment_variant_master", variantEnrichmentPaths);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-vep-base", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "v2EnrichmentVepBaseCsv", "v2_enrichment_vep_base", variantEnrichmentPaths);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-complete", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "v2EnrichmentCompleteCsv", "v2_enrichment_complete", variantEnrichmentPaths);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-vep-only", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "v2EnrichmentVepOnlyAuditCsv", "v2_enrichment_vep_only_audit", variantEnrichmentPaths);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-physical-matrix", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "v2EnrichmentPhysicalMatrixCsv", "v2_enrichment_physical_matrix", variantEnrichmentPaths);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-physical-evidence-audit", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "v2EnrichmentPhysicalEvidenceAuditJsonlGz",
    "v2_enrichment_physical_evidence_audit",
    variantEnrichmentPaths,
    { jsonl: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-module-projection", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "v2EnrichmentModuleProjectionCsv", "v2_enrichment_module_projection", variantEnrichmentPaths);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-retry-queue", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "enrichmentRetryQueueJsonl",
    "enrichment_retry_queue",
    variantEnrichmentPaths,
    { jsonl: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-identity-summary", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "enrichmentIdentityResolutionSummaryJson",
    "enrichment_identity_resolution_summary",
    variantEnrichmentPaths,
    { json: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-resolution-audit", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "v2EnrichmentResolutionAuditJsonl",
    "v2_enrichment_resolution_audit",
    variantEnrichmentPaths,
    { jsonl: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-performance", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "enrichmentPerformanceSummaryJson",
    "enrichment_performance_summary",
    variantEnrichmentPaths,
    { json: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-evidence-audit", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "v2EnrichmentEvidenceAuditJsonl",
    "v2_enrichment_evidence_audit",
    variantEnrichmentPaths,
    { jsonl: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-quality-summary", async (req, res) => {
  await downloadRuntimeArtifact(
    req,
    res,
    "enrichmentQualitySummaryJson",
    "enrichment_quality_summary",
    variantEnrichmentPaths,
    { json: true },
  );
});

app.get("/api/vcf-canon-matches/:jobId/curated-physical-matrix", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "curatedPhysicalMatrixCsv", "v2_curated_physical_variant_matrix", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/curated-physical-registry", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "curatedPhysicalRegistryCsv", "v2_curated_physical_variant_registry", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/curated-module-projection", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "curatedGeneModuleProjectionCsv", "v2_curated_gene_module_projection", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/canonical-gene-module-status", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "canonicalGeneModuleStatusCsv", "v2_canonical_gene_module_status", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/clinvar-aggregate", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "clinvarVariantAggregateCsv", "clinvar_variant_aggregate", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/clinvar-assertions", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "clinvarSubmitterAssertionsCsv", "clinvar_submitter_assertions", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/clinpgx-clinical-annotations", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "clinpgxClinicalAnnotationsCsv", "clinpgx_clinical_annotations", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/clinpgx-variant-annotations", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "clinpgxVariantAnnotationsCsv", "clinpgx_variant_annotations", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/gwas-associations", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "gwasVariantAssociationsCsv", "gwas_variant_associations", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/gwas-variant-traits", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "gwasVariantTraitSummaryCsv", "gwas_variant_trait_summary", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/gwas-gene-module-summary", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "gwasGeneModuleSummaryCsv", "gwas_gene_module_summary", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/gwas-evidence-clusters", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "gwasEvidenceClustersCsv", "gwas_evidence_clusters", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/gwas-relevance-template", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "gwasTraitModuleRelevanceTemplateCsv", "gwas_trait_module_relevance_template", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/gwas-metadata-retry-queue", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "gwasMetadataRetryQueueJsonl", "gwas_metadata_retry_queue", evidenceRefinementPaths, { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/publication-evidence", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "publicationEvidenceCsv", "publication_evidence", evidenceRefinementPaths);
});

app.get("/api/vcf-canon-matches/:jobId/evidence-refinement-raw", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "evidenceRefinementRawJsonlGz", "evidence_refinement_raw", evidenceRefinementPaths, { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/evidence-refinement-retry-queue", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "evidenceRefinementRetryQueueJsonl", "evidence_refinement_retry_queue", evidenceRefinementPaths, { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/evidence-refinement-summary", async (req, res) => {
  await downloadRuntimeArtifact(req, res, "evidenceRefinementSummaryJson", "evidence_refinement_summary", evidenceRefinementPaths, { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsCsv", "gene_module_group_payloads");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-jsonl", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsJsonl", "gene_module_group_payloads", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-variant-detail", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupVariantDetailCsv", "gene_module_group_variant_detail");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-summary", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupingSummaryJson", "gene_module_grouping_summary", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v4", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsCsvV4", "gene_module_group_payloads_v4");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v4-jsonl", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsJsonlV4", "gene_module_group_payloads_v4", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v5", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsCsvV5", "llm1_group_payloads_v5");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v5-jsonl", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsJsonlV5", "llm1_group_payloads_v5", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v6", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsCsvV6", "llm1_group_payloads_v6");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v6-jsonl", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsJsonlV6", "llm1_group_payloads_v6", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v7", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsCsvV7", "llm1_group_payloads_v7");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payloads-v7-jsonl", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadsJsonlV7", "llm1_group_payloads_v7", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/group-evidence-packets", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupEvidencePacketsJsonlGz", "group_evidence_packets", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/group-evidence-digests", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupEvidenceDigestsJsonl", "group_evidence_digests", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/group-evidence-digest-errors", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupEvidenceDigestErrorsCsv", "group_evidence_digest_errors");
});

app.get("/api/vcf-canon-matches/:jobId/group-token-budget-audit", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupTokenBudgetAuditCsv", "group_token_budget_audit");
});

app.get("/api/vcf-canon-matches/:jobId/group-evidence-coverage-audit", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupEvidenceCoverageAuditCsv", "group_evidence_coverage_audit");
});

app.get("/api/vcf-canon-matches/:jobId/group-compression-errors", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupCompressionErrorsCsv", "group_compression_errors");
});

app.get("/api/vcf-canon-matches/:jobId/group-compression-summary", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupCompressionSummaryJson", "group_compression_summary", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/llm1-pilot-candidate-manifest-v2", async (req, res) => {
  await downloadGroupedArtifact(req, res, "llm1PilotCandidateManifestV2Csv", "llm1_pilot_candidate_manifest_v2");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payload-v5-schema", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadSchemaV5Json", "llm1_group_payload_v5_schema", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payload-v6-schema", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadSchemaV6Json", "llm1_group_payload_v6_schema", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-payload-v7-schema", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadSchemaV7Json", "llm1_group_payload_v7_schema", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/group-preflight-v7", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPreflightV7Csv", "llm1_group_preflight_v7");
});

app.get("/api/vcf-canon-matches/:jobId/group-payload-v7-summary", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadV7SummaryJson", "llm1_group_payload_v7_summary", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/llm1-group-cards", async (req, res) => {
  await downloadGroupedArtifact(req, res, jobArtifactKeyForCards(req.params.jobId), "llm1_group_cards", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/llm1-group-quarantine", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupQuarantineJsonl", "llm1_group_quarantine", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/target-gene-consequence-audit", async (req, res) => {
  await downloadGroupedArtifact(req, res, "targetGeneConsequenceAuditCsv", "target_gene_consequence_audit");
});

app.get("/api/vcf-canon-matches/:jobId/allele-specific-frequency-audit", async (req, res) => {
  await downloadGroupedArtifact(req, res, "alleleSpecificFrequencyAuditCsv", "allele_specific_frequency_audit");
});

app.get("/api/vcf-canon-matches/:jobId/clinvar-condition-conflict-audit", async (req, res) => {
  await downloadGroupedArtifact(req, res, "clinvarConditionConflictAuditCsv", "clinvar_condition_conflict_audit");
});

app.get("/api/vcf-canon-matches/:jobId/group-token-budget-audit-v6", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupTokenBudgetAuditV6Csv", "group_token_budget_audit_v6");
});

app.get("/api/vcf-canon-matches/:jobId/group-payload-v6-summary", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadV6SummaryJson", "llm1_group_payload_v6_summary", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/group-payload-v6-errors", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupPayloadV6ErrorsCsv", "group_payload_v6_errors");
});

app.get("/api/vcf-canon-matches/:jobId/llm1-pilot-candidate-manifest-v3", async (req, res) => {
  await downloadGroupedArtifact(req, res, "llm1PilotCandidateManifestV3Csv", "llm1_pilot_candidate_manifest_v3");
});

app.get("/api/vcf-canon-matches/:jobId/persistent-mechanism-registry", async (req, res) => {
  await downloadGroupedArtifact(req, res, "persistentMechanismRegistryCsv", "mechanism_registry_v1");
});

app.get("/api/vcf-canon-matches/:jobId/persistent-gwas-registry", async (req, res) => {
  await downloadGroupedArtifact(req, res, "persistentGwasRegistryCsv", "gwas_module_relevance_registry_v1");
});

app.get("/api/vcf-canon-matches/:jobId/mechanism-registry", async (req, res) => {
  await downloadGroupedArtifact(req, res, "mechanismRegistryV1Csv", "mechanism_registry_v1");
});

app.get("/api/vcf-canon-matches/:jobId/llm1-pilot-manifest", async (req, res) => {
  await downloadGroupedArtifact(req, res, "llm1PilotManifestCsv", "llm1_pilot_manifest_v1");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-interpretations", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupInterpretationsCsv", "gene_module_group_interpretations");
});

app.get("/api/vcf-canon-matches/:jobId/grouped-interpretation-summary", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupInterpretationSummaryJson", "gene_module_group_interpretation_summary", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-interpretation-raw-responses", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupInterpretationRawResponsesJsonl", "gene_module_group_interpretation_raw_responses", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/llm1-pilot-approved-payloads", async (req, res) => {
  await downloadGroupedArtifact(req, res, "llm1PilotApprovedPayloadsJsonl", "llm1_pilot_approved_payloads_v6", { jsonl: true });
});

app.get("/api/vcf-canon-matches/:jobId/grouped-interpretation-call-audit", async (req, res) => {
  await downloadGroupedArtifact(req, res, "groupInterpretationCallAuditCsv", "gene_module_group_interpretation_call_audit");
});

app.get("/api/vcf-canon-matches/:jobId/llm1-pilot-prompt-snapshot", async (req, res) => {
  await downloadGroupedArtifact(req, res, "llm1PilotPromptSnapshotMd", "llm1_pilot_prompt_snapshot", { text: true });
});

app.get("/api/vcf-canon-matches/:jobId/llm1-pilot-response-schema-snapshot", async (req, res) => {
  await downloadGroupedArtifact(req, res, "llm1PilotResponseSchemaSnapshotJson", "llm1_pilot_response_schema_snapshot", { json: true });
});

app.get("/api/vcf-canon-matches/:jobId/enrichment", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.observedVariantEnrichmentCsv) {
    res.status(409).json({ error: "Variant enrichment CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = variantEnrichmentPaths();
  const csvPath = path.resolve(job.artifacts?.observedVariantEnrichmentCsv || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "Variant enrichment CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "Variant enrichment CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_observed_variant_enrichment.csv`);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-interpretive", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.observedVariantInterpretiveCsv) {
    res.status(409).json({ error: "Interpretive enrichment CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = variantEnrichmentPaths();
  const csvPath = path.resolve(job.artifacts?.observedVariantInterpretiveCsv || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "Interpretive enrichment CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "Interpretive enrichment CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_interpretive_enrichment.csv`);
});

app.get("/api/vcf-canon-matches/:jobId/enrichment-plus", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.observedVariantEnrichmentPlusCsv) {
    res.status(409).json({ error: "Enrichment Plus CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = variantEnrichmentPaths();
  const csvPath = path.resolve(job.artifacts?.observedVariantEnrichmentPlusCsv || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "Enrichment Plus CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "Enrichment Plus CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_interpretation_enrichment_plus.csv`);
});

app.get("/api/vcf-canon-matches/:jobId/individual-interpretations", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.individualVariantInterpretationsCsv) {
    res.status(409).json({ error: "Individual interpretation CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = individualInterpretationPaths();
  const csvPath = path.resolve(job.artifacts?.individualVariantInterpretationsCsv || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "Individual interpretation CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "Individual interpretation CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_individual_variant_interpretations.csv`);
});

app.get("/api/vcf-canon-matches/:jobId/individual-interpretations-normalized", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.individualVariantInterpretationsNormalizedCsv) {
    res.status(409).json({ error: "Normalized individual interpretation CSV is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = interpretationNormalizationPaths();
  const csvPath = path.resolve(job.artifacts?.individualVariantInterpretationsNormalizedCsv || "");
  if (!isPathInside(paths.root, csvPath)) {
    res.status(400).json({ error: "Normalized individual interpretation CSV is outside the allowed root." });
    return;
  }
  const csvStat = await stat(csvPath).catch(() => null);
  if (!csvStat || csvStat.size <= 0) {
    res.status(404).json({ error: "Normalized individual interpretation CSV was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.download(csvPath, `${baseName}_individual_variant_interpretations_normalized.csv`);
});

async function downloadGlobalInterpretationArtifact(req, res, artifactKey, suffix, contentType) {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.[artifactKey]) {
    res.status(409).json({ error: "Global interpretation artifact is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = globalInterpretationPaths();
  const artifactPath = path.resolve(job.artifacts?.[artifactKey] || "");
  if (!isPathInside(paths.root, artifactPath)) {
    res.status(400).json({ error: "Global interpretation artifact is outside the allowed root." });
    return;
  }
  const artifactStat = await stat(artifactPath).catch(() => null);
  if (!artifactStat || artifactStat.size <= 0) {
    res.status(404).json({ error: "Global interpretation artifact was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", contentType);
  res.download(artifactPath, `${baseName}_${suffix}`);
}

app.get("/api/vcf-canon-matches/:jobId/global-interpretation", async (req, res) => {
  await downloadGlobalInterpretationArtifact(
    req,
    res,
    "globalInterpretationJson",
    "global_interpretation.json",
    "application/json; charset=utf-8",
  );
});

app.get("/api/vcf-canon-matches/:jobId/global-interpretation-sections", async (req, res) => {
  await downloadGlobalInterpretationArtifact(
    req,
    res,
    "globalInterpretationSectionsCsv",
    "global_interpretation_sections.csv",
    "text/csv; charset=utf-8",
  );
});

app.get("/api/vcf-canon-matches/:jobId/global-interpretation-payload", async (req, res) => {
  await downloadGlobalInterpretationArtifact(
    req,
    res,
    "globalInterpretationPayloadJson",
    "global_interpretation_payload.json",
    "application/json; charset=utf-8",
  );
});

app.get("/api/vcf-canon-matches/:jobId/global-interpretation-deterministic-summary", async (req, res) => {
  await downloadGlobalInterpretationArtifact(
    req,
    res,
    "globalInterpretationDeterministicSummaryJson",
    "global_interpretation_deterministic_summary.json",
    "application/json; charset=utf-8",
  );
});

app.get("/api/vcf-canon-matches/:jobId/final-report", async (req, res) => {
  if (REQUIRE_ORIGIN && !req.headers.origin) {
    res.status(403).json({ error: "Origin header is required." });
    return;
  }

  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "VCF-canon match job not found." });
    return;
  }
  if (!job.artifacts?.finalReportDocx) {
    res.status(409).json({ error: "Final report is not ready yet." });
    return;
  }

  const upload = await loadUpload(job.uploadId).catch(() => null);
  if (upload && !canAccessUpload(req, upload)) {
    res.status(403).json({ error: "Match belongs to a different client." });
    return;
  }

  const paths = finalReportPaths();
  const reportPath = path.resolve(job.artifacts.finalReportDocx || "");
  if (!isPathInside(paths.root, reportPath)) {
    res.status(400).json({ error: "Final report artifact is outside the allowed root." });
    return;
  }
  const reportStat = await stat(reportPath).catch(() => null);
  if (!reportStat || reportStat.size <= 0) {
    res.status(404).json({ error: "Final report artifact was not found." });
    return;
  }

  const baseName = safeFileName(String(job.fileName || "heal-vcf").replace(/\.(vcf\.gz|vcf|gz)$/i, ""));
  res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  res.download(reportPath, `${baseName}_final_report.docx`);
});

function safeCurationSnapshotId(value) {
  const normalized = String(value || "").trim();
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{2,79}$/.test(normalized)) {
    throw new Error("snapshotId must contain 3-80 safe characters.");
  }
  return normalized;
}

function tier1CurationV2Paths(snapshotId) {
  const root = path.resolve(HEAL_TIER1_CURATION_V2_ROOT);
  const snapshot = path.join(root, "snapshots", safeCurationSnapshotId(snapshotId));
  return {
    root,
    snapshot,
    evidence: path.join(snapshot, "evidence"),
    evidenceManifest: path.join(snapshot, "evidence", "evidence_manifest.json"),
    gold: path.join(snapshot, "gold", "gold_manifest.json"),
    optimization: path.join(snapshot, "prompt-optimization"),
    fullCandidate: path.join(snapshot, "full-candidate"),
    reviews: path.join(snapshot, "manual-review", "review_manifest.json"),
    approval: path.join(snapshot, "manual-review", "snapshot_approval.json"),
  };
}

async function resolveTier1CanonArtifacts() {
  const current = JSON.parse(await readFile(path.join(CANON_ROOT, "current", "current.json"), "utf8"));
  const runId = HEAL_TIER1_CURATION_V2_CANON_RUN_ID || current.runId;
  const runRoot = path.resolve(CANON_ROOT, "runs", runId);
  if (!isPathInside(path.resolve(CANON_ROOT, "runs"), runRoot)) throw new Error("Canon run is outside the allowed root.");
  const artifacts = {
    runId,
    cleanRows: path.join(runRoot, "heal-canon-v2-clean-rows.csv"),
    geneMaster: path.join(runRoot, "heal-canon-v2-gene-master.csv"),
    mechanismRegistry: path.resolve(HEAL_MECHANISM_REGISTRY_PATH),
  };
  for (const artifact of Object.values(artifacts).filter((value) => value !== runId)) {
    if (!existsSync(artifact)) throw new Error(`Required Tier 1 curation input is missing: ${artifact}`);
  }
  return artifacts;
}

async function persistTier1CurationV2Job(job) {
  const jobsRoot = path.join(path.resolve(HEAL_TIER1_CURATION_V2_ROOT), "jobs");
  await mkdir(jobsRoot, { recursive: true });
  await writeFile(path.join(jobsRoot, `${job.id}.json`), JSON.stringify(job, null, 2), "utf8");
}

async function startTier1CurationV2Task(operation, args, snapshotId) {
  const activeDuplicate = [...tier1CurationV2Jobs.values()].find(
    (item) => item.snapshotId === snapshotId && item.operation === operation && item.status === "running",
  );
  if (activeDuplicate) throw new Error(`A ${operation} task is already running for this snapshot.`);
  const job = {
    id: crypto.randomUUID(), operation, snapshotId, status: "running", startedAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(), completedAt: null, exitCode: null, outputTail: [], error: "",
  };
  tier1CurationV2Jobs.set(job.id, job);
  await persistTier1CurationV2Job(job);
  const child = spawn(PYTHON_EXE, args, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  const appendOutput = (stream, chunk) => {
    const lines = chunk.toString("utf8").split(/\r?\n/).filter(Boolean);
    job.outputTail = [...job.outputTail, ...lines.map((line) => `${stream}:${line}`)].slice(-30);
    job.updatedAt = new Date().toISOString();
  };
  child.stdout.on("data", (chunk) => appendOutput("stdout", chunk));
  child.stderr.on("data", (chunk) => appendOutput("stderr", chunk));
  child.on("error", async (error) => {
    job.status = "technical_failure"; job.error = error.message; job.completedAt = new Date().toISOString(); job.updatedAt = job.completedAt;
    await persistTier1CurationV2Job(job).catch(() => {});
  });
  child.on("close", async (code) => {
    job.exitCode = code; job.status = code === 0 ? "completed" : "blocked";
    job.completedAt = new Date().toISOString(); job.updatedAt = job.completedAt;
    if (code !== 0 && !job.error) job.error = job.outputTail.filter((line) => line.startsWith("stderr:")).slice(-1)[0] || `Process exited with ${code}.`;
    await persistTier1CurationV2Job(job).catch(() => {});
  });
  return job;
}

async function readOptionalJson(filePath, fallback = null) {
  const raw = await readFile(filePath, "utf8").catch(() => null);
  return raw ? JSON.parse(raw) : fallback;
}

app.get("/api/tier1-curation-v2/status", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  const snapshotsRoot = path.join(path.resolve(HEAL_TIER1_CURATION_V2_ROOT), "snapshots");
  const entries = await readdir(snapshotsRoot, { withFileTypes: true }).catch(() => []);
  const snapshots = [];
  for (const entry of entries.filter((item) => item.isDirectory())) {
    const paths = tier1CurationV2Paths(entry.name);
    snapshots.push({
      snapshotId: entry.name,
      evidence: await readOptionalJson(paths.evidenceManifest),
      gold: await readOptionalJson(paths.gold),
      fullCandidate: await readOptionalJson(path.join(paths.fullCandidate, "candidate_summary.json")),
      approval: await readOptionalJson(paths.approval),
    });
  }
  res.json({
    enabled: HEAL_TIER1_CURATION_V2_ENABLED, model: HEAL_TIER1_CURATION_V2_MODEL,
    evidenceCutoff: HEAL_TIER1_CURATION_V2_CUTOFF, activeRegistryModified: false,
    snapshots, runningJobs: [...tier1CurationV2Jobs.values()],
  });
});

app.get("/api/tier1-curation-v2/tasks/:taskId", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  const memory = tier1CurationV2Jobs.get(req.params.taskId);
  const stored = memory || await readOptionalJson(path.join(path.resolve(HEAL_TIER1_CURATION_V2_ROOT), "jobs", `${req.params.taskId}.json`));
  if (!stored) return res.status(404).json({ error: "Tier 1 curation task not found." });
  res.json(stored);
});

app.post("/api/tier1-curation-v2/tasks", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  if (!HEAL_TIER1_CURATION_V2_ENABLED) return res.status(409).json({ error: "Tier 1 curation v2 is disabled by configuration." });
  if (HEAL_TIER1_CURATION_V2_MODEL !== "gpt-5.6-sol" || HEAL_TIER1_CURATION_V2_CUTOFF !== "2026-07-28") {
    return res.status(409).json({ error: "Tier 1 curation model or evidence cutoff differs from the frozen v2 contract." });
  }
  try {
    const operation = String(req.body?.operation || "");
    const snapshotId = safeCurationSnapshotId(req.body?.snapshotId);
    const paths = tier1CurationV2Paths(snapshotId);
    await mkdir(paths.snapshot, { recursive: true });
    const canon = await resolveTier1CanonArtifacts();
    let command;
    if (operation === "collect_evidence") {
      if (existsSync(paths.evidenceManifest)) throw new Error("Evidence manifest already exists; snapshots are immutable.");
      command = [SERVICE_SCRIPTS.tier1CurationV2Evidence, "--mechanism-registry", canon.mechanismRegistry, "--clean-canon-rows", canon.cleanRows, "--gene-master", canon.geneMaster, "--output-dir", paths.evidence, "--scope", req.body?.scope === "gold" ? "gold" : "all"];
    } else if (operation === "prepare_gold") {
      if (!existsSync(paths.evidenceManifest)) throw new Error("Evidence collection must complete before preparing gold.");
      command = [SERVICE_SCRIPTS.tier1CurationV2Runner, "prepare-gold", "--evidence-manifest", paths.evidenceManifest, "--output", paths.gold];
    } else if (operation === "calibrate") {
      const candidate = String(req.body?.candidate || "");
      const promptPath = req.body?.promptCandidate
        ? path.join(paths.optimization, safeFileName(req.body.promptCandidate))
        : path.join(APP_ROOT, "services", "heal-tier1-curation-v2", "prompt_mechanism_curator_v2.md");
      if (!existsSync(paths.gold)) throw new Error("An approved gold manifest is required.");
      if (!existsSync(promptPath) || (req.body?.promptCandidate && !isPathInside(paths.optimization, promptPath))) throw new Error("Prompt candidate is missing or outside the snapshot.");
      command = [SERVICE_SCRIPTS.tier1CurationV2Runner, "calibrate", "--evidence-manifest", paths.evidenceManifest, "--gold", paths.gold, "--output-root", paths.optimization, "--candidate", candidate, "--prompt", promptPath];
    } else if (operation === "optimize_prompt") {
      const sourceName = String(req.body?.currentPrompt || "prompt_mechanism_curator_v2.md");
      const currentPrompt = sourceName === "prompt_mechanism_curator_v2.md"
        ? path.join(APP_ROOT, "services", "heal-tier1-curation-v2", sourceName)
        : path.join(paths.optimization, safeFileName(sourceName));
      const outputName = safeFileName(String(req.body?.outputPrompt || ""));
      const outputPrompt = path.join(paths.optimization, outputName);
      if (!outputName.endsWith(".md") || !existsSync(currentPrompt) || !isPathInside(paths.optimization, outputPrompt)) throw new Error("Prompt optimizer paths are invalid.");
      command = [SERVICE_SCRIPTS.tier1CurationV2Runner, "optimize", "--output-root", paths.optimization, "--current-prompt", currentPrompt, "--output", outputPrompt];
    } else if (operation === "run_full") {
      const candidate = String(req.body?.candidate || "");
      const candidateRoot = path.join(paths.optimization, candidate);
      const promptPath = path.join(candidateRoot, "curator_prompt_snapshot.md");
      command = [SERVICE_SCRIPTS.tier1CurationV2Runner, "run-full", "--evidence-manifest", paths.evidenceManifest, "--acceptance-report", path.join(candidateRoot, "acceptance_report.json"), "--prompt", promptPath, "--output-dir", paths.fullCandidate];
    } else {
      throw new Error("Unsupported Tier 1 curation v2 operation.");
    }
    const job = await startTier1CurationV2Task(operation, [command[0], ...command.slice(1)], snapshotId);
    res.status(202).json(job);
  } catch (error) {
    res.status(400).json({ error: error.message || String(error) });
  }
});

app.put("/api/tier1-curation-v2/snapshots/:snapshotId/gold", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  try {
    const paths = tier1CurationV2Paths(req.params.snapshotId);
    if (!existsSync(paths.evidenceManifest)) throw new Error("Evidence manifest is required before gold approval.");
    if (existsSync(paths.gold) && (await readOptionalJson(paths.gold))?.approval_status === "approved") throw new Error("Approved gold manifests are immutable.");
    await mkdir(path.dirname(paths.gold), { recursive: true });
    const temporary = `${paths.gold}.${process.pid}.tmp`;
    const sealed = `${paths.gold}.${process.pid}.sealed`;
    await writeFile(temporary, JSON.stringify(req.body, null, 2), "utf8");
    const validation = await runPythonJsonCommand(SERVICE_SCRIPTS.tier1CurationV2Runner, ["validate-gold", "--evidence-manifest", paths.evidenceManifest, "--gold", temporary, "--seal-output", sealed]);
    await unlink(temporary).catch(() => {});
    await rename(sealed, paths.gold);
    res.json({ status: "accepted", validation });
  } catch (error) {
    res.status(400).json({ error: error.message || String(error) });
  }
});

app.get("/api/tier1-curation-v2/snapshots/:snapshotId/review", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  try {
    const paths = tier1CurationV2Paths(req.params.snapshotId);
    const summary = await readOptionalJson(path.join(paths.fullCandidate, "candidate_summary.json"));
    const flags = await readOptionalJson(path.join(paths.fullCandidate, "cross_group_audit_flags.json"), []);
    const registryPath = path.join(paths.fullCandidate, "mechanism_curation_v2_candidate.jsonl");
    const registryRaw = await readFile(registryPath, "utf8").catch(() => "");
    const candidates = registryRaw.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    const activeRows = existsSync(HEAL_MECHANISM_REGISTRY_PATH)
      ? parseCsvRecords(await readFile(HEAL_MECHANISM_REGISTRY_PATH, "utf8"))
      : [];
    const activeByGroup = new Map(activeRows.map((row) => [`${row.gene}:${row.module_id}`, row.curation_status || "draft"]));
    const records = [];
    const recordsRoot = path.join(paths.fullCandidate, "records");
    for (const entry of await readdir(recordsRoot, { withFileTypes: true }).catch(() => [])) {
      if (entry.isFile() && entry.name.endsWith(".json")) {
        const record = await readOptionalJson(path.join(recordsRoot, entry.name));
        if (record) records.push({
          group_id: record.group_id,
          curator: record.curator,
          critic: record.critic,
          arbiter: record.arbiter,
          arbitration_reasons: record.arbitration_reasons,
        });
      }
    }
    const promptVersions = [];
    for (const entry of await readdir(paths.optimization, { withFileTypes: true }).catch(() => [])) {
      if (entry.isDirectory() && entry.name.startsWith("candidate-")) {
        const candidateRoot = path.join(paths.optimization, entry.name);
        promptVersions.push({
          candidate: entry.name,
          manifest: await readOptionalJson(path.join(candidateRoot, "run_manifest.json")),
          acceptance: await readOptionalJson(path.join(candidateRoot, "acceptance_report.json")),
        });
      }
    }
    res.json({
      snapshotId: req.params.snapshotId, summary, flags, promptVersions, records,
      candidates: candidates.map((candidate) => ({ ...candidate, current_core_status: activeByGroup.get(candidate.group_id) || "absent" })),
      activeRegistryModified: false,
    });
  } catch (error) {
    res.status(400).json({ error: error.message || String(error) });
  }
});

app.put("/api/tier1-curation-v2/snapshots/:snapshotId/review", async (req, res) => {
  if (!requireCurationAccess(req, res)) return;
  try {
    const paths = tier1CurationV2Paths(req.params.snapshotId);
    if (!existsSync(path.join(paths.fullCandidate, "candidate_summary.json"))) throw new Error("Full candidate is not ready for review.");
    if (existsSync(paths.approval)) throw new Error("Snapshot approval already exists and is immutable.");
    await mkdir(path.dirname(paths.reviews), { recursive: true });
    await writeFile(paths.reviews, JSON.stringify(req.body?.reviewManifest || {}, null, 2), "utf8");
    const result = await runPythonJsonCommand(SERVICE_SCRIPTS.tier1CurationV2Runner, [
      "approve-snapshot", "--candidate-dir", paths.fullCandidate, "--review-manifest", paths.reviews,
      "--owner", String(req.body?.owner || ""), "--approval-reference", String(req.body?.approvalReference || ""),
      "--output", paths.approval,
    ]);
    res.json(result);
  } catch (error) {
    res.status(400).json({ error: error.message || String(error) });
  }
});

await loadPersistedVcfCanonJobs();

app.listen(PORT, "127.0.0.1", () => {
  console.log(`HEAL local API listening on http://127.0.0.1:${PORT}`);
  console.log(`Upload root: ${UPLOAD_ROOT}`);
  console.log(`Chunk size: ${CHUNK_SIZE_BYTES}`);
});
