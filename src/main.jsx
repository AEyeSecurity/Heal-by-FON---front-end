import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BarChart3,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  FileUp,
  Globe2,
  Loader2,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  UploadCloud,
  X,
  XCircle,
} from "lucide-react";
import forceLogo from "./assets/forceofnature-logo.svg";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8787";
const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY || "";
const JOB_ACCESS_TOKENS_KEY = "heal.jobAccessTokens.v1";
const POLL_RETRY_LIMIT = 8;
const POLL_RETRY_DELAY_MS = 1500;
const VALIDATION_POLL_DELAY_MS = 800;
const MATCH_POLL_DELAY_MS = 900;
const LONG_STAGE_POLL_DELAY_MS = 1800;
const BUSY_PHASES = [
  "uploading",
  "validating",
  "matching",
  "normalizing",
  "preparing",
  "triaging",
  "enriching",
  "enrichment_quality_gate",
  "grouping_preparation",
  "grouped_individual_interpretation",
  "individual_interpretation",
  "interpretation_normalization",
  "global_interpretation",
  "final_report",
];

function isBusyPhase(phase) {
  return BUSY_PHASES.includes(phase);
}

function isLongPollingStage(stage) {
  return [
    "grouped_individual_interpretation",
    "individual_interpretation",
    "interpretation_normalization",
    "global_interpretation",
    "final_report",
  ].includes(stage);
}

function readJobAccessTokens() {
  try {
    return JSON.parse(window.localStorage.getItem(JOB_ACCESS_TOKENS_KEY) || "{}");
  } catch {
    return {};
  }
}

function storeJobAccessToken(jobId, accessToken) {
  if (!jobId || !accessToken) return;
  const tokens = readJobAccessTokens();
  tokens[jobId] = accessToken;
  window.localStorage.setItem(JOB_ACCESS_TOKENS_KEY, JSON.stringify(tokens));
}

function getJobAccessToken(jobId) {
  if (!jobId) return "";
  return readJobAccessTokens()[jobId] || "";
}

