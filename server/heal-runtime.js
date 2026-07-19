import path from "node:path";

function resolveEnvPath(name, fallback) {
  return path.resolve(process.env[name] || fallback);
}

// HEAL_HOME owns project code, configuration, data, logs, and operations.
// Shared system dependencies such as Docker, Python, Node, and cloudflared
// intentionally remain outside this tree.
export const APP_ROOT = resolveEnvPath("HEAL_APP_ROOT", process.cwd());
export const HEAL_HOME = resolveEnvPath("HEAL_HOME", APP_ROOT);
export const DATA_ROOT = resolveEnvPath("HEAL_DATA_ROOT", path.join(HEAL_HOME, "data"));
export const CONFIG_ROOT = resolveEnvPath("HEAL_CONFIG_ROOT", path.join(HEAL_HOME, "config"));
export const LOG_ROOT = resolveEnvPath("HEAL_LOG_ROOT", path.join(HEAL_HOME, "logs"));
export const BACKUP_ROOT = resolveEnvPath("HEAL_BACKUP_ROOT", path.join(HEAL_HOME, "backups"));
export const SERVICE_CODE_ROOT = resolveEnvPath("HEAL_SERVICE_CODE_ROOT", path.join(APP_ROOT, "services"));

export function serviceScript(serviceName, scriptName) {
  return path.join(SERVICE_CODE_ROOT, serviceName, scriptName);
}

export function dataPath(...segments) {
  return path.join(DATA_ROOT, ...segments);
}

export const RUNTIME_PATHS = {
  uploads: resolveEnvPath("HEAL_UPLOAD_ROOT", dataPath("uploads")),
  canon: resolveEnvPath("HEAL_CANON_ROOT", dataPath("canon")),
  legacyRsid: resolveEnvPath("HEAL_RSID_RESOLUTION_ROOT", dataPath("legacy-rsid")),
  // Every stage persists under data/runs/<job-id>/<stage>.
  // Stage aliases preserve explicit API validation without duplicate roots.
  runs: resolveEnvPath("HEAL_RUN_ROOT", dataPath("runs")),
  match: resolveEnvPath("HEAL_VCF_CANON_MATCH_ROOT", dataPath("runs")),
  normalization: resolveEnvPath("HEAL_VCF_NORMALIZATION_ROOT", dataPath("runs")),
  preparation: resolveEnvPath("HEAL_MATCH_PREPARATION_ROOT", dataPath("runs")),
  triage: resolveEnvPath("HEAL_AI_TRIAGE_ROOT", dataPath("runs")),
  enrichment: resolveEnvPath("HEAL_VARIANT_ENRICHMENT_ROOT", dataPath("runs")),
  groupedPrep: resolveEnvPath("HEAL_GROUPED_INTERPRETATION_PREP_ROOT", dataPath("runs")),
  groupedInterpretation: resolveEnvPath("HEAL_GROUPED_INDIVIDUAL_INTERPRETATION_ROOT", dataPath("runs")),
  individualInterpretation: resolveEnvPath("HEAL_INDIVIDUAL_INTERPRETATION_ROOT", dataPath("runs")),
  interpretationNormalization: resolveEnvPath("HEAL_INTERPRETATION_NORMALIZATION_ROOT", dataPath("runs")),
  globalInterpretation: resolveEnvPath("HEAL_GLOBAL_INTERPRETATION_ROOT", dataPath("runs")),
  finalReport: resolveEnvPath("HEAL_FINAL_REPORT_ROOT", dataPath("runs")),
  references: resolveEnvPath("HEAL_REFERENCE_DATA_ROOT", dataPath("references")),
  jobs: resolveEnvPath("HEAL_JOB_ROOT", dataPath("jobs")),
  enrichmentCache: resolveEnvPath("HEAL_ENRICHMENT_CACHE_ROOT", dataPath("enrichment-cache")),
};

export const SERVICE_SCRIPTS = {
  validator: resolveEnvPath("HEAL_VALIDATOR_SCRIPT", serviceScript("heal-vcf-integrity", "validate_vcf_integrity.py")),
  canonProcessor: resolveEnvPath("HEAL_CANON_PROCESSOR_SCRIPT", serviceScript("heal-canon-intake", "process_heal_canon.py")),
  rsidResolution: serviceScript("heal-rsid-resolution", "resolve_rsid_coordinates.py"),
  legacyMatcher: serviceScript("heal-vcf-canon-match", "match_vcf_to_rsid_ready.py"),
  geneModuleMatcher: serviceScript("heal-vcf-canon-match", "match_vcf_to_gene_module_ready.py"),
  vcfNormalization: serviceScript("heal-vcf-normalization", "normalize_vcf_for_v2.py"),
  matchPreparation: serviceScript("heal-match-preparation", "prepare_match_deliverable.py"),
  aiTriage: serviceScript("heal-ai-triage", "triage_for_ai.py"),
  legacyEnrichment: serviceScript("heal-variant-enrichment", "enrich_observed_variants.py"),
  geneModuleEnrichment: serviceScript("heal-variant-enrichment", "enrich_gene_module_v2.py"),
  groupedPrep: serviceScript("heal-grouped-interpretation-prep", "prepare_gene_module_group_payloads.py"),
  groupedInterpretation: serviceScript("heal-grouped-individual-interpretation", "interpret_gene_module_groups.py"),
  individualInterpretation: serviceScript("heal-individual-interpretation", "interpret_observed_variants.py"),
  interpretationNormalization: serviceScript("heal-interpretation-normalization", "normalize_individual_interpretations.py"),
  globalInterpretation: serviceScript("heal-global-interpretation", "interpret_global_profile.py"),
  finalReport: serviceScript("heal-final-report", "render_final_report.py"),
};