function accessHeaders(accessToken) {
  return accessToken ? { "X-HEAL-Access-Token": accessToken } : {};
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const QA_LLM2_MODELS = ["gpt-5-mini", "gpt-5", "gpt-5.1", "gpt-5.2"];

function defaultAudienceMode(analysisMode) {
  return analysisMode === "quick" ? "family" : "all";
}

function defaultLanguageMode(language) {
  return language === "en" ? "en" : "es";
}

function reportLanguagesForMode(languageMode) {
  return languageMode === "both" ? ["es", "en"] : [languageMode === "en" ? "en" : "es"];
}

const COPY = {
  es: {
    languageLabel: "Idioma",
    langEs: "Espanol",
    langEn: "English",
    eyebrow: "HEAL by FON",
    title: "Genomic Interpretation Pipeline",
    lede: "Carga un VCF para validarlo, cruzarlo contra el canon HEAL y generar artefactos auditables de interpretacion.",
    pipelineLabel: "Pipeline",
    steps: ["Carga del VCF", "Validacion de integridad", "Match VCF-Canon", "Interpretacion", "Analisis posterior"],
    dropEmpty: "Arrastra tu VCF aca",
    dropHelp: "Tambien podes seleccionarlo desde tu equipo.",
    selectFile: "Seleccionar archivo",
    initialMessage: "Selecciona un archivo VCF para empezar.",
    fileReady: "Archivo listo para enviar.",
    modeLabel: "Tipo de analisis",
    quickMode: "Analisis superficial",
    quickModeDetail: "Valida estructura, headers y primeras variantes.",
    completeMode: "Analisis completo",
    completeModeDetail: "Agrega metricas streaming de todo el VCF.",
    qaMode: "Control de Calidad",
    qaModeDetail: "Activa parser seleccionable, controles por etapa y reportes debug.",
    parserLabel: "Motor VCF",
    parserStreaming: "Streaming estable",
    parserPysam: "pysam experimental",
    parserHelp: "pysam replica mejor el Colab cuando esta disponible; si falla, el backend vuelve a streaming.",
    vcfAssemblyLabel: "Assembly VCF",
    vcfAssemblyAuto: "Detectar automaticamente",
    vcfAssemblyHelp: "Para canon v2 se bloquea el match si el assembly del VCF y del canon no coincide.",
    playStage: "Ejecutar etapa",
    debugDownloads: "Descargas QA",
    qaVcfCandidates: "Candidatos VCF por posicion",
    qaVcfJoined: "VCF unido con canon",
    qaStrict: "QA matches estrictos",
    qaAltReview: "QA revision ALT",
    qaPositionReview: "QA revision por posicion",
    qaNoVcfMatch: "QA sin match VCF",
    qaRunUploadFirst: "Primero carga o reutiliza un VCF.",
    qaRunValidationFirst: "Primero valida el VCF.",
    variantLimit: "Variantes iniciales a revisar",
    uploadProgress: "Carga del archivo",
    validationProgress: "Validacion del VCF",
    matchProgress: "Match VCF-Canon",
    normalizationProgress: "Normalizacion VCF",
    preparationProgress: "Preparacion del match",
    aiTriageProgress: "Triage IA",
    enrichmentProgress: "Enriquecimiento externo",
    enrichmentQualityProgress: "QA de enrichment",
    groupingPreparationProgress: "Agrupacion gene+modulo",
    groupedInterpretationProgress: "Interpretacion individual agrupada",
    individualInterpretationProgress: "Interpretacion individual",
    interpretationNormalizationProgress: "Normalizacion QA",
    globalInterpretationProgress: "Interpretacion global",
    finalReportProgress: "Reporte final",
    submit: "Enviar y validar",
    securityCheck: "Verificacion de seguridad",
    securityCheckHelp: "Protege el backend antes de aceptar VCFs grandes.",
    securityRequired: "Completa la verificacion de seguridad antes de subir el archivo.",
    duplicateTitle: "VCF ya disponible",
    duplicateMessage: "Este VCF ya fue subido el {date}. Podes reutilizarlo para validar de nuevo sin cargarlo otra vez.",
    duplicateUseExisting: "Usar VCF existente",
    duplicateUploadAgain: "Subir de todos modos",
    duplicateCancel: "Cancelar",
    reusingUpload: "Usando el VCF ya subido e iniciando validacion...",
    uploading: "Subiendo el archivo en partes a un espacio aislado...",
    validationStarting: "Iniciando validacion por streaming...",
    validating: "Validando",
    connectionRetrying: "Reconectando con el backend...",
    matchStarting: "Iniciando match VCF-Canon...",
    matching: "Matcheando VCF contra canon...",
    preparing: "Preparando CSVs de auditoria...",
    normalizing: "Normalizando alelos VCF contra la referencia...",
    enriching: "Enriqueciendo variantes observadas...",
    enrichmentVepBaseProgress: "Enrichment base VEP",
    enrichmentCompleteProgress: "Enrichment completo",
    enrichmentVepOnlyProgress: "Resolucion VEP-only",
    enrichmentQuality: "Validando cobertura y calidad del enrichment...",
    groupingPreparing: "Preparando payloads agrupados por gen y modulo...",
    groupedInterpretationStarting: "Iniciando interpretacion individual agrupada...",
    groupedInterpreting: "Interpretando grupos gen-modulo...",
    groupedInterpretationComplete: "Interpretacion individual agrupada finalizada.",
    groupedInterpretationFailed: "No se pudo completar la interpretacion individual agrupada.",
    enrichmentFailed: "No se pudo completar el enriquecimiento externo.",
    aiTriageFailed: "No se pudo completar el triage IA.",
    retryEnrichment: "Reintentar enriquecimiento",
    individualInterpretationStarting: "Iniciando interpretacion individual...",
    individualInterpreting: "Interpretando variantes observadas una por una...",
    individualInterpretationComplete: "Interpretacion individual finalizada.",
    individualInterpretationFailed: "No se pudo completar la interpretacion individual.",
    interpretationNormalizationStarting: "Iniciando normalizacion QA...",
    interpretationNormalizing: "Aplicando reglas deterministicas post-LLM1...",
    interpretationNormalizationComplete: "Normalizacion QA finalizada.",
    interpretationNormalizationFailed: "No se pudo completar la normalizacion QA.",
    globalInterpretationStarting: "Iniciando interpretacion global...",
    globalInterpreting: "Sintetizando patrones globales...",
    globalInterpretationComplete: "Interpretacion global finalizada.",
    globalInterpretationFailed: "No se pudo completar la interpretacion global.",
    finalReportStarting: "Generando reporte final...",
    finalReportRendering: "Formateando reporte final Word...",
    finalReportComplete: "Reporte final generado.",
    finalReportFailed: "No se pudo generar el reporte final.",
    llm2OptionsTitle: "Opciones LLM2",
    llm2AudienceLabel: "Audiencia",
    llm2ModelLabel: "Modelo LLM2",
    llm2LanguageLabel: "Idioma LLM2",
    audienceTechnical: "Tecnico",
    audienceProfessional: "Profesional de salud",
    audienceFamily: "Familia",
    audienceAll: "Todas",
    matchFailed: "No se pudo completar el match VCF-Canon.",
    validationFailed: "La validacion fallo.",
    uploadFailed: "No se pudo completar la carga.",
    processFailed: "No se pudo completar el proceso.",
    complete: "Validacion finalizada.",
    matchComplete: "Match VCF-Canon finalizado.",
    preparationComplete: "Preparacion del match finalizada.",
    aiTriageComplete: "Triage IA finalizado.",
    enrichmentComplete: "Enriquecimiento externo finalizado.",
    enrichmentQualityComplete: "QA de enrichment finalizado.",
    resultValid: "VCF validado",
    resultWarning: "Validado con warnings",
    resultInvalid: "VCF invalido",
    format: "Formato",
    size: "Tamano",
    sample: "Sample",
    variantsChecked: "Variantes revisadas",
    totalRows: "Total filas VCF",
    rowsWithId: "Campo ID no vacio",
    rowsWithRsid: "rsID en campo ID",
    passRows: "Filas PASS",
    multiallelic: "Multialelicas",
    snv: "SNV",
    nonSnv: "No SNV",
    gtHet: "GT heterocigota",
    gtHomAlt: "GT hom alt",
    gtHomRef: "GT hom ref",
    gtMissing: "GT faltante/parcial",
    gtComplex: "GT no diploide/complex",
    malformed: "Filas malformadas",
    metricTime: "Tiempo metricas",
    scanComplete: "Scan completo por streaming",
    notCalculated: "No calculado",
    topChromosomes: "Top cromosomas/contigs",
    checksum: "SHA-256",
    matchTitle: "Match VCF-Canon",
    preparationTitle: "Preparacion del match",
    aiTriageTitle: "Triage deterministico IA",
    enrichmentTitle: "Enriquecimiento de variantes observadas",
    groupingPreparationTitle: "Preparacion de grupos gene+modulo",
    preparationRows: "Filas preparadas",
    preparationObserved: "Con genotipo observado",
    preparationHigh: "Confianza alta",
    preparationModerate: "Confianza moderada",
    preparationLow: "Confianza baja",
    aiTriageIncluded: "Filas elegibles para IA",
    aiTriageStrong: "Fuertes cod/splice",
    aiTriageUtr: "UTR fuertes",
    aiTriageBackgroundExcluded: "Excluidas background",
    aiTriageUtrExcluded: "Excluidas UTR debiles",
    aiTriageDraftExcluded: "Excluidas Draft optional",
    aiTriageNoncodingExcluded: "Excluidas no codificantes optional",
    enrichmentObserved: "Filas enriquecidas",
    enrichmentPhysicalVariants: "Variantes fisicas",
    enrichmentVepCoverage: "Cobertura VEP",
    enrichmentExactRsids: "rsIDs exactos resueltos",
    enrichmentVepOnlyVariants: "Variantes VEP-only",
    enrichmentResolutionAmbiguous: "Resoluciones ambiguas",
    enrichmentResolutionAlleleMismatch: "Alelo no coincidente",
    enrichmentQualityDecision: "Decision QA",
    enrichmentInputRows: "Filas fuente",
    enrichmentPlusRows: "Filas Enrichment Plus",
    enrichmentUniqueRsids: "rsIDs unicos",
    enrichmentSources: "Fuentes externas",
    enrichmentCacheHits: "Cache hits",
    enrichmentSourceErrors: "Errores de fuentes",
    groupingPreparationGroups: "Grupos gene+modulo",
    groupingPreparationVariants: "Variantes fuente",
    groupingPreparationAverageSize: "Tamano promedio",
    groupingPreparationLargeGroups: "Grupos >25 variantes",
    groupedInterpretationTitle: "Interpretacion individual agrupada",
    groupedInterpretationGroups: "Grupos interpretados",
    groupedInterpretationSourceGroups: "Grupos fuente",
    groupedInterpretationSourceVariants: "Variantes agrupadas",
    groupedInterpretationAverageSize: "Tamano promedio",
    groupedInterpretationConflictGroups: "Grupos con conflicto",
    groupedInterpretationReviewGroups: "Grupos con review",
    groupedInterpretationErrors: "Grupos con error",
    groupedInterpretationModel: "Modelo LLM1 agrupado",
    groupedInterpretationCountLabel: "interpretaciones",
    individualInterpretationTitle: "Interpretacion individual",
    individualInterpretationRows: "Filas interpretadas",
    individualInterpretationSourceRows: "Filas fuente LLM1",
    individualInterpretationErrors: "Filas con error",
    individualInterpretationModel: "Modelo LLM1",
    individualInterpretationWorkers: "Workers LLM1",
    individualInterpretationDryRun: "Dry run",
    interpretationNormalizationTitle: "Normalizacion QA",
    interpretationNormalizationRows: "Filas normalizadas",
    interpretationNormalizationChanged: "Filas ajustadas",
    interpretationNormalizationDuplicates: "Duplicados normalizados",
    interpretationNormalizationDuplicateGroups: "Grupos duplicados",
    interpretationNormalizationWarnings: "Warnings QA",
    interpretationConfidenceHigh: "Confianza High",
    interpretationConfidenceModerate: "Confianza Moderate",
    interpretationConfidenceLow: "Confianza Low",
    interpretationConfidenceConflicting: "Conflicting",
    globalInterpretationTitle: "Interpretacion global",
    globalInterpretationModel: "Modelo LLM2",
    globalInterpretationAudience: "Audiencia",
    globalInterpretationLanguage: "Idioma",
    globalInterpretationReadiness: "Readiness",
    globalInterpretationVariants: "Variantes sintetizadas",
    globalInterpretationGenes: "Genes unicos",
    globalInterpretationRepeatedRsids: "rsIDs repetidos",
    globalInterpretationReview: "Revision profesional",
    globalInterpretationAmbiguities: "Ambiguedades gen/locus",
    finalReportTitle: "Reporte final",
    finalReportFormat: "Formato",
    finalReportSource: "Fuente",
    finalReportSize: "Tamano DOCX",
    matchStatusStrict: "Matches estrictos",
    matchStatusAltReview: "Matches con revision ALT",
    matchStatusNoPosition: "Sin match por posicion",
    matchStatusNoRsid: "Sin rsID detectado",
    matchTargets: "Targets canon",
    matchCandidates: "Candidatos VCF",
    matchScannedRows: "Filas VCF escaneadas",
    changeCanon: "Cambiar canon",
    canonTitle: "Canon de interpretacion",
    canonCurrent: "Canon actual",
    canonNone: "Todavia no hay canon cargado.",
    canonUploadHelp: "Carga un canon nuevo en formato CSV o XLSX. Se procesara y quedara como version activa.",
    canonSelect: "Seleccionar canon",
    canonUpload: "Subir y limpiar canon",
    canonUploading: "Procesando canon...",
    canonProgress: "Carga y procesamiento del canon",
    canonStructureProgress: "Estructura y limpieza",
    canonCoreProcessingProgress: "Procesamiento central del canon",
    canonActivationProgress: "Artefactos y activacion",
    canonLoaded: "Canon cargado",
    canonRows: "Filas no vacias",
    canonUniqueRsids: "rsIDs unicos",
    canonRepeatedRsids: "rsIDs repetidos",
    canonManualReview: "Revision manual",
    canonAssembly: "Assembly",
    canonSchema: "Schema",
    canonWarnings: "Warnings",
    canonGenesResolved: "Genes resueltos",
    canonJobQueued: "Canon en cola...",
    canonJobRunning: "Procesando canon...",
    canonGeneMasterDownload: "Descargar gene master",
    canonPreview: "Vista previa limpia",
    canonDownload: "Descargar canon completo",
    rsidMasterDownload: "Descargar rsID master",
    matchDownload: "Descargar CSV de matches",
    matchPreparationAuditDownload: "Descargar CSV preparado",
    matchPreparationMinimalDownload: "Descargar CSV minimo",
    aiTriageDownload: "Descargar CSV triage IA",
    aiTriageExcludedDownload: "Descargar audit de excluidas",
    aiTriageSummaryDownload: "Descargar resumen triage IA",
    enrichmentDownload: "Descargar CSV interpretativo",
    enrichmentPlusDownload: "Descargar CSV Enrichment Plus",
    enrichmentQaDownload: "Descargar CSV tecnico QA",
    normalizedVariantsDownload: "Descargar variantes normalizadas",
    normalizationAuditDownload: "Descargar audit de normalizacion",
    enrichmentQualityDownload: "Descargar resumen QA enrichment",
    enrichmentVepBaseDownload: "Descargar base VEP",
    enrichmentCompleteDownload: "Descargar enrichment completo",
    enrichmentVepOnlyDownload: "Descargar auditoria VEP-only",
    enrichmentResolutionAuditDownload: "Descargar auditoria de resolucion",
    enrichmentPerformanceDownload: "Descargar metricas de rendimiento",
    enrichmentEvidenceAuditDownload: "Descargar evidencia enrichment",
    groupingPayloadsDownload: "Descargar payloads agrupados",
    groupingVariantDetailDownload: "Descargar detalle por variante",
    groupingSummaryDownload: "Descargar resumen de grupos",
    groupedInterpretationDownload: "Descargar interpretacion agrupada",
    groupedInterpretationSummaryDownload: "Descargar resumen interpretacion agrupada",
    individualInterpretationDownload: "Descargar CSV interpretacion individual",
    interpretationNormalizationDownload: "Descargar CSV normalizado",
    globalInterpretationDownload: "Descargar interpretacion global JSON",
    globalInterpretationSectionsDownload: "Descargar secciones globales CSV",
    globalInterpretationPayloadDownload: "Descargar payload LLM2",
    globalInterpretationSummaryDownload: "Descargar resumen deterministico",
    finalReportDownload: "Descargar reporte final Word",
    finalReportDownloadEs: "Descargar reporte Word ES",
    finalReportDownloadEn: "Descargar reporte Word EN",
    matchDownloadFailed: "No se pudo descargar el CSV de matches.",
    canonDownloadFailed: "No se pudo descargar el canon.",
    close: "Cerrar",
    errorPopupTitle: "Proceso interrumpido",
    errorPopupRetry: "Volve a intentarlo. Si el problema se repite, revisaremos los logs del servidor.",
    errorPopupClose: "Entendido",
  },
  en: {
    languageLabel: "Language",
    langEs: "Espanol",
    langEn: "English",
    eyebrow: "HEAL by FON",
    title: "Genomic Interpretation Pipeline",
    lede: "Upload a VCF to validate it, match it against the HEAL canon, and generate auditable interpretation artifacts.",
    pipelineLabel: "Pipeline",
    steps: ["VCF upload", "Integrity validation", "VCF-Canon match", "Interpretation", "Downstream analysis"],
    dropEmpty: "Drop your VCF here",
    dropHelp: "You can also select it from your computer.",
    selectFile: "Select file",
    initialMessage: "Select a VCF file to begin.",
    fileReady: "File ready to submit.",
    modeLabel: "Analysis type",
    quickMode: "Quick analysis",
    quickModeDetail: "Validates structure, headers, and first variants.",
    completeMode: "Full analysis",
    completeModeDetail: "Adds streaming metrics across the full VCF.",
    qaMode: "Quality Control",
    qaModeDetail: "Enables selectable parser, stage controls, and debug reports.",
    parserLabel: "VCF engine",
    parserStreaming: "Stable streaming",
    parserPysam: "Experimental pysam",
    parserHelp: "pysam mirrors the Colab parser more closely when available; if it fails, the backend falls back to streaming.",
    vcfAssemblyLabel: "VCF assembly",
    vcfAssemblyAuto: "Auto-detect",
    vcfAssemblyHelp: "For canon v2, matching is blocked when VCF and canon assemblies differ.",
    playStage: "Run stage",
    debugDownloads: "QA downloads",
    qaVcfCandidates: "VCF position candidates",
    qaVcfJoined: "VCF joined with canon",
    qaStrict: "QA strict matches",
    qaAltReview: "QA ALT review",
    qaPositionReview: "QA position review",
    qaNoVcfMatch: "QA no VCF match",
    qaRunUploadFirst: "Upload or reuse a VCF first.",
    qaRunValidationFirst: "Validate the VCF first.",
    variantLimit: "Initial variants to inspect",
    uploadProgress: "File upload",
    validationProgress: "VCF validation",
    matchProgress: "VCF-Canon match",
    normalizationProgress: "VCF normalization",
    preparationProgress: "Match preparation",
    aiTriageProgress: "AI triage",
    enrichmentProgress: "External enrichment",
    enrichmentQualityProgress: "Enrichment QA",
    groupingPreparationProgress: "Gene+module grouping",
    groupedInterpretationProgress: "Grouped individual interpretation",
    individualInterpretationProgress: "Individual interpretation",
    interpretationNormalizationProgress: "QA normalization",
    globalInterpretationProgress: "Global interpretation",
    finalReportProgress: "Final report",
    submit: "Send and validate",
    securityCheck: "Security check",
    securityCheckHelp: "Protects the backend before accepting large VCF files.",
    securityRequired: "Complete the security check before uploading the file.",
    duplicateTitle: "VCF already available",
    duplicateMessage: "This VCF was already uploaded on {date}. You can reuse it to validate again without uploading it.",
    duplicateUseExisting: "Use existing VCF",
    duplicateUploadAgain: "Upload anyway",
    duplicateCancel: "Cancel",
    reusingUpload: "Using the existing VCF and starting validation...",
    uploading: "Uploading the file in chunks into an isolated workspace...",
    validationStarting: "Starting streaming validation...",
    validating: "Validating",
    connectionRetrying: "Reconnecting to the backend...",
    matchStarting: "Starting VCF-Canon match...",
    matching: "Matching VCF against canon...",
    preparing: "Preparing audit CSVs...",
    normalizing: "Normalizing VCF alleles against the managed reference...",
    enriching: "Enriching observed variants...",
    enrichmentVepBaseProgress: "VEP base enrichment",
    enrichmentCompleteProgress: "Complete enrichment",
    enrichmentVepOnlyProgress: "VEP-only resolution",
    enrichmentQuality: "Checking enrichment coverage and quality...",
    groupingPreparing: "Preparing grouped gene-module payloads...",
    groupedInterpretationStarting: "Starting grouped individual interpretation...",
    groupedInterpreting: "Interpreting gene-module groups...",
    groupedInterpretationComplete: "Grouped individual interpretation finished.",
    groupedInterpretationFailed: "Could not complete grouped individual interpretation.",
    enrichmentFailed: "Could not complete external enrichment.",
    aiTriageFailed: "Could not complete AI triage.",
    retryEnrichment: "Retry enrichment",
    individualInterpretationStarting: "Starting individual interpretation...",
    individualInterpreting: "Interpreting observed variants one by one...",
    individualInterpretationComplete: "Individual interpretation finished.",
    individualInterpretationFailed: "Could not complete individual interpretation.",
    interpretationNormalizationStarting: "Starting QA normalization...",
    interpretationNormalizing: "Applying deterministic post-LLM1 rules...",
    interpretationNormalizationComplete: "QA normalization finished.",
    interpretationNormalizationFailed: "Could not complete QA normalization.",
    globalInterpretationStarting: "Starting global interpretation...",
    globalInterpreting: "Synthesizing global patterns...",
    globalInterpretationComplete: "Global interpretation finished.",
    globalInterpretationFailed: "Could not complete global interpretation.",
    finalReportStarting: "Generating final report...",
    finalReportRendering: "Formatting final Word report...",
    finalReportComplete: "Final report generated.",
    finalReportFailed: "Could not generate the final report.",
    llm2OptionsTitle: "LLM2 options",
    llm2AudienceLabel: "Audience",
    llm2ModelLabel: "LLM2 model",
    llm2LanguageLabel: "LLM2 language",
    audienceTechnical: "Technical",
    audienceProfessional: "Health professional",
    audienceFamily: "Family",
    audienceAll: "All",
    matchFailed: "Could not complete the VCF-Canon match.",
    validationFailed: "Validation failed.",
    uploadFailed: "Could not complete the upload.",
    processFailed: "Could not complete the process.",
    complete: "Validation finished.",
    matchComplete: "VCF-Canon match finished.",
    preparationComplete: "Match preparation finished.",
    aiTriageComplete: "AI triage finished.",
    enrichmentComplete: "External enrichment finished.",
    enrichmentQualityComplete: "Enrichment QA finished.",
    resultValid: "VCF validated",
    resultWarning: "Validated with warnings",
    resultInvalid: "Invalid VCF",
    format: "Format",
    size: "Size",
    sample: "Sample",
    variantsChecked: "Variants inspected",
    totalRows: "Total VCF rows",
    rowsWithId: "Non-empty ID field",
    rowsWithRsid: "rsID in ID field",
    passRows: "PASS rows",
    multiallelic: "Multiallelic",
    snv: "SNV",
    nonSnv: "Non-SNV",
    gtHet: "GT heterozygous",
    gtHomAlt: "GT hom alt",
    gtHomRef: "GT hom ref",
    gtMissing: "GT missing/partial",
    gtComplex: "GT non-diploid/complex",
    malformed: "Malformed rows",
    metricTime: "Metrics time",
    scanComplete: "Full streaming scan",
    notCalculated: "Not calculated",
    topChromosomes: "Top chromosomes/contigs",
    checksum: "SHA-256",
    matchTitle: "VCF-Canon match",
    preparationTitle: "Match preparation",
    aiTriageTitle: "Deterministic AI triage",
    enrichmentTitle: "Observed variant enrichment",
    groupingPreparationTitle: "Gene+module group preparation",
    preparationRows: "Prepared rows",
    preparationObserved: "Observed genotypes",
    preparationHigh: "High confidence",
    preparationModerate: "Moderate confidence",
    preparationLow: "Low confidence",
    aiTriageIncluded: "AI-eligible rows",
    aiTriageStrong: "Strong coding/splice",
    aiTriageUtr: "Strong UTR",
    aiTriageBackgroundExcluded: "Background excluded",
    aiTriageUtrExcluded: "Weak UTR excluded",
    aiTriageDraftExcluded: "Draft optional excluded",
    aiTriageNoncodingExcluded: "Optional noncoding excluded",
    enrichmentObserved: "Enriched rows",
    enrichmentPhysicalVariants: "Physical variants",
    enrichmentVepCoverage: "VEP coverage",
    enrichmentExactRsids: "Exact resolved rsIDs",
    enrichmentVepOnlyVariants: "VEP-only variants",
    enrichmentResolutionAmbiguous: "Ambiguous resolutions",
    enrichmentResolutionAlleleMismatch: "Allele mismatches",
    enrichmentQualityDecision: "QA decision",
    enrichmentInputRows: "Source rows",
    enrichmentPlusRows: "Enrichment Plus rows",
    enrichmentUniqueRsids: "Unique rsIDs",
    enrichmentSources: "External sources",
    enrichmentCacheHits: "Cache hits",
    enrichmentSourceErrors: "Source errors",
    groupingPreparationGroups: "Gene+module groups",
    groupingPreparationVariants: "Source variants",
    groupingPreparationAverageSize: "Average size",
    groupingPreparationLargeGroups: "Groups >25 variants",
    groupedInterpretationTitle: "Grouped individual interpretation",
    groupedInterpretationGroups: "Interpreted groups",
    groupedInterpretationSourceGroups: "Source groups",
    groupedInterpretationSourceVariants: "Grouped source variants",
    groupedInterpretationAverageSize: "Average size",
    groupedInterpretationConflictGroups: "Conflict groups",
    groupedInterpretationReviewGroups: "Review groups",
    groupedInterpretationErrors: "Groups with errors",
    groupedInterpretationModel: "Grouped LLM1 model",
    groupedInterpretationCountLabel: "interpretations",
    individualInterpretationTitle: "Individual interpretation",
    individualInterpretationRows: "Interpreted rows",
    individualInterpretationSourceRows: "LLM1 source rows",
    individualInterpretationErrors: "Rows with errors",
    individualInterpretationModel: "LLM1 model",
    individualInterpretationWorkers: "LLM1 workers",
    individualInterpretationDryRun: "Dry run",
    interpretationNormalizationTitle: "QA normalization",
    interpretationNormalizationRows: "Normalized rows",
    interpretationNormalizationChanged: "Adjusted rows",
    interpretationNormalizationDuplicates: "Normalized duplicates",
    interpretationNormalizationDuplicateGroups: "Duplicate groups",
    interpretationNormalizationWarnings: "QA warnings",
    interpretationConfidenceHigh: "High confidence",
    interpretationConfidenceModerate: "Moderate confidence",
    interpretationConfidenceLow: "Low confidence",
    interpretationConfidenceConflicting: "Conflicting",
    globalInterpretationTitle: "Global interpretation",
    globalInterpretationModel: "LLM2 model",
    globalInterpretationAudience: "Audience",
    globalInterpretationLanguage: "Language",
    globalInterpretationReadiness: "Readiness",
    globalInterpretationVariants: "Synthesized variants",
    globalInterpretationGenes: "Unique genes",
    globalInterpretationRepeatedRsids: "Repeated rsIDs",
    globalInterpretationReview: "Professional review",
    globalInterpretationAmbiguities: "Gene/locus ambiguities",
    finalReportTitle: "Final report",
    finalReportFormat: "Format",
    finalReportSource: "Source",
    finalReportSize: "DOCX size",
    matchStatusStrict: "Strict matches",
    matchStatusAltReview: "Matches needing ALT review",
    matchStatusNoPosition: "No position match",
    matchStatusNoRsid: "No rsID detected",
    matchTargets: "Canon targets",
    matchCandidates: "VCF candidates",
    matchScannedRows: "VCF rows scanned",
    changeCanon: "Change canon",
    canonTitle: "Interpretation canon",
    canonCurrent: "Current canon",
    canonNone: "No canon has been loaded yet.",
    canonUploadHelp: "Upload a new canon as CSV or XLSX. It will be processed and set as the active version.",
    canonSelect: "Select canon",
    canonUpload: "Upload and clean canon",
    canonUploading: "Processing canon...",
    canonProgress: "Canon upload and processing",
    canonStructureProgress: "Structure and cleanup",
    canonCoreProcessingProgress: "Core canon processing",
    canonActivationProgress: "Artifacts and activation",
    canonLoaded: "Canon loaded",
    canonRows: "Non-empty rows",
    canonUniqueRsids: "Unique rsIDs",
    canonRepeatedRsids: "Repeated rsIDs",
    canonManualReview: "Manual review",
    canonAssembly: "Assembly",
    canonSchema: "Schema",
    canonWarnings: "Warnings",
    canonGenesResolved: "Resolved genes",
    canonJobQueued: "Canon queued...",
    canonJobRunning: "Processing canon...",
    canonGeneMasterDownload: "Download gene master",
    canonPreview: "Clean preview",
    canonDownload: "Download full canon",
    rsidMasterDownload: "Download rsID master",
    matchDownload: "Download matches CSV",
    matchPreparationAuditDownload: "Download prepared CSV",
    matchPreparationMinimalDownload: "Download minimal CSV",
    aiTriageDownload: "Download AI triage CSV",
    aiTriageExcludedDownload: "Download excluded audit",
    aiTriageSummaryDownload: "Download AI triage summary",
    enrichmentDownload: "Download interpretive CSV",
    enrichmentPlusDownload: "Download Enrichment Plus CSV",
    enrichmentQaDownload: "Download technical QA CSV",
    normalizedVariantsDownload: "Download normalized variants",
    normalizationAuditDownload: "Download normalization audit",
    enrichmentQualityDownload: "Download enrichment QA summary",
    enrichmentVepBaseDownload: "Download VEP base",
    enrichmentCompleteDownload: "Download complete enrichment",
    enrichmentVepOnlyDownload: "Download VEP-only audit",
    enrichmentResolutionAuditDownload: "Download resolution audit",
    enrichmentPerformanceDownload: "Download performance metrics",
    enrichmentEvidenceAuditDownload: "Download enrichment evidence audit",
    groupingPayloadsDownload: "Download grouped payloads",
    groupingVariantDetailDownload: "Download grouped variant detail",
    groupingSummaryDownload: "Download grouped summary",
    groupedInterpretationDownload: "Download grouped interpretation CSV",
    groupedInterpretationSummaryDownload: "Download grouped interpretation summary",
    individualInterpretationDownload: "Download individual interpretation CSV",
    interpretationNormalizationDownload: "Download normalized CSV",
    globalInterpretationDownload: "Download global interpretation JSON",
    globalInterpretationSectionsDownload: "Download global sections CSV",
    globalInterpretationPayloadDownload: "Download LLM2 payload",
    globalInterpretationSummaryDownload: "Download deterministic summary",
    finalReportDownload: "Download final Word report",
    finalReportDownloadEs: "Download Word report ES",
    finalReportDownloadEn: "Download Word report EN",
    matchDownloadFailed: "Could not download matches CSV.",
    canonDownloadFailed: "Could not download canon.",
    close: "Close",
    errorPopupTitle: "Process interrupted",
    errorPopupRetry: "Please try again. If the problem repeats, we will review the server logs.",
    errorPopupClose: "Got it",
  },
};

function formatBytes(bytes, locale) {
  if (!Number.isFinite(bytes)) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${new Intl.NumberFormat(locale, {
    maximumFractionDigits: unit === 0 ? 0 : 2,
    minimumFractionDigits: unit === 0 ? 0 : 2,
  }).format(value)} ${units[unit]}`;
}

function formatNumber(value, locale) {
  if (!Number.isFinite(Number(value))) return "-";
  return new Intl.NumberFormat(locale).format(Number(value));
}

function clampVariantCount(value) {
  const parsed = Number.parseInt(String(value || ""), 10);
  if (!Number.isFinite(parsed)) return 20;
  return Math.min(100, Math.max(1, parsed));
}

function groupedInterpretationDetailFromMessage(message, t) {
  const text = String(message || "");
  const match = text.match(/\((\d+)\/(\d+)\)/);
  if (!match) return "";
  return `(${match[1]}/${match[2]} ${t.groupedInterpretationCountLabel})`;
}

function stageProgressDetailText(detail) {
  if (!detail) return "";
  const processed = Number(detail.processed || 0);
  const total = Number(detail.total || 0);
  const count = total > 0 ? processed + "/" + total + " " + (detail.unit || "items") : "";
  const metrics = detail.metrics || {};
  const rate = Number(metrics.calls_per_second || metrics.items_per_second || 0);
  const speed = rate > 0 ? `${rate.toFixed(1)}/s` : "";
  return [detail.substage, count, speed, detail.message].filter(Boolean).join(" - ");
}

function ProgressBar({
  label,
  value,
  detail = "",
  tone = "blue",
  downloadLabel = "",
  onDownload = null,
  downloadReady = null,
  onPlay = null,
  playLabel = "",
  playDisabled = false,
}) {
  const complete = Math.round(value) >= 100;
  const canDownload = onDownload && (downloadReady ?? complete);
  return (
    <div className="progress-block">
      <div className="progress-row">
        <span className="progress-label">
          {onPlay && (
            <button
              className="progress-play-button"
              type="button"
              onClick={onPlay}
              disabled={playDisabled}
              aria-label={playLabel || label}
              title={playLabel || label}
            >
              <Play size={14} />
            </button>
          )}
          <span>{label}</span>
        </span>
        <span className="progress-value">
          <strong>{Math.round(value)}%</strong>
          {detail && <span className="progress-detail">{detail}</span>}
          {canDownload && (
            <button className="progress-download-button" type="button" onClick={onDownload} aria-label={downloadLabel || label}>
              <Download size={16} />
            </button>
          )}
        </span>
      </div>
      <div className="progress-track" aria-label={label} aria-valuemin="0" aria-valuemax="100" aria-valuenow={value}>
        <div className={`progress-fill ${tone}`} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      </div>
    </div>
  );
}

function buildCanonProgressGroups(job, schemaVersion, t) {
  if (!job) return [];
  const stageMap = new Map((job.stages || []).map((stage) => [stage.key, stage]));
  const schemaStage = stageMap.get("schema_detection");
  const rowStage = stageMap.get("row_normalization");
  const geneStage = stageMap.get("gene_resolution");
  const artifactStage = stageMap.get("artifact_build");
  const activationStage = stageMap.get("activation");
  const resolvedSchema = schemaVersion || job.schemaDetected || "";
  const isV2 = resolvedSchema === "gene_module_v2";
  const averageProgress = (stages) => {
    const active = stages.filter((stage) => stage && (stage.progress > 0 || stage.status !== "pending"));
    if (active.length === 0) return 0;
    return Math.round(active.reduce((sum, stage) => sum + Number(stage.progress || 0), 0) / active.length);
  };
  const latestMessage = (stages) =>
    stages
      .slice()
      .reverse()
      .find((stage) => stage?.message)?.message || "";

  const groups = [
    {
      key: "structure",
      label: t.canonStructureProgress,
      value: averageProgress([schemaStage, rowStage]),
      detail: latestMessage([schemaStage, rowStage]),
    },
    {
      key: "core",
      label: t.canonCoreProcessingProgress,
      value: isV2 ? averageProgress([geneStage]) : averageProgress([artifactStage]),
      detail: isV2 ? latestMessage([geneStage]) : latestMessage([artifactStage]),
    },
    {
      key: "activation",
      label: t.canonActivationProgress,
      value: isV2 ? averageProgress([artifactStage, activationStage]) : averageProgress([activationStage]),
      detail: isV2 ? latestMessage([artifactStage, activationStage]) : latestMessage([activationStage]),
    },
  ];

  return groups.filter((group) => group.value > 0 || group.detail);
}

function PipelineStepper({ phase, t }) {
  const steps = [
    { key: "upload", label: t.steps[0] },
    { key: "validation", label: t.steps[1] },
    { key: "match", label: t.steps[2] },
    { key: "interpretation", label: t.steps[3] },
    { key: "analysis", label: t.steps[4] },
  ];
  const activeIndex =
    phase === "uploading"
      ? 0
      : phase === "validating"
        ? 1
        : phase === "matching" || phase === "preparing" || phase === "enriching"
          ? 2
          : phase === "individual_interpretation" ||
              phase === "interpretation_normalization" ||
              phase === "global_interpretation" ||
              phase === "final_report" ||
              phase === "done"
            ? 3
            : 0;
  const completeIndex =
    phase === "done"
      ? 3
      : phase === "individual_interpretation" ||
          phase === "interpretation_normalization" ||
          phase === "global_interpretation" ||
          phase === "final_report"
        ? 2
        : phase === "enriching"
          ? 1
        : phase === "matching" || phase === "preparing"
        ? 1
        : phase === "validating"
          ? 0
          : -1;

  return (
    <section className="pipeline" aria-label={t.pipelineLabel}>
      {steps.map((step, index) => {
        const state = index <= completeIndex ? "complete" : index === activeIndex ? "active" : "pending";
        const isFuture = step.key === "analysis";
        return (
          <div className={`pipeline-step ${state} ${isFuture ? "future" : ""}`} key={step.key}>
            <div className="pipeline-dot">{index <= completeIndex ? <CheckCircle2 size={16} /> : index + 1}</div>
            <span>{step.label}</span>
          </div>
        );
      })}
    </section>
  );
}

function MetricCard({ label, value, detail }) {
  return (
    <div className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function ModeSelector({ mode, setMode, t }) {
  return (
    <section className="mode-selector" aria-label={t.modeLabel}>
      <div className="mode-heading">
        <BarChart3 size={20} />
        <span>{t.modeLabel}</span>
      </div>
      <div className="mode-options">
        <button className={mode === "quick" ? "active" : ""} type="button" onClick={() => setMode("quick")}>
          <strong>{t.quickMode}</strong>
          <span>{t.quickModeDetail}</span>
        </button>
        <button className={mode === "complete" ? "active" : ""} type="button" onClick={() => setMode("complete")}>
          <strong>{t.completeMode}</strong>
          <span>{t.completeModeDetail}</span>
        </button>
        <button className={mode === "qa" ? "active" : ""} type="button" onClick={() => setMode("qa")}>
          <strong>{t.qaMode}</strong>
          <span>{t.qaModeDetail}</span>
        </button>
      </div>
    </section>
  );
}

function ErrorDialog({ message, onClose, onRetry, t }) {
  if (!message) return null;
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="error-dialog" role="alertdialog" aria-modal="true" aria-labelledby="error-dialog-title">
        <div className="modal-heading error-dialog-heading">
          <div>
            <h2 id="error-dialog-title">{t.errorPopupTitle}</h2>
            <p>{t.errorPopupRetry}</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label={t.close}>
            <X size={18} />
          </button>
        </div>
        <div className="modal-actions">
          {onRetry && (
            <button className="secondary-button" type="button" onClick={onRetry}>
              <RefreshCw size={17} />
              {t.retryEnrichment}
            </button>
          )}
          <button className="primary-button" type="button" onClick={onClose}>
            {t.errorPopupClose}
          </button>
        </div>
      </section>
    </div>
  );
}

function TurnstileBox({ siteKey, language, onToken, resetKey, t }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!siteKey || !containerRef.current) return undefined;
    let cancelled = false;
    let widgetId = null;

    if (!document.querySelector('script[src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"]')) {
      const script = document.createElement("script");
      script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }

    const timer = window.setInterval(() => {
      if (cancelled || !window.turnstile || !containerRef.current || widgetId !== null) return;
      containerRef.current.innerHTML = "";
      widgetId = window.turnstile.render(containerRef.current, {
        sitekey: siteKey,
        language: language === "es" ? "es" : "en",
        callback: (token) => onToken(token),
        "expired-callback": () => onToken(""),
        "error-callback": () => onToken(""),
      });
    }, 150);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      if (window.turnstile && widgetId !== null) {
        window.turnstile.remove(widgetId);
      }
    };
  }, [siteKey, language, onToken, resetKey]);

  if (!siteKey) return null;

  return (
    <section className="security-check">
      <div>
        <strong>{t.securityCheck}</strong>
        <span>{t.securityCheckHelp}</span>
      </div>
      <div className="turnstile-box" ref={containerRef} />
    </section>
  );
}

function DuplicateUploadModal({ candidate, locale, onUseExisting, onUploadAgain, onCancel, t }) {
  if (!candidate) return null;
  const uploadedAt = new Intl.DateTimeFormat(locale, {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(candidate.updatedAt || candidate.createdAt));

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="duplicate-title">
      <section className="duplicate-modal">
        <h2 id="duplicate-title">{t.duplicateTitle}</h2>
        <p>{t.duplicateMessage.replace("{date}", uploadedAt)}</p>
        <div className="duplicate-file">
          <strong>{candidate.fileName}</strong>
          <span>{formatBytes(candidate.sizeBytes, locale)}</span>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" type="button" onClick={onCancel}>
            {t.duplicateCancel}
          </button>
          <button className="secondary-button" type="button" onClick={onUploadAgain}>
            {t.duplicateUploadAgain}
          </button>
          <button className="primary-button compact" type="button" onClick={onUseExisting}>
            {t.duplicateUseExisting}
          </button>
        </div>
      </section>
    </div>
  );
}

function CanonModal({ open, onClose, language, locale, t }) {
  const fileInputRef = useRef(null);
  const [canonState, setCanonState] = useState(null);
  const [canonFile, setCanonFile] = useState(null);
  const [canonAssembly, setCanonAssembly] = useState("GRCh38");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [canonProgress, setCanonProgress] = useState(0);
  const [error, setError] = useState(null);
  const [canonJob, setCanonJob] = useState(null);
  const [turnstileToken, setTurnstileToken] = useState("");
  const [turnstileResetKey, setTurnstileResetKey] = useState(0);

  async function readJsonResponse(response) {
    const text = await response.text();
    try {
      return text ? JSON.parse(text) : {};
    } catch {
      return { error: text };
    }
  }

  async function loadCanon() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/canon/current`);
      const payload = await readJsonResponse(response);
      if (!response.ok) throw new Error(payload.error || "Could not load canon.");
      setCanonState(payload);
    } catch (caught) {
      setError(caught.message || String(caught));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) {
      loadCanon();
      setCanonFile(null);
      setCanonAssembly("GRCh38");
      setCanonProgress(0);
      setCanonJob(null);
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    }
  }, [open]);

  function postCanonWithProgress(selectedFile) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();

      xhr.open("POST", `${API_BASE}/api/canon/upload`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.setRequestHeader("X-Canon-File-Name", encodeURIComponent(selectedFile.name));
      xhr.setRequestHeader("X-Canon-Assembly", canonAssembly);
      xhr.setRequestHeader("X-Turnstile-Token", turnstileToken);

      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const uploadRatio = event.loaded / event.total;
        setCanonProgress(Math.max(5, Math.min(50, Math.round(uploadRatio * 50))));
      };

      xhr.upload.onload = () => {
        setCanonProgress((current) => Math.max(current, 55));
      };

      xhr.onload = () => {
        const payload = (() => {
          try {
            return xhr.responseText ? JSON.parse(xhr.responseText) : {};
          } catch {
            return { error: xhr.responseText };
          }
        })();
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(payload.error || "Could not upload canon."));
          return;
        }
        setCanonProgress(100);
        resolve(payload);
      };

      xhr.onerror = () => {
        reject(new Error("Could not upload canon."));
      };
      xhr.onabort = () => {
        reject(new Error("Canon upload was aborted."));
      };

      setCanonProgress(5);
      xhr.send(selectedFile);
    });
  }

  async function pollCanonJob(jobId) {
    for (;;) {
      const response = await fetch(`${API_BASE}/api/canon/jobs/${jobId}`);
      const payload = await readJsonResponse(response);
      if (!response.ok) throw new Error(payload.error || "Could not poll canon job.");
      setCanonJob(payload);
      if (payload.status === "complete") {
        setCanonState(payload.result || null);
        return payload.result || null;
      }
      if (payload.status === "failed") {
        throw new Error(payload.error || "Could not upload canon.");
      }
      await sleep(900);
    }
  }

  async function uploadCanon() {
    if (!canonFile) return;
    if (TURNSTILE_SITE_KEY && !turnstileToken) {
      setError(t.securityRequired);
      return;
    }
    setUploading(true);
    setError(null);
    setCanonProgress(0);
    try {
      const payload = await postCanonWithProgress(canonFile);
      setCanonJob(payload);
      await pollCanonJob(payload.id);
      setCanonFile(null);
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    } catch (caught) {
      setError(caught.message || String(caught));
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    } finally {
      setUploading(false);
    }
  }

  async function downloadFile(endpoint, fallbackName) {
    setError(null);
    try {
      const response = await fetch(`${API_BASE}${endpoint}`);
      if (!response.ok) {
        const payload = await readJsonResponse(response);
        throw new Error(payload.error || t.canonDownloadFailed);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/i);
      const fileName = match?.[1] || fallbackName;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught.message || String(caught));
    }
  }

  async function downloadCanon() {
    await downloadFile("/api/canon/current/download", "heal-canon-clean-rows.csv");
  }

  async function downloadRsidMaster() {
    await downloadFile("/api/canon/current/rsid-master", "heal-canon-rsid-master.csv");
  }

  if (!open) return null;

  const current = canonState?.current;
  const rows = canonState?.preview?.rows || [];
  const previewColumns = (canonState?.preview?.columns || []).slice(0, 6);
  const sourceGroups = current?.metadata?.source_group_counts || {};
  const loadedAt = current?.createdAt || current?.timestamps?.completedAt;
  const effectiveRsidLabel = current?.schemaVersion === "gene_module_v2" ? t.canonGeneMasterDownload : t.rsidMasterDownload;
  const canonProgressGroups = buildCanonProgressGroups(canonJob, canonJob?.schemaDetected || current?.schemaVersion, t);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="canon-title">
      <section className="canon-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">{t.eyebrow}</p>
            <h2 id="canon-title">{t.canonTitle}</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label={t.close}>
            <X size={18} />
          </button>
        </div>

        <div className="canon-current">
          <div>
            <strong>{t.canonCurrent}</strong>
            {loading ? (
              <span>{t.canonUploading}</span>
            ) : uploading || canonJob?.status === "queued" || canonJob?.status === "running" ? (
              <span>{canonJob?.message || (canonJob?.status === "queued" ? t.canonJobQueued : t.canonJobRunning)}</span>
            ) : current ? (
              <span>
                {current.sourceFileName}
                {loadedAt ? ` - ${new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" }).format(new Date(loadedAt))}` : ""}
              </span>
            ) : (
              <span>{t.canonNone}</span>
            )}
          </div>
          {current && (
            <>
              <div className="canon-mini-grid">
                <MetricCard label={t.canonRows} value={formatNumber(current.metadata?.rows_nonempty ?? current.metadata?.rows_total, locale)} />
                <MetricCard
                  label={current.schemaVersion === "gene_module_v2" ? t.canonGenesResolved : t.canonUniqueRsids}
                  value={formatNumber(current.schemaVersion === "gene_module_v2" ? current.metadata?.genes_resolved : current.metadata?.unique_rsids, locale)}
                />
                <MetricCard
                  label={current.schemaVersion === "gene_module_v2" ? t.canonWarnings : t.canonRepeatedRsids}
                  value={formatNumber(current.schemaVersion === "gene_module_v2" ? current.metadata?.warnings_count : current.metadata?.duplicate_rsids, locale)}
                />
                <MetricCard
                  label={current.schemaVersion === "gene_module_v2" ? t.canonAssembly : t.canonManualReview}
                  value={
                    current.schemaVersion === "gene_module_v2"
                      ? current.assembly || "-"
                      : formatNumber(sourceGroups.revision_manual || 0, locale)
                  }
                />
              </div>
              <div className="canon-mini-grid">
                <MetricCard label={t.canonSchema} value={current.schemaVersion || "-"} />
                <MetricCard label={t.canonAssembly} value={current.assembly || "-"} />
                <MetricCard label={t.canonWarnings} value={formatNumber(current.warnings?.length || 0, locale)} />
                <MetricCard label={t.canonManualReview} value={formatNumber(sourceGroups.revision_manual || 0, locale)} />
              </div>
              <button className="secondary-button canon-download-button" type="button" onClick={downloadCanon}>
                <Download size={17} />
                {t.canonDownload}
              </button>
              <button className="secondary-button canon-download-button" type="button" onClick={downloadRsidMaster}>
                <Download size={17} />
                {effectiveRsidLabel}
              </button>
            </>
          )}
        </div>

        <div className="canon-upload">
          <p>{t.canonUploadHelp}</p>
          <input
            ref={fileInputRef}
            className="file-input"
            type="file"
            accept=".csv,.xlsx"
            onChange={(event) => {
              setCanonFile(event.target.files?.[0] || null);
              setCanonProgress(0);
            }}
          />
          <div className="canon-upload-row">
            <button className="secondary-button" type="button" onClick={() => fileInputRef.current?.click()}>
              <FileSpreadsheet size={17} />
              {t.canonSelect}
            </button>
            <span>{canonFile ? `${canonFile.name} - ${formatBytes(canonFile.size, locale)}` : ""}</span>
          </div>
          <label className="parser-control">
            <span>{t.canonAssembly}</span>
            <select value={canonAssembly} onChange={(event) => setCanonAssembly(event.target.value)}>
              <option value="GRCh38">GRCh38</option>
              <option value="GRCh37">GRCh37</option>
            </select>
          </label>
          <TurnstileBox
            siteKey={TURNSTILE_SITE_KEY}
            language={language}
            onToken={setTurnstileToken}
            resetKey={turnstileResetKey}
            t={t}
          />
          {error && <p className="error-message">{error}</p>}
          {(uploading || canonProgress > 0) && <ProgressBar label={t.canonProgress} value={canonProgress} tone="green" />}
          {canonProgressGroups.map((group) => (
            <ProgressBar key={group.key} label={group.label} value={group.value} detail={group.detail} tone="blue" />
          ))}
          <button className="primary-button" type="button" disabled={!canonFile || uploading} onClick={uploadCanon}>
            {uploading ? <Loader2 className="spin" size={18} /> : <UploadCloud size={18} />}
            {uploading ? t.canonUploading : t.canonUpload}
          </button>
        </div>

        <div className="canon-preview">
          <h3>{t.canonPreview}</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {previewColumns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 60).map((row) => (
                  <tr key={row.row_id || row.canon_row_id || JSON.stringify(row)}>
                    {previewColumns.map((column) => (
                      <td key={column}>{row[column] || "-"}</td>
                    ))}
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={Math.max(1, previewColumns.length)}>{t.canonNone}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}

function ResultPanel({ result, analysisMode, locale, t }) {
  if (!result) return null;

  const isValid = result.status === "valid";
  const isWarning = result.status === "warning";
  const Icon = isValid || isWarning ? CheckCircle2 : XCircle;
  const stats = result.variant_stats?.counts || {};
  const topChromosomes = result.variant_stats?.top_chromosomes || [];
  const hasFullStats = analysisMode === "complete" && result.variant_stats?.status === "calculated";
  const resultFileLabel = result.metadata?.file_name || result.metadata?.upload_id || "";

  const basicCards = [
    [t.format, result.metadata?.detected_format || "-"],
    [t.size, formatBytes(result.metadata?.size_bytes, locale)],
    [t.sample, result.metadata?.samples?.[0] || "-"],
    [t.variantsChecked, formatNumber(result.metadata?.variant_rows_checked, locale)],
  ];

  const fullCards = hasFullStats
    ? [
        [t.totalRows, formatNumber(stats.total_variant_rows, locale)],
        [t.rowsWithId, formatNumber(stats.rows_with_id, locale)],
        [t.rowsWithRsid, formatNumber(stats.rows_with_rsid, locale)],
        [t.passRows, formatNumber(stats.rows_pass, locale)],
        [t.multiallelic, formatNumber(stats.rows_multiallelic, locale)],
        [t.snv, formatNumber(stats.rows_snv, locale)],
        [t.nonSnv, formatNumber(stats.rows_non_snv, locale)],
        [t.gtHet, formatNumber(stats.gt_het, locale)],
        [t.gtHomAlt, formatNumber(stats.gt_hom_alt, locale)],
        [t.gtHomRef, formatNumber(stats.gt_hom_ref, locale)],
        [t.gtMissing, formatNumber(stats.gt_missing_or_partial, locale)],
        [t.gtComplex, formatNumber(stats.gt_non_diploid_or_complex, locale)],
      ]
    : [];

  if (hasFullStats && stats.rows_malformed) {
    fullCards.push([t.malformed, formatNumber(stats.rows_malformed, locale)]);
  }

  return (
    <section className="result-panel">
      <div className="result-heading">
        <Icon size={22} />
        <div>
          <h2>{isValid ? t.resultValid : isWarning ? t.resultWarning : t.resultInvalid}</h2>
          {resultFileLabel && <p>{resultFileLabel}</p>}
        </div>
      </div>

      <div className="metrics-grid">
        {[...basicCards, ...fullCards].map(([label, value]) => (
          <MetricCard label={label} value={value} key={label} />
        ))}
        {hasFullStats && (
          <MetricCard
            label={t.metricTime}
            value={`${formatNumber(result.variant_stats?.duration_ms, locale)} ms`}
            detail={t.scanComplete}
          />
        )}
      </div>

      {hasFullStats && topChromosomes.length > 0 && (
        <div className="chrom-card">
          <span>{t.topChromosomes}</span>
          <div>
            {topChromosomes.map((item) => (
              <p key={item.chrom}>
                <strong>{item.chrom}</strong>
                <em>{formatNumber(item.count, locale)}</em>
              </p>
            ))}
          </div>
        </div>
      )}

      {result.checksum?.value && (
        <div className="checksum">
          <span>{t.checksum}</span>
          <code>{result.checksum.value}</code>
        </div>
      )}

      {(result.errors?.length > 0 || result.warnings?.length > 0) && (
        <div className="issues">
          {result.errors?.map((error) => (
            <p className="error" key={error}>
              {error}
            </p>
          ))}
          {result.warnings?.map((warning) => (
            <p className="warning" key={warning}>
              {warning}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function MatchResultPanel({ result, locale, t }) {
  const [downloadError, setDownloadError] = useState(null);
  if (!result) return null;

  const isValid = result.status === "valid";
  const Icon = isValid ? CheckCircle2 : XCircle;
  const isGeneModuleV2 = result.schemaVersion === "gene_module_v2";
  const metadata = result.metadata || {};
  const artifactReady = result.artifactsReady || {};
  const statusCounts = metadata.match_status_counts || {};
  const preparation = result.matchPreparation?.metadata || {};
  const aiTriage = result.aiTriage?.metadata || {};
  const confidenceCounts = preparation.confidence_level_counts || {};
  const preparationReviewCounts = preparation.review_status_counts || {};
  const enrichment = result.variantEnrichment?.metadata || {};
  const enrichmentQuality = enrichment.qualityGate || metadata.enrichment_quality_gate || {};
  const groupingPreparation = result.groupPrep?.metadata || {};
  const groupedInterpretation = result.groupedIndividualInterpretation?.metadata || {};
  const individualInterpretation = result.individualInterpretation?.metadata || {};
  const interpretationNormalization = result.interpretationNormalization?.metadata || {};
  const globalInterpretation = result.globalInterpretation?.metadata || {};
  const finalReport = result.finalReport?.metadata || {};
  const normalizedConfidenceCounts = interpretationNormalization.confidence_level_counts || {};
  const globalConfidenceCounts = globalInterpretation.confidence_distribution || {};
  const enrichmentSourceErrors = Object.values(enrichment.source_error_counts || {}).reduce(
    (total, value) => total + Number(value || 0),
    0,
  );
  const fileLabel = metadata.file_name || metadata.upload_id || "";
  const cards = isGeneModuleV2
    ? [
        [t.matchCandidates, formatNumber(metadata.variant_gene_candidates, locale)],
        [t.preparationRows, formatNumber(metadata.sheet_final_rows, locale)],
        [t.enrichmentObserved, formatNumber(metadata.unique_gene_matches, locale)],
        [t.preparationLow, formatNumber(metadata.background_rows, locale)],
        [t.preparationModerate, formatNumber(metadata.optional_annotation_rows, locale)],
        [t.preparationHigh, formatNumber(metadata.annotation_needed_rows, locale)],
        [t.matchScannedRows, formatNumber(metadata.scanned_variant_rows, locale)],
        [t.sample, metadata.sample_name || "-"],
      ]
    : [
        [t.matchTargets, formatNumber(metadata.target_keys, locale)],
        [t.matchCandidates, formatNumber(metadata.vcf_candidates_rows, locale)],
        [t.matchStatusStrict, formatNumber(statusCounts.match_strict || 0, locale)],
        [t.matchStatusAltReview, formatNumber(statusCounts.match_likely_needs_alt_review || 0, locale)],
        [t.matchStatusNoPosition, formatNumber(statusCounts.no_vcf_match_by_chr_pos || 0, locale)],
        [t.matchStatusNoRsid, formatNumber(statusCounts.no_rsid_detected || 0, locale)],
        [t.matchScannedRows, formatNumber(metadata.scanned_variant_rows, locale)],
        [t.sample, metadata.sample_name || "-"],
      ];
  const preparationCards = result.matchPreparation
    ? isGeneModuleV2
      ? [
          [t.preparationRows, formatNumber(preparation.rows_total, locale)],
          [t.preparationObserved, formatNumber(preparation.rows_with_genotype, locale)],
          [t.preparationHigh, formatNumber(preparationReviewCounts.ready_for_annotation || 0, locale)],
          [t.preparationModerate, formatNumber(preparationReviewCounts.optional_annotation || 0, locale)],
          [t.preparationLow, formatNumber(preparationReviewCounts.background_only || 0, locale)],
        ]
      : [
          [t.preparationRows, formatNumber(preparation.rows_total, locale)],
          [t.preparationObserved, formatNumber(preparation.rows_with_genotype, locale)],
          [t.preparationHigh, formatNumber(confidenceCounts.High || 0, locale)],
          [t.preparationModerate, formatNumber(confidenceCounts.Moderate || 0, locale)],
          [t.preparationLow, formatNumber(confidenceCounts.Low || 0, locale)],
        ]
    : [];
  const aiTriageCards = result.aiTriage
    ? [
        [t.aiTriageIncluded, formatNumber(aiTriage.included_for_ai, locale)],
        [t.aiTriageStrong, formatNumber(aiTriage.included_strong_region, locale)],
        [t.aiTriageUtr, formatNumber(aiTriage.included_strong_utr, locale)],
        [t.aiTriageBackgroundExcluded, formatNumber(aiTriage.excluded_background, locale)],
        [t.aiTriageUtrExcluded, formatNumber(aiTriage.excluded_utr_weak, locale)],
        [t.aiTriageDraftExcluded, formatNumber(aiTriage.excluded_draft_optional, locale)],
        [t.aiTriageNoncodingExcluded, formatNumber(aiTriage.excluded_optional_noncoding, locale)],
      ]
    : [];
  const enrichmentCards = result.variantEnrichment
    ? isGeneModuleV2
      ? [
          [t.enrichmentInputRows, formatNumber(enrichmentQuality.moduleRows, locale)],
          [t.enrichmentPhysicalVariants, formatNumber(enrichmentQuality.physicalVariants, locale)],
          [t.enrichmentVepCoverage, `${((Number(enrichmentQuality.vepCoverage) || 0) * 100).toFixed(1)}%`],
          [t.enrichmentExactRsids, formatNumber(enrichmentQuality.exactRsidsResolved, locale)],
          [t.enrichmentVepOnlyVariants, formatNumber(enrichmentQuality.vepOnlyVariants, locale)],
          [t.enrichmentResolutionAmbiguous, formatNumber(enrichmentQuality.resolutionCounts?.ambiguous || 0, locale)],
          [t.enrichmentResolutionAlleleMismatch, formatNumber(enrichmentQuality.resolutionCounts?.vep_colocated_allele_mismatch || 0, locale)],
          [t.enrichmentSourceErrors, formatNumber(Object.values(enrichmentQuality.sourceErrors || {}).reduce((sum, value) => sum + Number(value || 0), 0), locale)],
          [t.enrichmentQualityDecision, enrichmentQuality.status || "-"],
        ]
      : [
        [t.enrichmentInputRows, formatNumber(enrichment.source_rows, locale)],
        [t.enrichmentObserved, formatNumber(enrichment.output_rows, locale)],
        [t.enrichmentPlusRows, formatNumber(enrichment.plus_rows, locale)],
        [t.enrichmentUniqueRsids, formatNumber(enrichment.unique_rsids, locale)],
        [t.enrichmentSources, formatNumber(enrichment.sources?.length || 0, locale)],
        [t.enrichmentCacheHits, formatNumber(enrichment.cache_hits, locale)],
        [t.enrichmentSourceErrors, formatNumber(enrichmentSourceErrors, locale)],
      ]
    : [];
  const groupingPreparationCards = result.groupPrep
    ? [
        [t.groupingPreparationGroups, formatNumber(groupingPreparation.total_groups, locale)],
        [t.groupingPreparationVariants, formatNumber(groupingPreparation.source_variants_total, locale)],
        [t.groupingPreparationAverageSize, formatNumber(groupingPreparation.average_group_size, locale)],
        [t.groupingPreparationLargeGroups, formatNumber(groupingPreparation.groups_gt_25, locale)],
      ]
    : [];
  const groupedInterpretationCards = result.groupedIndividualInterpretation
    ? [
        [t.groupedInterpretationSourceGroups, formatNumber(groupedInterpretation.source_groups, locale)],
        [t.groupedInterpretationGroups, formatNumber(groupedInterpretation.interpreted_groups, locale)],
        [t.groupedInterpretationSourceVariants, formatNumber(groupedInterpretation.source_variants_total, locale)],
        [t.groupedInterpretationAverageSize, formatNumber(groupedInterpretation.average_group_size, locale)],
        [t.groupedInterpretationConflictGroups, formatNumber(groupedInterpretation.groups_with_conflict_flag, locale)],
        [t.groupedInterpretationReviewGroups, formatNumber(groupedInterpretation.groups_requires_review, locale)],
        [t.groupedInterpretationErrors, formatNumber(groupedInterpretation.error_groups, locale)],
        [t.groupedInterpretationModel, groupedInterpretation.model || "-"],
      ]
    : [];
  const individualInterpretationCards = result.individualInterpretation
    ? [
        [t.individualInterpretationSourceRows, formatNumber(individualInterpretation.source_rows, locale)],
        [t.individualInterpretationRows, formatNumber(individualInterpretation.interpreted_rows, locale)],
        [t.individualInterpretationErrors, formatNumber(individualInterpretation.error_rows, locale)],
        [t.individualInterpretationModel, individualInterpretation.model || "-"],
        [t.individualInterpretationWorkers, formatNumber(individualInterpretation.max_workers, locale)],
        [t.individualInterpretationDryRun, individualInterpretation.dry_run ? "true" : "false"],
      ]
    : [];
  const normalizationWarnings = Object.values(interpretationNormalization.qa_warning_counts || {}).reduce(
    (total, value) => total + Number(value || 0),
    0,
  );
  const interpretationNormalizationCards = result.interpretationNormalization
    ? [
        [t.interpretationNormalizationRows, formatNumber(interpretationNormalization.output_rows, locale)],
        [t.interpretationNormalizationChanged, formatNumber(interpretationNormalization.changed_rows, locale)],
        [
          t.interpretationNormalizationDuplicates,
          formatNumber(interpretationNormalization.duplicate_groups_normalized, locale),
        ],
        [
          t.interpretationNormalizationDuplicateGroups,
          formatNumber(interpretationNormalization.duplicate_groups, locale),
        ],
        [t.interpretationConfidenceHigh, formatNumber(normalizedConfidenceCounts.High || 0, locale)],
        [t.interpretationConfidenceModerate, formatNumber(normalizedConfidenceCounts.Moderate || 0, locale)],
        [t.interpretationConfidenceLow, formatNumber(normalizedConfidenceCounts.Low || 0, locale)],
        [t.interpretationConfidenceConflicting, formatNumber(normalizedConfidenceCounts.Conflicting || 0, locale)],
        [t.interpretationNormalizationWarnings, formatNumber(normalizationWarnings, locale)],
      ]
    : [];
  const globalInterpretationCards = result.globalInterpretation
    ? [
        [t.globalInterpretationVariants, formatNumber(globalInterpretation.variant_count_observed, locale)],
        [t.enrichmentUniqueRsids, formatNumber(globalInterpretation.unique_rsid_count, locale)],
        [t.globalInterpretationGenes, formatNumber(globalInterpretation.unique_gene_count, locale)],
        [t.globalInterpretationRepeatedRsids, formatNumber(globalInterpretation.repeated_rsid_count, locale)],
        [t.interpretationConfidenceHigh, formatNumber(globalConfidenceCounts.High || 0, locale)],
        [t.interpretationConfidenceModerate, formatNumber(globalConfidenceCounts.Moderate || 0, locale)],
        [t.interpretationConfidenceLow, formatNumber(globalConfidenceCounts.Low || 0, locale)],
        [t.interpretationConfidenceConflicting, formatNumber(globalConfidenceCounts.Conflicting || 0, locale)],
        [t.globalInterpretationReview, formatNumber(globalInterpretation.professional_review_variant_count, locale)],
        [t.globalInterpretationAmbiguities, formatNumber(globalInterpretation.gene_locus_ambiguity_count, locale)],
        [t.globalInterpretationModel, globalInterpretation.model || "-"],
        [t.globalInterpretationAudience, globalInterpretation.audience_mode || "-"],
        [t.globalInterpretationLanguage, globalInterpretation.language_mode || "-"],
        [t.globalInterpretationReadiness, globalInterpretation.overall_readiness || "-"],
      ]
    : [];
  const finalReportCards = result.finalReport
    ? [
        [t.finalReportFormat, finalReport.format || "docx"],
        [t.finalReportSource, finalReport.source || "-"],
        [t.finalReportSize, formatBytes(Number(finalReport.docx_size_bytes || 0), locale)],
        [t.globalInterpretationVariants, formatNumber(finalReport.variant_count_observed, locale)],
        [t.globalInterpretationGenes, formatNumber(finalReport.unique_gene_count, locale)],
        [t.globalInterpretationLanguage, finalReport.language_mode || "-"],
      ]
    : [];

  async function downloadCsv(endpoint, fallbackName) {
    if (!result.jobId) return;
    setDownloadError(null);
    try {
      const response = await fetch(`${API_BASE}${endpoint}`, {
        headers: accessHeaders(result.accessToken || getJobAccessToken(result.jobId)),
      });
      if (!response.ok) {
        const text = await response.text();
        let payload = {};
        try {
          payload = text ? JSON.parse(text) : {};
        } catch {
          payload = { error: text };
        }
        throw new Error(payload.error || t.matchDownloadFailed);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/i);
      const fileName = match?.[1] || fallbackName;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setDownloadError(caught.message || String(caught));
    }
  }

  async function downloadMatches() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/download`, "heal-vcf-canon-matches.csv");
  }

  async function downloadPreparedAudit() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/preparation-audit`, "heal-match-preparation-audit.csv");
  }

  async function downloadPreparedMinimal() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/preparation-minimal`, "heal-match-preparation-minimal.csv");
  }

  async function downloadAiTriage() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/ai-triage`, "heal-fon-ai-triage.csv");
  }

  async function downloadAiTriageExcluded() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/ai-triage-excluded`,
      "heal-fon-ai-triage-excluded-audit.csv",
    );
  }

  async function downloadAiTriageSummary() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/ai-triage-summary`, "heal-fon-ai-triage-summary.json");
  }

  async function downloadEnrichment() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-interpretive`,
      "heal-fon-interpretation-enriched-observed69.csv",
    );
  }

  async function downloadEnrichmentPlus() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-plus`,
      "heal-fon-interpretation-enrichment-plus.csv",
    );
  }

  async function downloadNormalizedVariants() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/normalized-variants`, "normalized_variants.csv");
  }

  async function downloadNormalizationAudit() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/normalization-excluded-audit`,
      "normalization_excluded_audit.csv",
    );
  }

  async function downloadEnrichmentQuality() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-quality-summary`,
      "enrichment_quality_summary.json",
    );
  }

  async function downloadEnrichmentEvidenceAudit() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-evidence-audit`,
      "v2_enrichment_evidence_audit.jsonl",
    );
  }

  async function downloadEnrichmentVepBase() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-vep-base`, "v2_enrichment_vep_base.csv");
  }

  async function downloadEnrichmentComplete() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-complete`, "v2_enrichment_complete.csv");
  }

  async function downloadEnrichmentVepOnly() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-vep-only`, "v2_enrichment_vep_only_audit.csv");
  }

  async function downloadEnrichmentResolutionAudit() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-resolution-audit`, "v2_enrichment_resolution_audit.jsonl");
  }

  async function downloadEnrichmentPerformance() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-performance`, "enrichment_performance_summary.json");
  }

  async function downloadGroupedPayloads() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/grouped-payloads`, "gene_module_group_payloads.csv");
  }

  async function downloadGroupedVariantDetail() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/grouped-variant-detail`,
      "gene_module_group_variant_detail.csv",
    );
  }

  async function downloadGroupedSummary() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/grouped-summary`, "gene_module_grouping_summary.json");
  }

  async function downloadGroupedInterpretations() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/grouped-interpretations`,
      "gene_module_group_interpretations.csv",
    );
  }

  async function downloadGroupedInterpretationSummary() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/grouped-interpretation-summary`,
      "gene_module_group_interpretation_summary.json",
    );
  }

  async function downloadEnrichmentQa() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment`, "heal-observed-variant-enrichment.csv");
  }

  async function downloadIndividualInterpretations() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/individual-interpretations`,
      "heal-individual-variant-interpretations.csv",
    );
  }

  async function downloadNormalizedInterpretations() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/individual-interpretations-normalized`,
      "heal-individual-variant-interpretations-normalized.csv",
    );
  }

  async function downloadGlobalInterpretation() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/global-interpretation`, "heal-global-interpretation.json");
  }

  async function downloadGlobalInterpretationSections() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/global-interpretation-sections`,
      "heal-global-interpretation-sections.csv",
    );
  }

  async function downloadGlobalInterpretationPayload() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/global-interpretation-payload`,
      "heal-global-interpretation-payload.json",
    );
  }

  async function downloadGlobalInterpretationDeterministicSummary() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/global-interpretation-deterministic-summary`,
      "heal-global-interpretation-deterministic-summary.json",
    );
  }

  async function downloadFinalReport() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/final-report`, "heal-final-report.docx");
  }

  async function downloadDebugArtifact(artifact, fallbackName) {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/debug/${artifact}`, fallbackName);
  }

  return (
    <section className="result-panel">
      <div className="result-heading">
        <Icon size={22} />
        <div>
          <h2>{t.matchTitle}</h2>
          {fileLabel && <p>{fileLabel}</p>}
        </div>
      </div>
      <div className="metrics-grid">
        {cards.map(([label, value]) => (
          <MetricCard label={label} value={value} key={label} />
        ))}
      </div>
      {preparationCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.preparationTitle}</h3>
          <div className="metrics-grid">
            {preparationCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {aiTriageCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.aiTriageTitle}</h3>
          <div className="metrics-grid">
            {aiTriageCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {enrichmentCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.enrichmentTitle}</h3>
          <div className="metrics-grid">
            {enrichmentCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {groupingPreparationCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.groupingPreparationTitle}</h3>
          <div className="metrics-grid">
            {groupingPreparationCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {groupedInterpretationCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.groupedInterpretationTitle}</h3>
          <div className="metrics-grid">
            {groupedInterpretationCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {individualInterpretationCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.individualInterpretationTitle}</h3>
          <div className="metrics-grid">
            {individualInterpretationCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {interpretationNormalizationCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.interpretationNormalizationTitle}</h3>
          <div className="metrics-grid">
            {interpretationNormalizationCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {globalInterpretationCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.globalInterpretationTitle}</h3>
          <div className="metrics-grid">
            {globalInterpretationCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {finalReportCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.finalReportTitle}</h3>
          <div className="metrics-grid">
            {finalReportCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      <div className="match-download-actions">
        {!isGeneModuleV2 && artifactReady.finalReport && <button className="secondary-button match-download-button" type="button" onClick={downloadFinalReport}>
          <Download size={17} />
          {t.finalReportDownload}
        </button>}
        {artifactReady.matches && <button className="secondary-button match-download-button" type="button" onClick={downloadMatches}>
          <Download size={17} />
          {t.matchDownload}
        </button>}
        {artifactReady.preparation && <button className="secondary-button match-download-button" type="button" onClick={downloadPreparedAudit}>
          <Download size={17} />
          {t.matchPreparationAuditDownload}
        </button>}
        {artifactReady.preparation && <button className="secondary-button match-download-button" type="button" onClick={downloadPreparedMinimal}>
          <Download size={17} />
          {t.matchPreparationMinimalDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.aiTriage && <button className="secondary-button match-download-button" type="button" onClick={downloadAiTriage}>
          <Download size={17} />
          {t.aiTriageDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.aiTriage && <button className="secondary-button match-download-button" type="button" onClick={downloadAiTriageExcluded}>
          <Download size={17} />
          {t.aiTriageExcludedDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.aiTriage && <button className="secondary-button match-download-button" type="button" onClick={downloadAiTriageSummary}>
          <Download size={17} />
          {t.aiTriageSummaryDownload}
        </button>}
        {artifactReady.enrichment && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichment}>
          <Download size={17} />
          {t.enrichmentDownload}
        </button>}
        {artifactReady.enrichmentPlus && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentPlus}>
          <Download size={17} />
          {t.enrichmentPlusDownload}
        </button>}
        {artifactReady.enrichmentQuality && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentQa}>
          <Download size={17} />
          {t.enrichmentQaDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.normalization && <button className="secondary-button match-download-button" type="button" onClick={downloadNormalizedVariants}>
          <Download size={17} />
          {t.normalizedVariantsDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.normalizationAudit && <button className="secondary-button match-download-button" type="button" onClick={downloadNormalizationAudit}>
          <Download size={17} />
          {t.normalizationAuditDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentQuality && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentQuality}>
          <Download size={17} />
          {t.enrichmentQualityDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichment && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentEvidenceAudit}>
          <Download size={17} />
          {t.enrichmentEvidenceAuditDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentVepBase && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentVepBase}>
          <Download size={17} />
          {t.enrichmentVepBaseDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentComplete && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentComplete}>
          <Download size={17} />
          {t.enrichmentCompleteDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentVepOnly && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentVepOnly}>
          <Download size={17} />
          {t.enrichmentVepOnlyDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentResolutionAudit && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentResolutionAudit}>
          <Download size={17} />
          {t.enrichmentResolutionAuditDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentPerformance && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentPerformance}>
          <Download size={17} />
          {t.enrichmentPerformanceDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedPayloads && <button className="secondary-button match-download-button" type="button" onClick={downloadGroupedPayloads}>
          <Download size={17} />
          {t.groupingPayloadsDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedVariantDetail && <button className="secondary-button match-download-button" type="button" onClick={downloadGroupedVariantDetail}>
          <Download size={17} />
          {t.groupingVariantDetailDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedPayloads && <button className="secondary-button match-download-button" type="button" onClick={downloadGroupedSummary}>
          <Download size={17} />
          {t.groupingSummaryDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedInterpretation && <button className="secondary-button match-download-button" type="button" onClick={downloadGroupedInterpretations}>
          <Download size={17} />
          {t.groupedInterpretationDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedInterpretation && <button className="secondary-button match-download-button" type="button" onClick={downloadGroupedInterpretationSummary}>
          <Download size={17} />
          {t.groupedInterpretationSummaryDownload}
        </button>}
        {!isGeneModuleV2 && artifactReady.individualInterpretation && <button className="secondary-button match-download-button" type="button" onClick={downloadIndividualInterpretations}>
          <Download size={17} />
          {t.individualInterpretationDownload}
        </button>}
        {!isGeneModuleV2 && artifactReady.interpretationNormalization && <button className="secondary-button match-download-button" type="button" onClick={downloadNormalizedInterpretations}>
          <Download size={17} />
          {t.interpretationNormalizationDownload}
        </button>}
        {!isGeneModuleV2 && artifactReady.globalInterpretation && <button className="secondary-button match-download-button" type="button" onClick={downloadGlobalInterpretationSections}>
          <Download size={17} />
          {t.globalInterpretationSectionsDownload}
        </button>}
      </div>
      <h3 className="result-subtitle">{t.debugDownloads}</h3>
      <div className="match-download-actions">
        {artifactReady.debug && <button className="secondary-button match-download-button" type="button" onClick={() => downloadDebugArtifact("vcf_candidates", "heal-vcf-candidates.csv")}>
          <Download size={17} />
          {t.qaVcfCandidates}
        </button>}
        {artifactReady.debug && <button className="secondary-button match-download-button" type="button" onClick={() => downloadDebugArtifact("vcf_joined_chr_pos", "heal-vcf-joined-chr-pos.csv")}>
          <Download size={17} />
          {t.qaVcfJoined}
        </button>}
        {artifactReady.debug && <button className="secondary-button match-download-button" type="button" onClick={() => downloadDebugArtifact("match_strict", "heal-match-strict.csv")}>
          <Download size={17} />
          {t.qaStrict}
        </button>}
        {artifactReady.debug && <button className="secondary-button match-download-button" type="button" onClick={() => downloadDebugArtifact("alt_review", "heal-match-alt-review.csv")}>
          <Download size={17} />
          {t.qaAltReview}
        </button>}
        {artifactReady.debug && <button className="secondary-button match-download-button" type="button" onClick={() => downloadDebugArtifact("position_review", "heal-match-position-review.csv")}>
          <Download size={17} />
          {t.qaPositionReview}
        </button>}
        {artifactReady.debug && <button className="secondary-button match-download-button" type="button" onClick={() => downloadDebugArtifact("no_vcf_match", "heal-match-no-vcf-match.csv")}>
          <Download size={17} />
          {t.qaNoVcfMatch}
        </button>}
        {!isGeneModuleV2 && artifactReady.globalInterpretation && <button className="secondary-button match-download-button" type="button" onClick={downloadGlobalInterpretation}>
          <Download size={17} />
          {t.globalInterpretationDownload}
        </button>}
        {!isGeneModuleV2 && artifactReady.globalInterpretation && <button className="secondary-button match-download-button" type="button" onClick={downloadGlobalInterpretationPayload}>
          <Download size={17} />
          {t.globalInterpretationPayloadDownload}
        </button>}
        {!isGeneModuleV2 && artifactReady.globalInterpretation && <button className="secondary-button match-download-button" type="button" onClick={downloadGlobalInterpretationDeterministicSummary}>
          <Download size={17} />
          {t.globalInterpretationSummaryDownload}
        </button>}
      </div>
      {downloadError && <p className="error-message">{downloadError}</p>}
      {(result.errors?.length > 0 || result.warnings?.length > 0) && (
        <div className="issues">
          {result.errors?.map((error) => (
            <p className="error" key={error}>
              {error}
            </p>
          ))}
          {result.warnings?.map((warning) => (
            <p className="warning" key={warning}>
              {warning}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function App() {
  const fileInputRef = useRef(null);
  const [language, setLanguage] = useState("es");
  const [analysisMode, setAnalysisMode] = useState("quick");
  const [vcfParser, setVcfParser] = useState("streaming");
  const [vcfAssembly, setVcfAssembly] = useState("auto");
  const [file, setFile] = useState(null);
  const [uploadRecord, setUploadRecord] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [validationProgress, setValidationProgress] = useState(0);
  const [matchProgress, setMatchProgress] = useState(0);
  const [normalizationProgress, setNormalizationProgress] = useState(0);
  const [preparationProgress, setPreparationProgress] = useState(0);
  const [aiTriageProgress, setAiTriageProgress] = useState(0);
  const [enrichmentProgress, setEnrichmentProgress] = useState(0);
  const [enrichmentVepBaseProgress, setEnrichmentVepBaseProgress] = useState(0);
  const [enrichmentCompleteProgress, setEnrichmentCompleteProgress] = useState(0);
  const [enrichmentVepOnlyProgress, setEnrichmentVepOnlyProgress] = useState(0);
  const [enrichmentQualityProgress, setEnrichmentQualityProgress] = useState(0);
  const [groupingPreparationProgress, setGroupingPreparationProgress] = useState(0);
  const [groupedInterpretationProgress, setGroupedInterpretationProgress] = useState(0);
  const [individualInterpretationProgress, setIndividualInterpretationProgress] = useState(0);
  const [interpretationNormalizationProgress, setInterpretationNormalizationProgress] = useState(0);
  const [globalInterpretationProgress, setGlobalInterpretationProgress] = useState(0);
  const [finalReportProgress, setFinalReportProgress] = useState(0);
  const [stageProgressDetails, setStageProgressDetails] = useState({});
  const [qaLlm2Model, setQaLlm2Model] = useState("gpt-5-mini");
  const [qaAudienceMode, setQaAudienceMode] = useState("all");
  const [qaLanguageMode, setQaLanguageMode] = useState("both");
  const [maxVariants, setMaxVariants] = useState(20);
  const [phase, setPhase] = useState("idle");
  const [messageKey, setMessageKey] = useState("initialMessage");
  const [customMessage, setCustomMessage] = useState("");
  const [groupedInterpretationDetail, setGroupedInterpretationDetail] = useState("");
  const [individualInterpretationDetail, setIndividualInterpretationDetail] = useState("");
  const [result, setResult] = useState(null);
  const [matchResult, setMatchResult] = useState(null);
  const [matchArtifactsReady, setMatchArtifactsReady] = useState({
    matches: false,
    debug: false,
    normalization: false,
    normalizationAudit: false,
    preparation: false,
    aiTriage: false,
    enrichment: false,
    enrichmentVepBase: false,
    enrichmentResolutionAudit: false,
    enrichmentComplete: false,
    enrichmentVepOnly: false,
    enrichmentPerformance: false,
    enrichmentInterpretive: false,
    enrichmentPlus: false,
    enrichmentQuality: false,
    groupedPayloads: false,
    groupedVariantDetail: false,
    groupedInterpretation: false,
    individualInterpretation: false,
    interpretationNormalization: false,
    globalInterpretation: false,
    finalReport: false,
    finalReportEs: false,
    finalReportEn: false,
  });
  const [finalReportDownloads, setFinalReportDownloads] = useState({
    es: null,
    en: null,
  });
  const [error, setError] = useState(null);
  const [errorDialog, setErrorDialog] = useState(null);
  const [retryEnrichmentJobId, setRetryEnrichmentJobId] = useState(null);
  const [turnstileToken, setTurnstileToken] = useState("");
  const [turnstileResetKey, setTurnstileResetKey] = useState(0);
  const [duplicateCandidate, setDuplicateCandidate] = useState(null);
  const [canonOpen, setCanonOpen] = useState(false);
  const activeAccessTokenRef = useRef("");

  const t = COPY[language];
  const locale = language === "es" ? "es-AR" : "en-US";
  const isGeneModuleV2 = matchResult?.schemaVersion === "gene_module_v2";
  const v2DownstreamBlocked = isGeneModuleV2 && matchResult?.metadata?.downstream_supported === false;
  const legacyLabel = (label) => label + " (legacy)";
  const canSend = useMemo(
    () =>
      file &&
      !isBusyPhase(phase),
    [file, phase],
  );
  const statusMessage = customMessage || t[messageKey] || t.initialMessage;

  function clearFinalReportDownloads() {
    setFinalReportDownloads((current) => {
      Object.values(current).forEach((item) => {
        if (item?.url) URL.revokeObjectURL(item.url);
      });
      return { es: null, en: null };
    });
  }

  function pickFile(nextFile) {
    clearFinalReportDownloads();
    setFile(nextFile || null);
    setUploadProgress(0);
    setValidationProgress(0);
    setMatchProgress(0);
    setNormalizationProgress(0);
    setPreparationProgress(0);
    setAiTriageProgress(0);
    setEnrichmentProgress(0);
    setEnrichmentVepBaseProgress(0);
    setEnrichmentCompleteProgress(0);
    setEnrichmentVepOnlyProgress(0);
    setEnrichmentQualityProgress(0);
    setGroupingPreparationProgress(0);
    setGroupedInterpretationProgress(0);
    setIndividualInterpretationProgress(0);
    setInterpretationNormalizationProgress(0);
    setGlobalInterpretationProgress(0);
    setFinalReportProgress(0);
    setStageProgressDetails({});
    setResult(null);
    setMatchResult(null);
    setMatchArtifactsReady({
      matches: false,
      debug: false,
      normalization: false,
      normalizationAudit: false,
      preparation: false,
      aiTriage: false,
      enrichment: false,
      enrichmentVepBase: false,
      enrichmentResolutionAudit: false,
      enrichmentComplete: false,
      enrichmentVepOnly: false,
      enrichmentPerformance: false,
      enrichmentInterpretive: false,
      enrichmentPlus: false,
      enrichmentQuality: false,
      groupedPayloads: false,
      groupedVariantDetail: false,
      groupedInterpretation: false,
      individualInterpretation: false,
      interpretationNormalization: false,
      globalInterpretation: false,
      finalReport: false,
      finalReportEs: false,
      finalReportEn: false,
    });
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    setDuplicateCandidate(null);
    setUploadRecord(null);
    activeAccessTokenRef.current = "";
    setPhase("idle");
    setCustomMessage("");
    setGroupedInterpretationDetail("");
    setIndividualInterpretationDetail("");
    setTurnstileToken("");
    setTurnstileResetKey((current) => current + 1);
    setMessageKey(nextFile ? "fileReady" : "initialMessage");
  }

  async function readJsonResponse(response) {
    const text = await response.text();
    try {
      return text ? JSON.parse(text) : {};
    } catch {
      return { error: text };
    }
  }

  async function downloadCsv(endpoint, fallbackName) {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      headers: accessHeaders(activeAccessTokenRef.current || getJobAccessToken(matchResult?.jobId)),
    });
    if (!response.ok) {
      const payload = await readJsonResponse(response);
      throw new Error(payload.error || t.matchDownloadFailed);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/i);
    const fileName = match?.[1] || fallbackName;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function fetchArtifactBlob(endpoint, fallbackName) {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      headers: accessHeaders(activeAccessTokenRef.current || getJobAccessToken(matchResult?.jobId)),
    });
    if (!response.ok) {
      const payload = await readJsonResponse(response);
      throw new Error(payload.error || t.matchDownloadFailed);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/i);
    return {
      blob,
      fileName: match?.[1] || fallbackName,
    };
  }

  function downloadStoredReport(report) {
    if (!report?.url) return;
    const link = document.createElement("a");
    link.href = report.url;
    link.download = report.fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async function downloadMatchArtifact(kind) {
    if (!matchResult?.jobId) return;
    setError(null);
    try {
      if (kind === "matches") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/download`, "heal-vcf-canon-matches.csv");
      } else if (kind === "normalization") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/normalized-variants`, "normalized_variants.csv");
      } else if (kind === "normalizationAudit") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/normalization-excluded-audit`,
          "normalization_excluded_audit.csv",
        );
      } else if (kind === "preparation") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/preparation-audit`,
          "heal-match-preparation-audit.csv",
        );
      } else if (kind === "aiTriage") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/ai-triage`,
          "heal-fon-ai-triage.csv",
        );
      } else if (kind === "aiTriageExcluded") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/ai-triage-excluded`,
          "heal-fon-ai-triage-excluded-audit.csv",
        );
      } else if (kind === "aiTriageSummary") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/ai-triage-summary`,
          "heal-fon-ai-triage-summary.json",
        );
      } else if (kind === "enrichment") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-interpretive`,
          "heal-fon-interpretation-enriched-observed69.csv",
        );
      } else if (kind === "enrichmentPlus") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-plus`,
          "heal-fon-interpretation-enrichment-plus.csv",
        );
      } else if (kind === "enrichmentQuality") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-quality-summary`,
          "enrichment_quality_summary.json",
        );
      } else if (kind === "enrichmentEvidenceAudit") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-evidence-audit`,
          "v2_enrichment_evidence_audit.jsonl",
        );
      } else if (kind === "enrichmentVepBase") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/enrichment-vep-base`, "v2_enrichment_vep_base.csv");
      } else if (kind === "enrichmentComplete") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/enrichment-complete`, "v2_enrichment_complete.csv");
      } else if (kind === "enrichmentVepOnly") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/enrichment-vep-only`, "v2_enrichment_vep_only_audit.csv");
      } else if (kind === "enrichmentResolutionAudit") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/enrichment-resolution-audit`, "v2_enrichment_resolution_audit.jsonl");
      } else if (kind === "enrichmentPerformance") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/enrichment-performance`, "enrichment_performance_summary.json");
      } else if (kind === "groupedPayloads") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/grouped-payloads`,
          "gene_module_group_payloads.csv",
        );
      } else if (kind === "groupedVariantDetail") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/grouped-variant-detail`,
          "gene_module_group_variant_detail.csv",
        );
      } else if (kind === "groupedSummary") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/grouped-summary`,
          "gene_module_grouping_summary.json",
        );
      } else if (kind === "groupedInterpretation") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/grouped-interpretations`,
          "gene_module_group_interpretations.csv",
        );
      } else if (kind === "groupedInterpretationSummary") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/grouped-interpretation-summary`,
          "gene_module_group_interpretation_summary.json",
        );
      } else if (kind === "individualInterpretation") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/individual-interpretations`,
          "heal-individual-variant-interpretations.csv",
        );
      } else if (kind === "interpretationNormalization") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/individual-interpretations-normalized`,
          "heal-individual-variant-interpretations-normalized.csv",
        );
      } else if (kind === "globalInterpretation") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/global-interpretation`,
          "heal-global-interpretation.json",
        );
      } else if (kind === "finalReport") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/final-report`,
          "heal-final-report.docx",
        );
      } else if (kind === "finalReportEs") {
        downloadStoredReport(finalReportDownloads.es);
      } else if (kind === "finalReportEn") {
        downloadStoredReport(finalReportDownloads.en);
      }
    } catch (caught) {
      setError(caught.message || String(caught));
    }
  }

  async function uploadFile(selectedFile) {
    const initResponse = await fetch(`${API_BASE}/api/uploads/init`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fileName: selectedFile.name,
        sizeBytes: selectedFile.size,
        contentType: selectedFile.type || "application/octet-stream",
        turnstileToken,
      }),
    });
    const initUpload = await readJsonResponse(initResponse);
    if (!initResponse.ok) throw new Error(initUpload.error || t.uploadFailed);

    const chunkSize = initUpload.chunkSizeBytes;
    const totalChunks = initUpload.totalChunks;
    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
      const start = chunkIndex * chunkSize;
      const end = Math.min(selectedFile.size, start + chunkSize);
      const chunk = selectedFile.slice(start, end);
      const chunkResponse = await fetch(`${API_BASE}/api/uploads/${initUpload.uploadId}/chunks/${chunkIndex}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Upload-Id": initUpload.uploadId,
          "X-Chunk-Index": String(chunkIndex),
          ...accessHeaders(initUpload.accessToken),
        },
        body: chunk,
      });
      const chunkResult = await readJsonResponse(chunkResponse);
      if (!chunkResponse.ok) throw new Error(chunkResult.error || t.uploadFailed);
      setUploadProgress(Math.round(((chunkIndex + 1) / totalChunks) * 96));
    }

    const completeResponse = await fetch(`${API_BASE}/api/uploads/${initUpload.uploadId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...accessHeaders(initUpload.accessToken) },
      body: JSON.stringify({ accessToken: initUpload.accessToken }),
    });
    const completeUpload = await readJsonResponse(completeResponse);
    if (!completeResponse.ok) throw new Error(completeUpload.error || t.uploadFailed);
    setUploadProgress(100);
    return { ...completeUpload, accessToken: completeUpload.accessToken || initUpload.accessToken };
  }

  async function lookupExistingUpload(selectedFile) {
    const response = await fetch(`${API_BASE}/api/uploads/lookup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fileName: selectedFile.name,
        sizeBytes: selectedFile.size,
      }),
    });
    const lookup = await readJsonResponse(response);
    if (!response.ok) return null;
    return lookup.match || null;
  }

  async function pollValidation(jobId) {
    let transientFailures = 0;
    for (;;) {
      let response;
      try {
        response = await fetch(`${API_BASE}/api/validations/${jobId}`);
      } catch (caught) {
        transientFailures += 1;
        if (transientFailures > POLL_RETRY_LIMIT) throw caught;
        setCustomMessage(`${t.connectionRetrying} (${transientFailures}/${POLL_RETRY_LIMIT})`);
        await sleep(POLL_RETRY_DELAY_MS);
        continue;
      }
      if (!response.ok) {
        if (response.status >= 500 && transientFailures < POLL_RETRY_LIMIT) {
          transientFailures += 1;
          setCustomMessage(`${t.connectionRetrying} (${transientFailures}/${POLL_RETRY_LIMIT})`);
          await sleep(POLL_RETRY_DELAY_MS);
          continue;
        }
        throw new Error(await response.text());
      }
      transientFailures = 0;
      const job = await response.json();
      setValidationProgress(job.progress || 0);
      setCustomMessage(job.message || t.validating);

      if (job.status === "complete") return { ...(job.result || {}), jobId: job.id };
      if (job.status === "failed") throw new Error(job.error || t.validationFailed);
      await sleep(VALIDATION_POLL_DELAY_MS);
    }
  }

  function updateMatchSnapshot(job) {
    const ready = job.artifactsReady || {};
    setMatchArtifactsReady((current) => ({
      matches: Boolean(ready.matches),
      debug: Boolean(ready.debug),
      normalization: Boolean(ready.normalization),
      normalizationAudit: Boolean(ready.normalizationAudit),
      preparation: Boolean(ready.preparation),
      aiTriage: Boolean(ready.aiTriage),
      enrichment: Boolean(ready.enrichment),
      enrichmentVepBase: Boolean(ready.enrichmentVepBase),
      enrichmentResolutionAudit: Boolean(ready.enrichmentResolutionAudit),
      enrichmentComplete: Boolean(ready.enrichmentComplete),
      enrichmentVepOnly: Boolean(ready.enrichmentVepOnly),
      enrichmentPerformance: Boolean(ready.enrichmentPerformance),
      enrichmentInterpretive: Boolean(ready.enrichmentInterpretive),
      enrichmentPlus: Boolean(ready.enrichmentPlus),
      enrichmentQuality: Boolean(ready.enrichmentQuality),
      groupedPayloads: Boolean(ready.groupedPayloads),
      groupedVariantDetail: Boolean(ready.groupedVariantDetail),
      groupedInterpretation: Boolean(ready.groupedInterpretation),
      individualInterpretation: Boolean(ready.individualInterpretation),
      interpretationNormalization: Boolean(ready.interpretationNormalization),
      globalInterpretation: Boolean(ready.globalInterpretation),
      finalReport: Boolean(ready.finalReport),
      finalReportEs: current.finalReportEs,
      finalReportEn: current.finalReportEn,
    }));
    const accessToken = activeAccessTokenRef.current || getJobAccessToken(job.id);
    if (accessToken) storeJobAccessToken(job.id, accessToken);
    if (
      job.result ||
      ready.matches ||
      ready.normalization ||
      ready.preparation ||
      ready.aiTriage ||
      ready.enrichment ||
      ready.enrichmentVepBase ||
      ready.enrichmentResolutionAudit ||
      ready.enrichmentComplete ||
      ready.enrichmentVepOnly ||
      ready.enrichmentPerformance ||
      ready.enrichmentInterpretive ||
      ready.enrichmentPlus ||
      ready.enrichmentQuality ||
      ready.groupedPayloads ||
      ready.groupedVariantDetail ||
      ready.groupedInterpretation ||
      ready.individualInterpretation ||
      ready.interpretationNormalization ||
      ready.globalInterpretation ||
      ready.finalReport
    ) {
      setMatchResult({
        ...(job.result || {}),
        jobId: job.id,
        artifactsReady: ready,
        accessToken,
      });
    }
  }

  async function pollMatch(jobId) {
    let transientFailures = 0;
    for (;;) {
      let response;
      try {
        response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}`);
      } catch (caught) {
        transientFailures += 1;
        if (transientFailures > POLL_RETRY_LIMIT) throw caught;
        setCustomMessage(`${t.connectionRetrying} (${transientFailures}/${POLL_RETRY_LIMIT})`);
        await sleep(POLL_RETRY_DELAY_MS);
        continue;
      }
      if (!response.ok) {
        if (response.status >= 500 && transientFailures < POLL_RETRY_LIMIT) {
          transientFailures += 1;
          setCustomMessage(`${t.connectionRetrying} (${transientFailures}/${POLL_RETRY_LIMIT})`);
          await sleep(POLL_RETRY_DELAY_MS);
          continue;
        }
        throw new Error(await response.text());
      }
      transientFailures = 0;
      const job = await response.json();
      updateMatchSnapshot(job);
      if (job.stage && job.stageProgressDetail) {
        setStageProgressDetails((current) => ({
          ...current,
          [job.stage]: stageProgressDetailText(job.stageProgressDetail),
        }));
      }
      setMatchProgress(job.progress || 0);
      if (job.stage === "normalizing") {
        setPhase("normalizing");
        setMatchProgress(0);
        setNormalizationProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.normalizing);
      } else if (job.stage === "preparing") {
        setPhase("preparing");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.preparing);
      } else if (job.stage === "triaging") {
        setPhase("triaging");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.aiTriageProgress);
      } else if (job.stage === "enrichment_vep") {
        setPhase("enrichment_vep");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentVepBaseProgress(job.stageProgress ?? job.progress ?? 0);
        setEnrichmentProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.enriching);
      } else if (job.stage === "enrichment_complete") {
        setPhase("enrichment_complete");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentVepBaseProgress(100);
        setEnrichmentCompleteProgress(job.stageProgress ?? job.progress ?? 0);
        setEnrichmentProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.enriching);
      } else if (job.stage === "enrichment_vep_only") {
        setPhase("enrichment_vep_only");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentVepBaseProgress(100);
        setEnrichmentCompleteProgress(100);
        setEnrichmentVepOnlyProgress(job.stageProgress ?? job.progress ?? 0);
        setEnrichmentProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.enriching);
      } else if (job.stage === "enriching") {
        setPhase("enriching");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentVepBaseProgress(100);
        setEnrichmentCompleteProgress(100);
        setEnrichmentVepOnlyProgress(0);
        setEnrichmentProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.enriching);
      } else if (job.stage === "enrichment_quality_gate") {
        setPhase("enrichment_quality_gate");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentVepBaseProgress(100);
        setEnrichmentCompleteProgress(100);
        setEnrichmentVepOnlyProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.enrichmentQuality);
      } else if (job.stage === "grouping_preparation") {
        setPhase("grouping_preparation");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setGroupingPreparationProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.groupingPreparing);
      } else if (job.stage === "grouped_individual_interpretation") {
        setPhase("grouped_individual_interpretation");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setGroupingPreparationProgress(100);
        setGroupedInterpretationProgress(job.stageProgress ?? job.progress ?? 0);
        setGroupedInterpretationDetail(groupedInterpretationDetailFromMessage(job.message, t));
        setCustomMessage(job.message || t.groupedInterpreting);
      } else if (job.stage === "individual_interpretation") {
        setPhase("individual_interpretation");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setGroupingPreparationProgress(100);
        setGroupedInterpretationProgress(100);
        setGroupedInterpretationDetail("");
        setIndividualInterpretationProgress(job.stageProgress ?? job.progress ?? 0);
        setIndividualInterpretationDetail(job.message || "");
        setCustomMessage(t.individualInterpreting);
      } else if (job.stage === "interpretation_normalization") {
        setPhase("interpretation_normalization");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setGroupingPreparationProgress(100);
        setGroupedInterpretationProgress(100);
        setGroupedInterpretationDetail("");
        setIndividualInterpretationProgress(100);
        setIndividualInterpretationDetail("");
        setInterpretationNormalizationProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.interpretationNormalizing);
      } else if (job.stage === "global_interpretation") {
        setPhase("global_interpretation");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setGroupingPreparationProgress(100);
        setGroupedInterpretationProgress(100);
        setGroupedInterpretationDetail("");
        setIndividualInterpretationProgress(100);
        setIndividualInterpretationDetail("");
        setInterpretationNormalizationProgress(100);
        setGlobalInterpretationProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.globalInterpreting);
      } else if (job.stage === "final_report") {
        setPhase("final_report");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setGroupingPreparationProgress(100);
        setGroupedInterpretationProgress(100);
        setGroupedInterpretationDetail("");
        setIndividualInterpretationProgress(100);
        setIndividualInterpretationDetail("");
        setInterpretationNormalizationProgress(100);
        setGlobalInterpretationProgress(100);
        setFinalReportProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.finalReportRendering);
      } else {
        setGroupedInterpretationDetail("");
        setIndividualInterpretationDetail("");
        setMatchProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.matching);
      }

      if (job.status === "complete") {
        if (job.artifactsReady?.normalization || job.result?.vcfNormalization) {
          setNormalizationProgress(100);
        }
        setPreparationProgress(100);
        if (job.artifactsReady?.aiTriage || job.result?.aiTriage) {
          setAiTriageProgress(100);
        }
        if (job.artifactsReady?.enrichment || job.result?.variantEnrichment) {
          setEnrichmentProgress(100);
        }
        if (job.artifactsReady?.enrichmentQuality || job.result?.metadata?.enrichment_quality_gate) {
          setEnrichmentQualityProgress(100);
        }
        if (job.artifactsReady?.groupedPayloads || job.result?.groupPrep) {
          setGroupingPreparationProgress(100);
        }
        if (job.artifactsReady?.groupedInterpretation || job.result?.groupedIndividualInterpretation) {
          setGroupedInterpretationProgress(100);
        }
        if (job.artifactsReady?.individualInterpretation || job.result?.individualInterpretation) {
          setIndividualInterpretationProgress(100);
        }
        if (job.artifactsReady?.interpretationNormalization || job.result?.interpretationNormalization) {
          setInterpretationNormalizationProgress(100);
        }
        if (job.artifactsReady?.globalInterpretation || job.result?.globalInterpretation) {
          setGlobalInterpretationProgress(100);
        }
        if (job.artifactsReady?.finalReport || job.result?.finalReport) {
          setFinalReportProgress(100);
        }
        const accessToken = activeAccessTokenRef.current || getJobAccessToken(job.id);
        if (accessToken) storeJobAccessToken(job.id, accessToken);
        return { ...(job.result || {}), jobId: job.id, artifactsReady: job.artifactsReady || {}, accessToken };
      }
      if (job.status === "failed") {
        const failed = new Error(
          job.error ||
            (job.stage === "interpretation_normalization"
              ? t.interpretationNormalizationFailed
              : job.stage === "global_interpretation"
                ? t.globalInterpretationFailed
              : job.stage === "final_report"
                ? t.finalReportFailed
              : job.stage === "grouped_individual_interpretation"
                ? t.groupedInterpretationFailed
              : job.stage === "grouping_preparation"
                ? t.groupedInterpretationFailed
              : job.stage === "triaging"
                ? t.aiTriageFailed
              : job.stage === "individual_interpretation"
              ? t.individualInterpretationFailed
              : job.stage === "enriching"
                ? t.enrichmentFailed
                : job.stage === "normalizing" || job.stage === "enrichment_quality_gate"
                  ? t.enrichmentFailed
                : t.matchFailed),
        );
        failed.stage = job.stage;
        failed.jobId = job.id;
        failed.artifactsReady = job.artifactsReady || {};
        failed.result = job.result || null;
        throw failed;
      }
      const pollDelay = isLongPollingStage(job.stage) ? LONG_STAGE_POLL_DELAY_MS : MATCH_POLL_DELAY_MS;
      await sleep(pollDelay);
    }
  }

  async function resolveUpload({ skipDuplicateCheck = false, reuseUpload = null } = {}) {
    if (!file) return;
    if (TURNSTILE_SITE_KEY && !turnstileToken) {
      setError(t.securityRequired);
      return;
    }
    setPhase("uploading");
    setMessageKey("uploading");
    setCustomMessage("");
    setDuplicateCandidate(null);

    let upload = reuseUpload;
    if (upload) {
      setUploadProgress(100);
      setCustomMessage(t.reusingUpload);
    } else {
      if (!skipDuplicateCheck) {
        const existingUpload = await lookupExistingUpload(file);
        if (existingUpload) {
          setPhase("idle");
          setUploadProgress(0);
          setValidationProgress(0);
          setMatchProgress(0);
          setNormalizationProgress(0);
          setPreparationProgress(0);
          setAiTriageProgress(0);
          setEnrichmentProgress(0);
          setEnrichmentVepBaseProgress(0);
          setEnrichmentCompleteProgress(0);
          setEnrichmentVepOnlyProgress(0);
          setEnrichmentQualityProgress(0);
          setGroupingPreparationProgress(0);
          setGroupedInterpretationProgress(0);
          setIndividualInterpretationProgress(0);
          setInterpretationNormalizationProgress(0);
          setGlobalInterpretationProgress(0);
          setFinalReportProgress(0);
          clearFinalReportDownloads();
          setMatchArtifactsReady({
            matches: false,
            debug: false,
            normalization: false,
            normalizationAudit: false,
            preparation: false,
            aiTriage: false,
            enrichment: false,
            enrichmentVepBase: false,
            enrichmentResolutionAudit: false,
            enrichmentComplete: false,
            enrichmentVepOnly: false,
            enrichmentPerformance: false,
            enrichmentInterpretive: false,
            enrichmentPlus: false,
            enrichmentQuality: false,
            groupedPayloads: false,
            groupedVariantDetail: false,
            groupedInterpretation: false,
            individualInterpretation: false,
            interpretationNormalization: false,
            globalInterpretation: false,
            finalReport: false,
            finalReportEs: false,
            finalReportEn: false,
          });
          setDuplicateCandidate(existingUpload);
          setMessageKey("fileReady");
          return null;
        }
      }
      upload = await uploadFile(file);
    }
    setUploadRecord(upload);
    activeAccessTokenRef.current = upload.accessToken || "";
    return upload;
  }

  async function validateUpload(upload) {
    if (!upload) return null;
    const variantLimit = clampVariantCount(maxVariants);
    const shouldCalculateStats = analysisMode === "complete" || analysisMode === "qa";
    setMaxVariants(variantLimit);
    setPhase("validating");
    setMessageKey("validationStarting");
    setValidationProgress(5);

    const validationStart = await fetch(`${API_BASE}/api/validations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        uploadId: upload.uploadId,
        accessToken: upload.accessToken,
        fileName: upload.fileName,
        calculateChecksum: true,
        calculateStats: shouldCalculateStats,
        analysisMode,
        maxVariantsToCheck: variantLimit,
        vcfParser,
      }),
    });
    if (!validationStart.ok) throw new Error(await validationStart.text());
    const job = await validationStart.json();
    const validationResult = await pollValidation(job.id);
    setResult(validationResult);
    setValidationProgress(100);
    return validationResult;
  }

  async function runMatch(upload) {
    if (!upload) return null;
    setPhase("matching");
    setMessageKey("matchStarting");
    setMatchProgress(5);
    setNormalizationProgress(0);
    setPreparationProgress(0);
    setAiTriageProgress(0);
    setEnrichmentProgress(0);
    setEnrichmentQualityProgress(0);
    setGroupingPreparationProgress(0);
    setGroupedInterpretationProgress(0);
    setIndividualInterpretationProgress(0);
    setInterpretationNormalizationProgress(0);
    setGlobalInterpretationProgress(0);
    setFinalReportProgress(0);

    const matchStart = await fetch(`${API_BASE}/api/vcf-canon-matches`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...accessHeaders(upload.accessToken) },
      body: JSON.stringify({ uploadId: upload.uploadId, accessToken: upload.accessToken, vcfParser, vcfAssembly, analysisMode }),
    });
    const matchJob = await readJsonResponse(matchStart);
    if (!matchStart.ok) throw new Error(matchJob.error || t.matchFailed);
    const nextMatchResult = await pollMatch(matchJob.id);
    setMatchResult(nextMatchResult);
    setPhase("done");
    const downstreamSupported = nextMatchResult?.metadata?.downstream_supported !== false;
    setMessageKey(
      downstreamSupported
        ? "enrichmentComplete"
        : nextMatchResult?.variantEnrichment
          ? "enrichmentQualityComplete"
        : nextMatchResult?.groupedIndividualInterpretation
          ? "groupedInterpretationComplete"
          : "aiTriageComplete",
    );
    setCustomMessage("");
    setMatchProgress(100);
    if (nextMatchResult?.artifactsReady?.normalization || nextMatchResult?.vcfNormalization) {
      setNormalizationProgress(100);
    }
    setPreparationProgress(100);
    if (nextMatchResult?.artifactsReady?.aiTriage || nextMatchResult?.aiTriage) {
      setAiTriageProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.enrichment || nextMatchResult?.variantEnrichment) {
      setEnrichmentProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.enrichmentVepBase) {
      setEnrichmentVepBaseProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.enrichmentComplete) {
      setEnrichmentCompleteProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.enrichmentVepOnly) {
      setEnrichmentVepOnlyProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.enrichmentQuality || nextMatchResult?.metadata?.enrichment_quality_gate) {
      setEnrichmentQualityProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.groupedPayloads || nextMatchResult?.groupPrep) {
      setGroupingPreparationProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.groupedInterpretation || nextMatchResult?.groupedIndividualInterpretation) {
      setGroupedInterpretationProgress(100);
    }
    return nextMatchResult;
  }

  async function runIndividualInterpretation(jobId) {
    if (!jobId) return null;
    setPhase("individual_interpretation");
    setMessageKey("individualInterpretationStarting");
    setCustomMessage("");
    setIndividualInterpretationProgress(5);

    const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}/individual-interpretation`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...accessHeaders(activeAccessTokenRef.current || getJobAccessToken(jobId)) },
      body: JSON.stringify({ accessToken: activeAccessTokenRef.current || getJobAccessToken(jobId) }),
    });
    const started = await readJsonResponse(response);
    if (!response.ok) throw new Error(started.error || t.individualInterpretationFailed);
    const nextMatchResult = await pollMatch(started.id);
    setMatchResult(nextMatchResult);
    setPhase("done");
    setMessageKey("individualInterpretationComplete");
    setCustomMessage("");
    setIndividualInterpretationProgress(100);
    return nextMatchResult;
  }

  async function runInterpretationNormalization(jobId) {
    if (!jobId) return null;
    setPhase("interpretation_normalization");
    setMessageKey("interpretationNormalizationStarting");
    setCustomMessage("");
    setInterpretationNormalizationProgress(8);
    const accessToken = activeAccessTokenRef.current || getJobAccessToken(jobId);

    const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}/interpretation-normalization`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...accessHeaders(accessToken) },
      body: JSON.stringify({ accessToken }),
    });
    const started = await readJsonResponse(response);
    if (!response.ok) throw new Error(started.error || t.interpretationNormalizationFailed);
    const nextMatchResult = await pollMatch(started.id);
    setMatchResult(nextMatchResult);
    setPhase("done");
    setMessageKey("interpretationNormalizationComplete");
    setCustomMessage("");
    setInterpretationNormalizationProgress(100);
    return nextMatchResult;
  }

  function llm2Options() {
    const isQa = analysisMode === "qa";
    return {
      analysisMode,
      languageMode: isQa ? qaLanguageMode : defaultLanguageMode(language),
      audienceMode: isQa ? qaAudienceMode : defaultAudienceMode(analysisMode),
      model: isQa ? qaLlm2Model : undefined,
    };
  }

  async function runGlobalInterpretation(jobId, options = {}) {
    if (!jobId) return null;
    setPhase("global_interpretation");
    setMessageKey("globalInterpretationStarting");
    setCustomMessage("");
    setGlobalInterpretationProgress(8);
    const accessToken = activeAccessTokenRef.current || getJobAccessToken(jobId);
    const baseOptions = llm2Options();
    const requestOptions = {
      ...baseOptions,
      ...options,
    };

    const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}/global-interpretation`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...accessHeaders(accessToken) },
      body: JSON.stringify({ accessToken, ...requestOptions }),
    });
    const started = await readJsonResponse(response);
    if (!response.ok) throw new Error(started.error || t.globalInterpretationFailed);
    const nextMatchResult = await pollMatch(started.id);
    setMatchResult(nextMatchResult);
    setPhase("done");
    setMessageKey("globalInterpretationComplete");
    setCustomMessage("");
    setGlobalInterpretationProgress(100);
    return nextMatchResult;
  }

  function rememberFinalReport(languageMode, artifact) {
    const normalizedLanguage = languageMode === "en" ? "en" : "es";
    const url = URL.createObjectURL(artifact.blob);
    setFinalReportDownloads((current) => {
      if (current[normalizedLanguage]?.url) URL.revokeObjectURL(current[normalizedLanguage].url);
      return {
        ...current,
        [normalizedLanguage]: {
          url,
          fileName: artifact.fileName,
        },
      };
    });
    setMatchArtifactsReady((current) => ({
      ...current,
      finalReport: true,
      finalReportEs: current.finalReportEs || normalizedLanguage === "es",
      finalReportEn: current.finalReportEn || normalizedLanguage === "en",
    }));
  }

  async function runFinalReport(jobId, options = {}) {
    if (!jobId) return null;
    setPhase("final_report");
    setMessageKey("finalReportStarting");
    setCustomMessage("");
    setFinalReportProgress(8);
    const accessToken = activeAccessTokenRef.current || getJobAccessToken(jobId);
    const selectedLanguageMode = options.languageMode || llm2Options().languageMode;
    const selectedAudienceMode = options.audienceMode || llm2Options().audienceMode;

    const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}/final-report`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...accessHeaders(accessToken) },
      body: JSON.stringify({ accessToken, languageMode: selectedLanguageMode, audienceMode: selectedAudienceMode }),
    });
    const started = await readJsonResponse(response);
    if (!response.ok) throw new Error(started.error || t.finalReportFailed);
    const nextMatchResult = await pollMatch(started.id);
    setMatchResult(nextMatchResult);
    setPhase("done");
    setMessageKey("finalReportComplete");
    setCustomMessage("");
    setFinalReportProgress(100);
    if (options.capture !== false) {
      const artifact = await fetchArtifactBlob(
        `/api/vcf-canon-matches/${jobId}/final-report`,
        selectedLanguageMode === "en" ? "heal-final-report-en.docx" : "heal-final-report-es.docx",
      );
      rememberFinalReport(selectedLanguageMode, artifact);
    }
    return nextMatchResult;
  }

  async function runGlobalAndFinalReports(jobId) {
    if (!jobId) return null;
    const options = llm2Options();
    const languages = analysisMode === "qa" ? reportLanguagesForMode(options.languageMode) : [defaultLanguageMode(language)];
    let latestResult = null;
    for (const languageMode of languages) {
      const globalResult = await runGlobalInterpretation(jobId, {
        ...options,
        languageMode,
      });
      latestResult = await runFinalReport(globalResult?.jobId || jobId, {
        languageMode,
        audienceMode: options.audienceMode,
      });
    }
    return latestResult;
  }

  async function retryEnrichment() {
    const jobId = retryEnrichmentJobId || matchResult?.jobId;
    if (!jobId) return;
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    setPhase("enriching");
    setMessageKey("enriching");
    setCustomMessage("");
    setEnrichmentProgress(5);
    try {
      const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}/retry-enrichment`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...accessHeaders(activeAccessTokenRef.current || getJobAccessToken(jobId)) },
        body: JSON.stringify({ accessToken: activeAccessTokenRef.current || getJobAccessToken(jobId) }),
      });
      const started = await readJsonResponse(response);
      if (!response.ok) throw new Error(started.error || t.enrichmentFailed);
      const nextMatchResult = await pollMatch(started.id);
      setMatchResult(nextMatchResult);
      setPhase("done");
      setMessageKey("enrichmentComplete");
      setCustomMessage("");
      setEnrichmentProgress(100);
    } catch (caught) {
      setPhase("error");
      setError(t.enrichmentFailed);
      setErrorDialog(true);
      setRetryEnrichmentJobId(caught.jobId || jobId);
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function runQaUpload() {
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    try {
      const upload = await resolveUpload({ skipDuplicateCheck: false });
      if (!upload) return;
      setPhase("done");
      setMessageKey("fileReady");
      setCustomMessage("");
    } catch (caught) {
      setPhase("error");
      setError(caught.message || String(caught));
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function runQaValidation() {
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    try {
      const upload = uploadRecord || (await resolveUpload({ skipDuplicateCheck: false }));
      if (!upload) return;
      await validateUpload(upload);
      setPhase("done");
      setMessageKey("complete");
      setCustomMessage("");
    } catch (caught) {
      setPhase("error");
      setError(caught.message || String(caught));
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function runQaMatch() {
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    try {
      const upload = uploadRecord;
      if (!upload) throw new Error(t.qaRunUploadFirst);
      if (!result || result.status === "invalid") throw new Error(t.qaRunValidationFirst);
      await runMatch(upload);
    } catch (caught) {
      setPhase("error");
      if (caught.stage === "enriching") {
        setError(t.enrichmentFailed);
        setErrorDialog(true);
        setRetryEnrichmentJobId(caught.jobId || matchResult?.jobId || null);
      } else if (caught.stage === "grouped_individual_interpretation" || caught.stage === "grouping_preparation") {
        setError(t.groupedInterpretationFailed);
      } else if (caught.stage === "individual_interpretation") {
        setError(t.individualInterpretationFailed);
      } else {
        setError(caught.message || String(caught));
      }
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function runQaIndividualInterpretation() {
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    try {
      const jobId = matchResult?.jobId;
      if (!jobId) throw new Error(t.enrichmentFailed);
      await runIndividualInterpretation(jobId);
    } catch (caught) {
      setPhase("error");
      setError(t.individualInterpretationFailed);
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function runQaInterpretationNormalization() {
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    try {
      const jobId = matchResult?.jobId;
      if (!jobId) throw new Error(t.interpretationNormalizationFailed);
      await runInterpretationNormalization(jobId);
    } catch (caught) {
      setPhase("error");
      setError(t.interpretationNormalizationFailed);
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function runQaGlobalInterpretation() {
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    try {
      const jobId = matchResult?.jobId;
      if (!jobId) throw new Error(t.globalInterpretationFailed);
      await runGlobalInterpretation(jobId);
    } catch (caught) {
      setPhase("error");
      setError(t.globalInterpretationFailed);
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function runQaFinalReport() {
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    try {
      const jobId = matchResult?.jobId;
      if (!jobId) throw new Error(t.finalReportFailed);
      await runGlobalAndFinalReports(jobId);
    } catch (caught) {
      setPhase("error");
      setError(t.finalReportFailed);
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function submit({ skipDuplicateCheck = false, reuseUpload = null } = {}) {
    if (!file) return;
    setError(null);
    setErrorDialog(null);
    setRetryEnrichmentJobId(null);
    setResult(null);
    setMatchResult(null);
    setStageProgressDetails({});
    clearFinalReportDownloads();
    setMatchArtifactsReady({
      matches: false,
      debug: false,
      normalization: false,
      normalizationAudit: false,
      preparation: false,
      aiTriage: false,
      enrichment: false,
      enrichmentVepBase: false,
      enrichmentResolutionAudit: false,
      enrichmentComplete: false,
      enrichmentVepOnly: false,
      enrichmentPerformance: false,
      enrichmentInterpretive: false,
      enrichmentPlus: false,
      enrichmentQuality: false,
      groupedPayloads: false,
      groupedVariantDetail: false,
      groupedInterpretation: false,
      individualInterpretation: false,
      interpretationNormalization: false,
      globalInterpretation: false,
      finalReport: false,
      finalReportEs: false,
      finalReportEn: false,
    });
    setGroupedInterpretationDetail("");
    setIndividualInterpretationDetail("");
    setUploadProgress(0);
    setValidationProgress(0);
    setMatchProgress(0);
    setNormalizationProgress(0);
    setPreparationProgress(0);
    setAiTriageProgress(0);
    setEnrichmentProgress(0);
    setEnrichmentQualityProgress(0);
    setGroupingPreparationProgress(0);
    setGroupedInterpretationProgress(0);
    setIndividualInterpretationProgress(0);
    setInterpretationNormalizationProgress(0);
    setGlobalInterpretationProgress(0);
    setFinalReportProgress(0);
    setUploadRecord(null);
    activeAccessTokenRef.current = "";

    try {
      const upload = await resolveUpload({ skipDuplicateCheck, reuseUpload });
      if (!upload) return;
      const validationResult = await validateUpload(upload);
      if (validationResult?.status === "invalid") {
        setPhase("done");
        setMessageKey("complete");
        setCustomMessage("");
        setTurnstileToken("");
        setTurnstileResetKey((current) => current + 1);
        return;
      }
      const nextMatchResult = await runMatch(upload);
      if (nextMatchResult?.metadata?.downstream_supported === false) {
        setTurnstileToken("");
        setTurnstileResetKey((current) => current + 1);
        return;
      }
      const individualResult = await runIndividualInterpretation(nextMatchResult?.jobId);
      const normalizedResult = await runInterpretationNormalization(individualResult?.jobId || nextMatchResult?.jobId);
      await runGlobalAndFinalReports(normalizedResult?.jobId || individualResult?.jobId || nextMatchResult?.jobId);
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    } catch (caught) {
      setPhase("error");
      if (caught.stage === "enriching") {
        setError(t.enrichmentFailed);
        setErrorDialog(true);
        setRetryEnrichmentJobId(caught.jobId || matchResult?.jobId || null);
      } else if (caught.stage === "individual_interpretation") {
        setError(t.individualInterpretationFailed);
      } else if (caught.stage === "interpretation_normalization") {
        setError(t.interpretationNormalizationFailed);
      } else if (caught.stage === "global_interpretation") {
        setError(t.globalInterpretationFailed);
      } else if (caught.stage === "final_report") {
        setError(t.finalReportFailed);
      } else {
        setError(caught.message || String(caught));
      }
      setMessageKey("processFailed");
      setCustomMessage("");
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <img className="fon-logo" src={forceLogo} alt="Force of Nature" />
        <div className="topbar-actions">
          <button className="secondary-button small" type="button" onClick={() => setCanonOpen(true)}>
            <FileSpreadsheet size={16} />
            {t.changeCanon}
          </button>
          <label className="language-control">
            <Globe2 size={17} />
            <span>{t.languageLabel}</span>
            <select value={language} onChange={(event) => setLanguage(event.target.value)}>
              <option value="es">{t.langEs}</option>
              <option value="en">{t.langEn}</option>
            </select>
          </label>
        </div>
      </header>

      <section className="intro">
        <div className="brand-mark">
          <ShieldCheck size={24} />
        </div>
        <div>
          <p className="eyebrow">{t.eyebrow}</p>
          <h1>{t.title}</h1>
          <p className="lede">{t.lede}</p>
        </div>
      </section>

      <PipelineStepper phase={phase} t={t} />

      <section
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          pickFile(event.dataTransfer.files?.[0]);
        }}
      >
        <input
          ref={fileInputRef}
          className="file-input"
          type="file"
          accept=".vcf,.gz,.vcf.gz"
          onChange={(event) => pickFile(event.target.files?.[0])}
        />
        <FileUp size={34} />
        <h2>{file ? file.name : t.dropEmpty}</h2>
        <p>{file ? formatBytes(file.size, locale) : t.dropHelp}</p>
        <button className="secondary-button" type="button" onClick={() => fileInputRef.current?.click()}>
          {t.selectFile}
        </button>
      </section>

      <section className="action-panel">
        <div className="status-line">
          {isBusyPhase(phase) ? (
            <Loader2 className="spin" size={20} />
          ) : (
            <ShieldCheck size={20} />
          )}
          <span>{statusMessage}</span>
        </div>

        <ModeSelector mode={analysisMode} setMode={setAnalysisMode} t={t} />
        <label className="parser-control">
          <span>{t.vcfAssemblyLabel}</span>
          <select value={vcfAssembly} onChange={(event) => setVcfAssembly(event.target.value)}>
            <option value="auto">{t.vcfAssemblyAuto}</option>
            <option value="GRCh38">GRCh38</option>
            <option value="GRCh37">GRCh37</option>
          </select>
          <small>{t.vcfAssemblyHelp}</small>
        </label>
        {analysisMode === "qa" && (
          <>
            <label className="parser-control">
              <span>{t.parserLabel}</span>
              <select value={vcfParser} onChange={(event) => setVcfParser(event.target.value)}>
                <option value="streaming">{t.parserStreaming}</option>
                <option value="pysam">{t.parserPysam}</option>
              </select>
              <small>{t.parserHelp}</small>
            </label>
            <section className="llm2-options" aria-label={t.llm2OptionsTitle}>
              <div className="mode-heading">
                <BarChart3 size={20} />
                <span>{t.llm2OptionsTitle} (legacy)</span>
              </div>
              <div className="llm2-grid">
                <label>
                  <span>{t.llm2LanguageLabel}</span>
                  <select value={qaLanguageMode} onChange={(event) => setQaLanguageMode(event.target.value)}>
                    <option value="es">{t.langEs}</option>
                    <option value="en">{t.langEn}</option>
                    <option value="both">ES + EN</option>
                  </select>
                </label>
                <label>
                  <span>{t.llm2AudienceLabel}</span>
                  <select value={qaAudienceMode} onChange={(event) => setQaAudienceMode(event.target.value)}>
                    <option value="all">{t.audienceAll}</option>
                    <option value="technical">{t.audienceTechnical}</option>
                    <option value="health_professional">{t.audienceProfessional}</option>
                    <option value="family">{t.audienceFamily}</option>
                  </select>
                </label>
                <label>
                  <span>{t.llm2ModelLabel}</span>
                  <select value={qaLlm2Model} onChange={(event) => setQaLlm2Model(event.target.value)}>
                    {QA_LLM2_MODELS.map((model) => (
                      <option value={model} key={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </section>
          </>
        )}
        <TurnstileBox
          siteKey={TURNSTILE_SITE_KEY}
          language={language}
          onToken={setTurnstileToken}
          resetKey={turnstileResetKey}
          t={t}
        />

        <label className="variant-control">
          <span>{t.variantLimit}</span>
          <input
            type="number"
            min="1"
            max="100"
            value={maxVariants}
            onChange={(event) => setMaxVariants(event.target.value)}
            onBlur={() => setMaxVariants(clampVariantCount(maxVariants))}
          />
        </label>

        <ProgressBar
          label={t.uploadProgress}
          value={uploadProgress}
          tone="green"
          onPlay={analysisMode === "qa" ? runQaUpload : null}
          playLabel={`${t.playStage}: ${t.uploadProgress}`}
          playDisabled={!file || isBusyPhase(phase)}
        />
        <ProgressBar
          label={t.validationProgress}
          value={validationProgress}
          tone="blue"
          onPlay={analysisMode === "qa" ? runQaValidation : null}
          playLabel={`${t.playStage}: ${t.validationProgress}`}
          playDisabled={!file || isBusyPhase(phase)}
        />
        {isGeneModuleV2 && (
          <ProgressBar
            label={t.normalizationProgress}
            value={normalizationProgress}
            detail={stageProgressDetails.normalizing || ""}
            tone="blue"
            downloadLabel={t.normalizedVariantsDownload}
            onDownload={matchResult?.jobId ? () => downloadMatchArtifact("normalization") : null}
            downloadReady={matchArtifactsReady.normalization}
          />
        )}
        <ProgressBar
          label={t.matchProgress}
          value={matchProgress}
          tone="blue"
          downloadLabel={t.matchDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("matches") : null}
          downloadReady={matchArtifactsReady.matches}
          onPlay={analysisMode === "qa" ? runQaMatch : null}
          playLabel={`${t.playStage}: ${t.matchProgress}`}
          playDisabled={!uploadRecord || !result || result.status === "invalid" || isBusyPhase(phase)}
        />
        <ProgressBar
          label={t.preparationProgress}
          value={preparationProgress}
          detail={stageProgressDetails.preparing || ""}
          tone="blue"
          downloadLabel={t.matchPreparationAuditDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("preparation") : null}
          downloadReady={matchArtifactsReady.preparation}
        />
        {!isGeneModuleV2 && (
          <ProgressBar
            label={legacyLabel(t.normalizationProgress)}
            value={normalizationProgress}
            detail={stageProgressDetails.normalizing || ""}
            tone="blue"
            downloadLabel={t.normalizedVariantsDownload}
            onDownload={matchResult?.jobId ? () => downloadMatchArtifact("normalization") : null}
            downloadReady={matchArtifactsReady.normalization}
          />
        )}
        {(!matchResult || isGeneModuleV2) && (
          <ProgressBar
            label={t.aiTriageProgress}
            value={aiTriageProgress}
            detail={stageProgressDetails.triaging || ""}
            tone="blue"
            downloadLabel={t.aiTriageDownload}
            onDownload={matchResult?.jobId ? () => downloadMatchArtifact("aiTriage") : null}
            downloadReady={matchArtifactsReady.aiTriage}
          />
        )}
        {isGeneModuleV2 ? (
          <>
            <ProgressBar
              label={t.enrichmentVepBaseProgress}
              value={enrichmentVepBaseProgress}
              detail={stageProgressDetails.enrichment_vep || ""}
              tone="blue"
              downloadLabel={t.enrichmentVepBaseDownload}
              onDownload={matchResult?.jobId ? () => downloadMatchArtifact("enrichmentVepBase") : null}
              downloadReady={matchArtifactsReady.enrichmentVepBase}
            />
            <ProgressBar
              label={t.enrichmentCompleteProgress}
              value={enrichmentCompleteProgress}
              detail={stageProgressDetails.enrichment_complete || ""}
              tone="blue"
              downloadLabel={t.enrichmentCompleteDownload}
              onDownload={matchResult?.jobId ? () => downloadMatchArtifact("enrichmentComplete") : null}
              downloadReady={matchArtifactsReady.enrichmentComplete}
            />
            <ProgressBar
              label={t.enrichmentVepOnlyProgress}
              value={enrichmentVepOnlyProgress}
              detail={stageProgressDetails.enrichment_vep_only || ""}
              tone="blue"
              downloadLabel={t.enrichmentVepOnlyDownload}
              onDownload={matchResult?.jobId ? () => downloadMatchArtifact("enrichmentVepOnly") : null}
              downloadReady={matchArtifactsReady.enrichmentVepOnly}
            />
          </>
        ) : (
          <ProgressBar
            label={t.enrichmentProgress}
            value={enrichmentProgress}
            detail={stageProgressDetails.enriching || ""}
            tone="blue"
            downloadLabel={t.enrichmentDownload}
            onDownload={matchResult?.jobId ? () => downloadMatchArtifact("enrichment") : null}
            downloadReady={matchArtifactsReady.enrichmentInterpretive}
            onPlay={analysisMode === "qa" ? retryEnrichment : null}
            playLabel={`${t.playStage}: ${t.enrichmentProgress}`}
            playDisabled={!uploadRecord || !result || result.status === "invalid" || isBusyPhase(phase)}
          />
        )}
        {(!matchResult || isGeneModuleV2) && (
          <ProgressBar
            label={t.enrichmentQualityProgress}
            value={enrichmentQualityProgress}
            detail={stageProgressDetails.enrichment_quality_gate || ""}
            tone="blue"
            downloadLabel={t.enrichmentQualityDownload}
            onDownload={matchResult?.jobId ? () => downloadMatchArtifact("enrichmentQuality") : null}
            downloadReady={matchArtifactsReady.enrichmentQuality}
          />
        )}
        {isGeneModuleV2 && v2DownstreamBlocked && (
          <p className="warning-message">
            {matchResult?.metadata?.downstream_message || "V2 grouping and LLM1 are blocked until the enrichment quality gate and explicit enablement pass."}
          </p>
        )}
        {isGeneModuleV2 && !v2DownstreamBlocked && (
          <>
            <ProgressBar
              label={t.groupingPreparationProgress}
              value={groupingPreparationProgress}
              detail={stageProgressDetails.grouping_preparation || ""}
              tone="blue"
              downloadLabel={t.groupingPayloadsDownload}
              onDownload={matchResult?.jobId ? () => downloadMatchArtifact("groupedPayloads") : null}
              downloadReady={matchArtifactsReady.groupedPayloads}
            />
            <ProgressBar
              label={t.groupedInterpretationProgress}
              value={groupedInterpretationProgress}
              detail={groupedInterpretationDetail || stageProgressDetails.grouped_individual_interpretation || ""}
              tone="blue"
              downloadLabel={t.groupedInterpretationDownload}
              onDownload={matchResult?.jobId ? () => downloadMatchArtifact("groupedInterpretation") : null}
              downloadReady={matchArtifactsReady.groupedInterpretation}
            />
          </>
        )}
        {!isGeneModuleV2 && (
          <>
        <ProgressBar
          label={legacyLabel(t.individualInterpretationProgress)}
          value={individualInterpretationProgress}
          detail={individualInterpretationDetail}
          tone="blue"
          downloadLabel={t.individualInterpretationDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("individualInterpretation") : null}
          downloadReady={matchArtifactsReady.individualInterpretation}
          onPlay={analysisMode === "qa" ? runQaIndividualInterpretation : null}
          playLabel={`${t.playStage}: ${t.individualInterpretationProgress}`}
          playDisabled={
            !matchResult?.jobId ||
            !matchArtifactsReady.enrichmentPlus ||
            isBusyPhase(phase)
          }
        />
        <ProgressBar
          label={legacyLabel(t.interpretationNormalizationProgress)}
          value={interpretationNormalizationProgress}
          tone="blue"
          downloadLabel={t.interpretationNormalizationDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("interpretationNormalization") : null}
          downloadReady={matchArtifactsReady.interpretationNormalization}
          onPlay={analysisMode === "qa" ? runQaInterpretationNormalization : null}
          playLabel={`${t.playStage}: ${t.interpretationNormalizationProgress}`}
          playDisabled={
            !matchResult?.jobId ||
            !matchArtifactsReady.individualInterpretation ||
            isBusyPhase(phase)
          }
        />
        <ProgressBar
          label={legacyLabel(t.globalInterpretationProgress)}
          value={globalInterpretationProgress}
          tone="blue"
          downloadLabel={t.globalInterpretationDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("globalInterpretation") : null}
          downloadReady={matchArtifactsReady.globalInterpretation}
          onPlay={analysisMode === "qa" ? runQaGlobalInterpretation : null}
          playLabel={`${t.playStage}: ${t.globalInterpretationProgress}`}
          playDisabled={
            !matchResult?.jobId ||
            !matchArtifactsReady.interpretationNormalization ||
            isBusyPhase(phase)
          }
        />
        <ProgressBar
          label={legacyLabel(t.finalReportProgress)}
          value={finalReportProgress}
          tone="blue"
          downloadLabel={t.finalReportDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("finalReport") : null}
          downloadReady={matchArtifactsReady.finalReport}
          onPlay={analysisMode === "qa" ? runQaFinalReport : null}
          playLabel={`${t.playStage}: ${t.finalReportProgress}`}
          playDisabled={
            !matchResult?.jobId ||
            !matchArtifactsReady.globalInterpretation ||
            isBusyPhase(phase)
          }
        />
          </>
        )}
        {analysisMode === "qa" && (finalReportDownloads.es || finalReportDownloads.en) && (
          <div className="report-download-row">
            {finalReportDownloads.es && (
              <button className="secondary-button small" type="button" onClick={() => downloadStoredReport(finalReportDownloads.es)}>
                <Download size={15} />
                {t.finalReportDownloadEs}
              </button>
            )}
            {finalReportDownloads.en && (
              <button className="secondary-button small" type="button" onClick={() => downloadStoredReport(finalReportDownloads.en)}>
                <Download size={15} />
                {t.finalReportDownloadEn}
              </button>
            )}
          </div>
        )}
        {error && <p className="error-message">{error}</p>}
        <button className="primary-button" type="button" disabled={!canSend} onClick={submit}>
          <Send size={18} />
          {t.submit}
        </button>
      </section>

      <ResultPanel result={result} analysisMode={analysisMode} locale={locale} t={t} />
      <MatchResultPanel result={matchResult} locale={locale} t={t} />
      <DuplicateUploadModal
        candidate={duplicateCandidate}
        locale={locale}
        onCancel={() => setDuplicateCandidate(null)}
        onUploadAgain={() => {
          const candidate = duplicateCandidate;
          setDuplicateCandidate(null);
          submit({ skipDuplicateCheck: true, reuseUpload: null, ignoredCandidate: candidate });
        }}
        onUseExisting={() => {
          const candidate = duplicateCandidate;
          setDuplicateCandidate(null);
          submit({ skipDuplicateCheck: true, reuseUpload: candidate });
        }}
        t={t}
      />
      <ErrorDialog
        message={errorDialog}
        onClose={() => {
          setErrorDialog(null);
          setRetryEnrichmentJobId(null);
        }}
        onRetry={retryEnrichmentJobId ? retryEnrichment : null}
        t={t}
      />
      <CanonModal open={canonOpen} onClose={() => setCanonOpen(false)} language={language} locale={locale} t={t} />
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
