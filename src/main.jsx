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
  "evidence_refinement",
  "evidence_refinement_quality_gate",
  "grouping_preparation",
  "grouped_individual_interpretation",
  "grouped_prototype",
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
    "evidence_refinement",
    "grouped_individual_interpretation",
    "grouped_prototype",
    "individual_interpretation",
    "interpretation_normalization",
    "global_interpretation",
    "final_report",
  ].includes(stage);
}

function isVariantEnrichmentStage(stage) {
  return [
    "enriching",
    "enrichment_vep",
    "enrichment_complete",
    "enrichment_vep_only",
    "enrichment_quality_gate",
    "evidence_refinement",
    "evidence_refinement_quality_gate",
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
    quickModeDetail: "Enriquece VEP y rsIDs exactos; omite rescate costoso por coordenadas.",
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
    evidenceRefinementProgress: "Refinamiento de evidencia",
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
    enrichmentVepOnlyProgress: "Identidad no resuelta / seguimiento por coordenadas",
    enrichmentQuality: "Validando cobertura y calidad del enrichment...",
    evidenceRefining: "Curando ClinVar, contexto PharmGKB, clusters GWAS y publicaciones seleccionadas...",
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
    evidenceRefinementComplete: "Refinamiento de evidencia finalizado.",
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
    evidenceRefinementTitle: "Contrato curado multifuente",
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
    enrichmentVepOnlyVariants: "Identidad no resuelta / seguimiento",
    enrichmentCoordinateResolved: "Resueltas por coordenada",
    enrichmentTechnicalGate: "Technical gate",
    enrichmentEvidenceReadiness: "Evidence readiness",
    enrichmentNotQueried: "No consultadas",
    enrichmentResolutionAmbiguous: "Resoluciones ambiguas",
    enrichmentResolutionAlleleMismatch: "Alelo no coincidente",
    enrichmentQualityDecision: "Decision QA",
    enrichmentInputRows: "Filas fuente",
    enrichmentPlusRows: "Filas Enrichment Plus",
    enrichmentUniqueRsids: "rsIDs unicos",
    enrichmentSources: "Fuentes externas",
    enrichmentCacheHits: "Cache hits",
    enrichmentSourceErrors: "Errores de fuentes",
    curatedRegistryVariants: "Variantes normalizadas conservadas",
    curatedPhysicalVariants: "Variantes fisicas curadas",
    curatedDeepVariants: "Curacion profunda",
    curatedBenignVariants: "Benignas documentadas",
    curatedAnnotationAbsent: "Sin anotacion externa",
    curatedUnresolvedVariants: "Identidad no resuelta",
    curatedFocusVariants: "Elegibles como foco",
    curatedRetryRows: "Errores reintentables",
    curatedPublications: "Publicaciones unicas",
    curatedGwasRaw: "Asociaciones GWAS raw",
    curatedGwasClustersHigh: "Clusters GWAS de alta confianza",
    curatedGwasClustersModerate: "Clusters GWAS contextuales",
    curatedGwasMetadataPending: "Metadata GWAS diferida",
    groupingPreparationGroups: "Grupos gene+modulo",
    groupingPreparationVariants: "Variantes fuente",
    groupingPreparationAverageSize: "Tamano promedio",
    groupingPreparationLargeGroups: "Grupos >25 variantes",
    groupingTranscriptFocus: "Variantes foco transcript-aware",
    groupingFocusReclassified: "Filas retiradas del foco v5",
    groupingApprovedMechanisms: "Mecanismos aprobados",
    groupingApprovedGwas: "Grupos con GWAS aprobado",
    groupingPayloadReady: "Grupos listos para payload",
    groupingSourceFailureGroups: "Grupos con errores de fuente",
    llm1CanaryTitle: "Canary LLM1: estado por grupo",
    llm1CanaryCategory: "Categoria",
    llm1CanaryFocus: "Foco concordante",
    llm1CanaryAltFrequency: "Frecuencia ALT",
    llm1CanaryMechanism: "Mecanismo",
    llm1CanaryGwas: "Relevancia GWAS",
    llm1CanaryErrors: "Errores fuente",
    llm1CanaryTokens: "Tokens",
    llm1CanaryBlockers: "Bloqueos",
    groupedInterpretationTitle: "Interpretacion individual agrupada",
    llm1CardsTitle: "Tarjetas LLM1 v7",
    groupedPrototypeTitle: "Prototipo agrupado HEAL",
    groupedPrototypeNotice: "Prototipo de desarrollo: cobertura científica firmada de {covered} sobre {canonical} grupos canónicos. La validación formal con un nuevo holdout permanece pendiente.",
    groupedPrototypeCovered: "Grupos cubiertos",
    groupedPrototypeObserved: "Cubiertos con variante observada",
    groupedPrototypeNoObserved: "Cubiertos sin variante observada",
    groupedPrototypeValid: "Tarjetas válidas",
    groupedPrototypeQuarantined: "Tarjetas en cuarentena",
    groupedPrototypeNotCovered: "Grupos no cubiertos",
    groupedPrototypeCost: "Costo interno estimado",
    groupedPrototypeStatus: "Estado del prototipo",
    groupedPrototypeProgress: "Prototipo agrupado end-to-end",
    groupedPrototypeDocx: "Descargar reporte DOCX",
    groupedPrototypePdf: "Descargar reporte PDF",
    groupedPrototypeCardsDownload: "Descargar tarjetas CSV",
    groupedPrototypeCoverageDownload: "Descargar cobertura CSV",
    llm1CardsCoverage: "Cobertura Tier 1",
    llm1CardsActive: "Interpretaciones activas",
    llm1CardsExperimental: "Anexo experimental",
    llm1CardsExcluded: "Grupos no interpretados",
    llm1CardsLoading: "Cargando tarjetas auditables...",
    llm1CardsUnavailable: "Las tarjetas LLM1 todavia no estan disponibles.",
    llm1CardsInference: "Modo de inferencia",
    llm1CardsConfidence: "Confianza",
    llm1CardsPriority: "Prioridad de revision",
    llm1CardsCompleteness: "Completitud del VCF",
    llm1CardsEvidence: "Evidencia y trazabilidad",
    llm1CardsLimitations: "Limitaciones",
    llm1CardsDisclaimer: "Inferencia inicial generada por una LLM; no es un diagnostico ni una indicacion terapeutica.",
    llm1CardsExperimentalNotice: "Experimental: este tier no esta activado y no cuenta como cobertura Tier 1 ni alimenta LLM2.",
    llm1CardsNotObserved: "No observado; la callability es desconocida y no se presume homocigosis de referencia.",
    llm1CardsShowDetails: "Ver evidencia, gates y procedencia",
    llm1ReviewApprove: "Aprobar revision interna completa",
    llm1ReviewReject: "Rechazar revision interna",
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
    enrichmentVepOnlyDownload: "Descargar identidades no resueltas",
    enrichmentPhysicalMatrixDownload: "Descargar matriz fisica por variante",
    enrichmentResolutionAuditDownload: "Descargar auditoria de resolucion",
    enrichmentPhysicalEvidenceAuditDownload: "Descargar auditoria fisica compacta",
    enrichmentModuleProjectionDownload: "Descargar proyeccion gen-modulo",
    enrichmentRetryQueueDownload: "Descargar cola de reintentos",
    enrichmentIdentitySummaryDownload: "Descargar resumen de identidad",
    enrichmentPerformanceDownload: "Descargar metricas de rendimiento",
    enrichmentEvidenceAuditDownload: "Descargar evidencia enrichment",
    curatedPhysicalMatrixDownload: "Descargar matriz fisica curada",
    curatedPhysicalRegistryDownload: "Descargar registro fisico completo",
    curatedModuleProjectionDownload: "Descargar proyeccion gen-modulo curada",
    canonicalStatusDownload: "Descargar estado completo del canon",
    clinvarAggregateDownload: "Descargar resumen ClinVar",
    clinvarAssertionsDownload: "Descargar assertions ClinVar",
    clinpgxClinicalDownload: "Descargar contexto clinico PharmGKB base",
    clinpgxVariantDownload: "Descargar contexto de variantes PharmGKB base",
    gwasAssociationsDownload: "Descargar asociaciones GWAS",
    gwasVariantTraitsDownload: "Descargar resumen GWAS variante-trait",
    gwasGeneModuleDownload: "Descargar resumen GWAS gen-modulo-trait",
    gwasClustersDownload: "Descargar clusters de evidencia GWAS",
    gwasRelevanceTemplateDownload: "Descargar plantilla de relevancia GWAS",
    gwasMetadataRetryDownload: "Descargar metadata GWAS pendiente",
    publicationEvidenceDownload: "Descargar evidencia bibliografica",
    evidenceRefinementRawDownload: "Descargar evidencia publica raw",
    evidenceRefinementRetryDownload: "Descargar retries de curacion",
    evidenceRefinementSummaryDownload: "Descargar resumen de curacion",
    groupingPayloadsDownload: "Descargar payloads agrupados",
    groupingPayloadsV4Download: "Descargar payloads agrupados v4",
    groupingPayloadsV5Download: "Descargar payloads acotados v5",
    groupingPayloadsV6Download: "Descargar payloads transcript-aware v6",
    groupingPayloadsV7Download: "Descargar payloads de produccion v7",
    groupPreflightV7Download: "Descargar preflight de 180 grupos",
    groupPayloadV7SummaryDownload: "Descargar resumen LLM1 v7",
    groupPayloadSchemaV7Download: "Descargar schema del payload v7",
    llm1CardsDownload: "Descargar tarjetas LLM1",
    targetGeneAuditDownload: "Descargar auditoria gen-transcrito",
    alleleFrequencyAuditDownload: "Descargar auditoria de frecuencia ALT",
    clinvarConflictAuditDownload: "Descargar conflictos ClinVar por condicion",
    groupTokenBudgetV6Download: "Descargar presupuesto de tokens v6",
    groupPayloadV6SummaryDownload: "Descargar resumen LLM1 v6",
    groupPayloadV6ErrorsDownload: "Descargar errores LLM1 v6",
    llm1PilotCandidateManifestV3Download: "Descargar manifest canary v3",
    groupPayloadSchemaV6Download: "Descargar schema del payload v6",
    persistentMechanismRegistryDownload: "Descargar mecanismos de curacion interna",
    persistentGwasRegistryDownload: "Descargar relevancia GWAS de curacion interna",
    curationMechanismUpload: "Cargar mecanismos aprobados internamente",
    curationGwasUpload: "Cargar relevancia GWAS aprobada internamente",
    curationManifestUpload: "Cargar aprobacion del canary",
    llm1PreflightV7Start: "Regenerar preflight LLM1 v7",
    groupEvidencePacketsDownload: "Descargar ledger por grupo",
    groupEvidenceDigestsDownload: "Descargar digests de evidencia",
    groupEvidenceDigestErrorsDownload: "Descargar errores de digest",
    groupTokenBudgetDownload: "Descargar auditoria de tokens",
    groupEvidenceCoverageDownload: "Descargar auditoria de cobertura",
    groupCompressionErrorsDownload: "Descargar errores de compresion",
    groupCompressionSummaryDownload: "Descargar resumen de compresion",
    llm1PilotCandidateManifestV2Download: "Descargar candidatos del piloto v2",
    groupPayloadSchemaV5Download: "Descargar schema del payload v5",
    evidenceDigestStart: "Generar digest de evidencia publica",
    groupingMaxTokens: "Maximo de tokens por grupo",
    groupingWithinLimit: "Grupos dentro del limite",
    groupingCompressionReview: "Grupos que requieren revision",
    groupingCoverageRecords: "Registros de evidencia auditados",
    groupingCoverageReconciled: "Cobertura reconciliada",
    groupingPilotCandidates: "Candidatos de piloto",
    mechanismRegistryDownload: "Descargar registro de mecanismos",
    llm1PilotManifestDownload: "Descargar manifest del piloto LLM1",
    llm1PilotStart: "Ejecutar piloto LLM1 aprobado",
    groupingVariantDetailDownload: "Descargar detalle por variante",
    groupingSummaryDownload: "Descargar resumen de grupos",
    groupedInterpretationDownload: "Descargar interpretacion agrupada",
    groupedInterpretationSummaryDownload: "Descargar resumen interpretacion agrupada",
    groupedInterpretationRawDownload: "Descargar respuestas raw del canary",
    llm1PilotApprovedPayloadsDownload: "Descargar payloads aprobados del canary",
    groupedInterpretationCallAuditDownload: "Descargar auditoria de llamadas LLM1",
    llm1PilotPromptDownload: "Descargar prompt LLM1 usado",
    llm1PilotSchemaDownload: "Descargar schema de respuesta LLM1",
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
    executionLogTitle: "Consola de ejecucion",
    executionLogEmpty: "Los eventos internos apareceran aca cuando comience el procesamiento.",
    executionLogDownload: "Descargar logs",
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
    quickModeDetail: "Enriches VEP and exact rsIDs; skips costly coordinate rescue.",
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
    evidenceRefinementProgress: "Evidence refinement",
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
    enrichmentVepOnlyProgress: "Unresolved identity / coordinate follow-up",
    enrichmentQuality: "Checking enrichment coverage and quality...",
    evidenceRefining: "Curating ClinVar, PharmGKB context, GWAS clusters, and selected publications...",
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
    evidenceRefinementComplete: "Evidence refinement finished.",
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
    evidenceRefinementTitle: "Curated multisource contract",
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
    enrichmentVepOnlyVariants: "Unresolved identity / follow-up",
    enrichmentCoordinateResolved: "Coordinate-resolved",
    enrichmentTechnicalGate: "Technical gate",
    enrichmentEvidenceReadiness: "Evidence readiness",
    enrichmentNotQueried: "Not queried",
    enrichmentResolutionAmbiguous: "Ambiguous resolutions",
    enrichmentResolutionAlleleMismatch: "Allele mismatches",
    enrichmentQualityDecision: "QA decision",
    enrichmentInputRows: "Source rows",
    enrichmentPlusRows: "Enrichment Plus rows",
    enrichmentUniqueRsids: "Unique rsIDs",
    enrichmentSources: "External sources",
    enrichmentCacheHits: "Cache hits",
    enrichmentSourceErrors: "Source errors",
    curatedRegistryVariants: "Retained normalized variants",
    curatedPhysicalVariants: "Curated physical variants",
    curatedDeepVariants: "Deeply curated",
    curatedBenignVariants: "Documented benign variants",
    curatedAnnotationAbsent: "No external annotation",
    curatedUnresolvedVariants: "Unresolved identity",
    curatedFocusVariants: "Focus eligible",
    curatedRetryRows: "Retryable errors",
    curatedPublications: "Unique publications",
    curatedGwasRaw: "Raw GWAS associations",
    curatedGwasClustersHigh: "High-confidence GWAS clusters",
    curatedGwasClustersModerate: "Contextual GWAS clusters",
    curatedGwasMetadataPending: "Deferred GWAS metadata",
    groupingPreparationGroups: "Gene+module groups",
    groupingPreparationVariants: "Source variants",
    groupingPreparationAverageSize: "Average size",
    groupingPreparationLargeGroups: "Groups >25 variants",
    groupingTranscriptFocus: "Transcript-aware focus variants",
    groupingFocusReclassified: "Rows removed from v5 focus",
    groupingApprovedMechanisms: "Approved mechanisms",
    groupingApprovedGwas: "Groups with approved GWAS",
    groupingPayloadReady: "Payload-ready groups",
    groupingSourceFailureGroups: "Groups with source errors",
    llm1CanaryTitle: "LLM1 canary: group status",
    llm1CanaryCategory: "Category",
    llm1CanaryFocus: "Concordant focus",
    llm1CanaryAltFrequency: "ALT frequency",
    llm1CanaryMechanism: "Mechanism",
    llm1CanaryGwas: "GWAS relevance",
    llm1CanaryErrors: "Source errors",
    llm1CanaryTokens: "Tokens",
    llm1CanaryBlockers: "Blockers",
    groupedInterpretationTitle: "Grouped individual interpretation",
    llm1CardsTitle: "LLM1 v7 cards",
    groupedPrototypeTitle: "HEAL grouped prototype",
    groupedPrototypeNotice: "Development prototype: signed scientific coverage includes {covered} of {canonical} canonical groups. Formal validation with a new holdout remains pending.",
    groupedPrototypeCovered: "Covered groups",
    groupedPrototypeObserved: "Covered with observed variant",
    groupedPrototypeNoObserved: "Covered without observed variant",
    groupedPrototypeValid: "Valid cards",
    groupedPrototypeQuarantined: "Quarantined cards",
    groupedPrototypeNotCovered: "Uncovered groups",
    groupedPrototypeCost: "Estimated internal cost",
    groupedPrototypeStatus: "Prototype status",
    groupedPrototypeProgress: "End-to-end grouped prototype",
    groupedPrototypeDocx: "Download DOCX report",
    groupedPrototypePdf: "Download PDF report",
    groupedPrototypeCardsDownload: "Download cards CSV",
    groupedPrototypeCoverageDownload: "Download coverage CSV",
    llm1CardsCoverage: "Tier 1 coverage",
    llm1CardsActive: "Active interpretations",
    llm1CardsExperimental: "Experimental appendix",
    llm1CardsExcluded: "Groups not interpreted",
    llm1CardsLoading: "Loading auditable cards...",
    llm1CardsUnavailable: "LLM1 cards are not available yet.",
    llm1CardsInference: "Inference mode",
    llm1CardsConfidence: "Confidence",
    llm1CardsPriority: "Review priority",
    llm1CardsCompleteness: "VCF completeness",
    llm1CardsEvidence: "Evidence and traceability",
    llm1CardsLimitations: "Limitations",
    llm1CardsDisclaimer: "Initial inference generated by an LLM; it is not a diagnosis or a treatment instruction.",
    llm1CardsExperimentalNotice: "Experimental: this tier is inactive and does not count toward Tier 1 coverage or feed LLM2.",
    llm1CardsNotObserved: "Not observed; callability is unknown and homozygous-reference status is not assumed.",
    llm1CardsShowDetails: "View evidence, gates, and provenance",
    llm1ReviewApprove: "Approve complete internal review",
    llm1ReviewReject: "Reject internal review",
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
    enrichmentVepOnlyDownload: "Download unresolved identity audit",
    enrichmentPhysicalMatrixDownload: "Download physical variant matrix",
    enrichmentResolutionAuditDownload: "Download resolution audit",
    enrichmentPhysicalEvidenceAuditDownload: "Download compact physical audit",
    enrichmentModuleProjectionDownload: "Download gene-module projection",
    enrichmentRetryQueueDownload: "Download retry queue",
    enrichmentIdentitySummaryDownload: "Download identity summary",
    enrichmentPerformanceDownload: "Download performance metrics",
    enrichmentEvidenceAuditDownload: "Download enrichment evidence audit",
    curatedPhysicalMatrixDownload: "Download curated physical matrix",
    curatedPhysicalRegistryDownload: "Download complete physical registry",
    curatedModuleProjectionDownload: "Download curated gene-module projection",
    canonicalStatusDownload: "Download complete canon status",
    clinvarAggregateDownload: "Download ClinVar aggregate",
    clinvarAssertionsDownload: "Download ClinVar assertions",
    clinpgxClinicalDownload: "Download base PharmGKB clinical context",
    clinpgxVariantDownload: "Download base PharmGKB variant context",
    gwasAssociationsDownload: "Download GWAS associations",
    gwasVariantTraitsDownload: "Download GWAS variant-trait summary",
    gwasGeneModuleDownload: "Download GWAS gene-module-trait summary",
    gwasClustersDownload: "Download GWAS evidence clusters",
    gwasRelevanceTemplateDownload: "Download GWAS relevance review template",
    gwasMetadataRetryDownload: "Download pending GWAS metadata",
    publicationEvidenceDownload: "Download publication evidence",
    evidenceRefinementRawDownload: "Download raw public evidence",
    evidenceRefinementRetryDownload: "Download curation retry queue",
    evidenceRefinementSummaryDownload: "Download curation summary",
    groupingPayloadsDownload: "Download grouped payloads",
    groupingPayloadsV4Download: "Download grouped payloads v4",
    groupingPayloadsV5Download: "Download bounded v5 payloads",
    groupingPayloadsV6Download: "Download transcript-aware v6 payloads",
    groupingPayloadsV7Download: "Download production-candidate v7 payloads",
    groupPreflightV7Download: "Download 180-group preflight",
    groupPayloadV7SummaryDownload: "Download LLM1 v7 summary",
    groupPayloadSchemaV7Download: "Download v7 payload schema",
    llm1CardsDownload: "Download LLM1 cards",
    targetGeneAuditDownload: "Download gene-transcript audit",
    alleleFrequencyAuditDownload: "Download ALT-frequency audit",
    clinvarConflictAuditDownload: "Download condition-aware ClinVar conflicts",
    groupTokenBudgetV6Download: "Download v6 token budget",
    groupPayloadV6SummaryDownload: "Download LLM1 v6 summary",
    groupPayloadV6ErrorsDownload: "Download LLM1 v6 errors",
    llm1PilotCandidateManifestV3Download: "Download canary manifest v3",
    groupPayloadSchemaV6Download: "Download v6 payload schema",
    persistentMechanismRegistryDownload: "Download internal-curation mechanism registry",
    persistentGwasRegistryDownload: "Download internal-curation GWAS relevance registry",
    curationMechanismUpload: "Upload internally approved mechanisms",
    curationGwasUpload: "Upload internally approved GWAS relevance",
    curationManifestUpload: "Upload canary approval",
    llm1PreflightV7Start: "Regenerate LLM1 v7 preflight",
    groupEvidencePacketsDownload: "Download group evidence ledger",
    groupEvidenceDigestsDownload: "Download evidence digests",
    groupEvidenceDigestErrorsDownload: "Download digest errors",
    groupTokenBudgetDownload: "Download token budget audit",
    groupEvidenceCoverageDownload: "Download coverage audit",
    groupCompressionErrorsDownload: "Download compression errors",
    groupCompressionSummaryDownload: "Download compression summary",
    llm1PilotCandidateManifestV2Download: "Download pilot candidate manifest v2",
    groupPayloadSchemaV5Download: "Download v5 payload schema",
    evidenceDigestStart: "Generate public evidence digest",
    groupingMaxTokens: "Maximum tokens per group",
    groupingWithinLimit: "Groups within hard limit",
    groupingCompressionReview: "Groups requiring review",
    groupingCoverageRecords: "Evidence records audited",
    groupingCoverageReconciled: "Coverage reconciled",
    groupingPilotCandidates: "Pilot candidates",
    mechanismRegistryDownload: "Download mechanism registry",
    llm1PilotManifestDownload: "Download LLM1 pilot manifest",
    llm1PilotStart: "Run approved LLM1 pilot",
    groupingVariantDetailDownload: "Download grouped variant detail",
    groupingSummaryDownload: "Download grouped summary",
    groupedInterpretationDownload: "Download grouped interpretation CSV",
    groupedInterpretationSummaryDownload: "Download grouped interpretation summary",
    groupedInterpretationRawDownload: "Download raw canary responses",
    llm1PilotApprovedPayloadsDownload: "Download approved canary payloads",
    groupedInterpretationCallAuditDownload: "Download LLM1 call audit",
    llm1PilotPromptDownload: "Download executed LLM1 prompt",
    llm1PilotSchemaDownload: "Download LLM1 response schema",
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
    executionLogTitle: "Execution console",
    executionLogEmpty: "Internal events will appear here when processing starts.",
    executionLogDownload: "Download logs",
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

function ExecutionLogPanel({ jobId, logs, onDownload, t, locale }) {
  return (
    <section className="execution-log-panel" aria-live="polite" aria-label={t.executionLogTitle}>
      <div className="execution-log-heading">
        <div>
          <strong>{t.executionLogTitle}</strong>
          {jobId && <span>{jobId}</span>}
        </div>
        {jobId && onDownload && (
          <button className="secondary-button small" type="button" onClick={onDownload}>
            <Download size={15} />
            {t.executionLogDownload}
          </button>
        )}
      </div>
      <div className="execution-log-body">
        {logs.length === 0 ? (
          <span className="execution-log-empty">{t.executionLogEmpty}</span>
        ) : (
          logs.map((entry, index) => {
            const timestamp = entry.timestamp
              ? new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(entry.timestamp))
              : "--:--:--";
            const detail = entry.stageProgressDetail;
            const counts = detail && detail.total > 0 ? ` (${detail.processed}/${detail.total} ${detail.unit || "items"})` : "";
            const message = entry.message || entry.stderr || entry.event || "event";
            return (
              <div className="execution-log-line" key={`${entry.timestamp || "event"}-${index}`}>
                <time>{timestamp}</time>
                <span className="execution-log-event">{entry.event}</span>
                <span>{message}{counts}</span>
              </div>
            );
          })
        )}
      </div>
    </section>
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

function parseJsonArray(value) {
  if (Array.isArray(value)) return value;
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function Llm1GroupCard({ card, language, t }) {
  const interpretation = card.interpretation || null;
  const suffix = language === "es" ? "es" : "en";
  const oneSentence = interpretation?.[`interpretation_one_sentence_${suffix}`] || "";
  const longText = interpretation?.[`interpretation_long_${suffix}`] || "";
  const technical = interpretation?.[`technical_interpretation_${suffix}`] || "";
  const rationale = interpretation?.[`confidence_rationale_${suffix}`] || "";
  const familyNotes = interpretation?.[`family_notes_${suffix}`] || "";
  const nextStep = interpretation?.[`recommended_next_review_step_${suffix}`] || "";
  const evidenceUsed = parseJsonArray(interpretation?.evidence_used);
  const limitations = parseJsonArray(interpretation?.evidence_limitations);
  const focusVariants = card.focus_variants || [];
  const isUnavailable = ["quarantined", "technical_failure"].includes(card.status);
  const isCoverageOnly = !interpretation;
  return (
    <article className={`llm1-card llm1-card-${card.status || "unknown"}`}>
      <div className="llm1-card-heading">
        <div>
          <strong>{card.gene || card.group_id}</strong>
          <span>{card.module_id} · {card.module_name || "-"}</span>
        </div>
        <span className="llm1-status-badge">{card.status || card.coverage_status}</span>
      </div>
      {card.experimental_canary && <p className="llm1-card-notice">{t.llm1CardsExperimentalNotice}</p>}
      {isUnavailable ? (
        <p className="error">{language === "es" ? "Resultado no disponible; el grupo fue aislado." : "Result unavailable; the group was isolated."}</p>
      ) : isCoverageOnly ? (
        <>
          <p>{card.decision_reason}</p>
          {card.input_completeness?.mode === "observed_variants_only" && card.focus_variant_count === 0 && (
            <p className="llm1-card-notice">{t.llm1CardsNotObserved}</p>
          )}
        </>
      ) : (
        <>
          <p className="llm1-card-summary">{oneSentence}</p>
          <div className="llm1-card-metadata">
            <span><b>{t.llm1CardsInference}:</b> {interpretation.inference_mode}</span>
            <span><b>{t.llm1CardsConfidence}:</b> {interpretation.final_confidence_level}</span>
            <span><b>{t.llm1CardsPriority}:</b> {interpretation.review_priority}</span>
            <span><b>{t.llm1CardsCompleteness}:</b> {card.input_completeness?.mode || interpretation.input_completeness_mode}</span>
          </div>
          {longText && <p>{longText}</p>}
          <p className="llm1-card-disclaimer">{t.llm1CardsDisclaimer}</p>
        </>
      )}
      <details className="llm1-card-details">
        <summary>{t.llm1CardsShowDetails}</summary>
        <div className="llm1-card-detail-grid">
          <div>
            <h5>{t.llm1CardsEvidence}</h5>
            <p>{technical || card.deterministic_summary?.statement || "-"}</p>
            {evidenceUsed.length > 0 && <pre>{JSON.stringify(evidenceUsed, null, 2)}</pre>}
            {focusVariants.length > 0 && <pre>{JSON.stringify(focusVariants, null, 2)}</pre>}
          </div>
          <div>
            <h5>{t.llm1CardsLimitations}</h5>
            {rationale && <p>{rationale}</p>}
            {familyNotes && <p>{familyNotes}</p>}
            {nextStep && <p>{nextStep}</p>}
            {limitations.length > 0 && <ul>{limitations.map((item) => <li key={String(item)}>{String(item)}</li>)}</ul>}
            {card.blocker_codes?.length > 0 && <p><b>Blockers:</b> {card.blocker_codes.join(", ")}</p>}
          </div>
        </div>
        <pre>{JSON.stringify({
          mechanism: card.curated_mechanism,
          conflicts: card.clinical_conflicts,
          gwas: card.gwas_context,
          gates: card.gates,
          provenance: card.provenance,
          allowed_evidence_ids: card.allowed_evidence_ids,
          allowed_variant_refs: card.allowed_variant_refs,
        }, null, 2)}</pre>
      </details>
    </article>
  );
}

function Llm1CardsPanel({ cards, loading, error, language, onLanguageChange, t }) {
  if (loading) return <p>{t.llm1CardsLoading}</p>;
  if (error) return <p className="error">{error}</p>;
  if (!cards?.length) return <p>{t.llm1CardsUnavailable}</p>;
  const active = cards.filter((card) => card.client_visible === true || (card.interpretation && !card.experimental_canary));
  const experimental = cards.filter((card) => card.experimental_canary === true);
  const excluded = cards.filter((card) => !active.includes(card) && !experimental.includes(card));
  const tier1 = cards.filter((card) => card.tier === "T1");
  return (
    <div className="llm1-cards-panel">
      <div className="llm1-cards-toolbar">
        <div>
          <strong>{t.llm1CardsCoverage}</strong>
          <span>{tier1.length} total · {active.length} active · {excluded.length} coverage-only</span>
        </div>
        <div className="llm1-language-switch" role="group" aria-label="LLM1 card language">
          <button type="button" className={language === "es" ? "active" : ""} onClick={() => onLanguageChange("es")}>ES</button>
          <button type="button" className={language === "en" ? "active" : ""} onClick={() => onLanguageChange("en")}>EN</button>
        </div>
      </div>
      {active.length > 0 && <><h4>{t.llm1CardsActive}</h4><div className="llm1-cards-grid">{active.map((card) => <Llm1GroupCard key={card.group_id} card={card} language={language} t={t} />)}</div></>}
      {experimental.length > 0 && <><h4>{t.llm1CardsExperimental}</h4><div className="llm1-cards-grid">{experimental.map((card) => <Llm1GroupCard key={card.group_id} card={card} language={language} t={t} />)}</div></>}
      <details className="llm1-coverage-details">
        <summary>{t.llm1CardsExcluded} ({excluded.length})</summary>
        <div className="llm1-cards-grid compact">{excluded.map((card) => <Llm1GroupCard key={card.group_id} card={card} language={language} t={t} />)}</div>
      </details>
    </div>
  );
}

function MatchResultPanel({ result, locale, t }) {
  const [downloadError, setDownloadError] = useState(null);
  const [llm1Cards, setLlm1Cards] = useState([]);
  const [llm1CardsLoading, setLlm1CardsLoading] = useState(false);
  const [llm1CardsError, setLlm1CardsError] = useState(null);
  const [llm1CardLanguage, setLlm1CardLanguage] = useState(locale?.startsWith("es") ? "es" : "en");
  const prototypeCardsReady = Boolean(result?.artifactsReady?.groupedPrototypeCards);
  const cardsReady = prototypeCardsReady || Boolean(result?.artifactsReady?.groupCardsV7);
  useEffect(() => {
    if (!result?.jobId || !cardsReady) {
      setLlm1Cards([]);
      return;
    }
    let cancelled = false;
    setLlm1CardsLoading(true);
    setLlm1CardsError(null);
    const cardsEndpoint = prototypeCardsReady
      ? `/api/vcf-canon-matches/${result.jobId}/grouped-prototype/cards`
      : `/api/vcf-canon-matches/${result.jobId}/llm1-group-cards`;
    fetch(`${API_BASE}${cardsEndpoint}`, {
      headers: accessHeaders(result.accessToken || getJobAccessToken(result.jobId)),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null);
        if (!response.ok) throw new Error(payload?.error || "Could not load LLM1 cards.");
        return payload;
      })
      .then((payload) => { if (!cancelled) setLlm1Cards(Array.isArray(payload) ? payload : []); })
      .catch((loadError) => { if (!cancelled) setLlm1CardsError(loadError.message || String(loadError)); })
      .finally(() => { if (!cancelled) setLlm1CardsLoading(false); });
    return () => { cancelled = true; };
  }, [result?.jobId, result?.updatedAt, cardsReady, prototypeCardsReady]);
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
  const evidenceRefinement = result.evidenceRefinement || {};
  const evidenceRefinementCounts = evidenceRefinement.counts || {};
  const refinedGwasCounts = evidenceRefinement.sourceCounts?.gwas || {};
  const groupingPreparation = result.groupPrep?.metadata || {};
  const groupedInterpretation = result.groupedIndividualInterpretation?.metadata || {};
  const individualInterpretation = result.individualInterpretation?.metadata || {};
  const interpretationNormalization = result.interpretationNormalization?.metadata || {};
  const globalInterpretation = result.globalInterpretation?.metadata || {};
  const finalReport = result.finalReport?.metadata || {};
  const groupedPrototype = result.groupedPrototype || {};
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
          [t.enrichmentCoordinateResolved, formatNumber(enrichmentQuality.identityMetrics?.ensembl?.resolved || 0, locale)],
          [t.enrichmentResolutionAmbiguous, formatNumber(enrichmentQuality.resolutionCounts?.ambiguous || 0, locale)],
          [t.enrichmentResolutionAlleleMismatch, formatNumber(enrichmentQuality.resolutionCounts?.vep_colocated_allele_mismatch || 0, locale)],
          [t.enrichmentSourceErrors, formatNumber(Object.values(enrichmentQuality.sourceErrors || {}).reduce((sum, value) => sum + Number(value || 0), 0), locale)],
          [t.enrichmentTechnicalGate, enrichmentQuality.technicalGate?.status || enrichmentQuality.status || "-"],
          [t.enrichmentEvidenceReadiness, enrichmentQuality.evidenceReadinessGate?.status || "-"],
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
  const evidenceRefinementCards = result.evidenceRefinement
    ? [
        [
          t.curatedRegistryVariants,
          formatNumber(
            evidenceRefinementCounts.physicalRegistryRows ?? evidenceRefinementCounts.matchedPhysicalRegistryRows,
            locale,
          ),
        ],
        [t.curatedPhysicalVariants, formatNumber(evidenceRefinementCounts.enrichedPhysicalVariants, locale)],
        [t.curatedDeepVariants, formatNumber(evidenceRefinementCounts.deepCuratedVariants, locale)],
        [t.curatedBenignVariants, formatNumber(evidenceRefinementCounts.benignContextVariants, locale)],
        [t.curatedAnnotationAbsent, formatNumber(evidenceRefinementCounts.annotationAbsentVariants, locale)],
        [t.curatedUnresolvedVariants, formatNumber(evidenceRefinementCounts.unresolvedVariants, locale)],
        [t.curatedFocusVariants, formatNumber(evidenceRefinementCounts.focusCandidateVariants, locale)],
        [t.curatedRetryRows, formatNumber(evidenceRefinementCounts.retryQueueRows, locale)],
        [t.curatedPublications, formatNumber(evidenceRefinementCounts.uniquePublications, locale)],
        [t.curatedGwasRaw, formatNumber(refinedGwasCounts.associations, locale)],
        [t.curatedGwasClustersHigh, formatNumber(refinedGwasCounts.highConfidenceReplicatedClusters, locale)],
        [t.curatedGwasClustersModerate, formatNumber(refinedGwasCounts.moderateContextualClusters, locale)],
        [t.curatedGwasMetadataPending, formatNumber(refinedGwasCounts.metadataDeferredOrRetry, locale)],
      ]
    : [];
  const groupingPreparationCards = result.groupPrep
    ? [
        [t.groupingPreparationGroups, formatNumber(groupingPreparation.total_groups, locale)],
        [t.groupingPreparationVariants, formatNumber(groupingPreparation.source_variants_total, locale)],
        [t.groupingPreparationAverageSize, formatNumber(groupingPreparation.average_group_size, locale)],
        [t.groupingPreparationLargeGroups, formatNumber(groupingPreparation.groups_gt_25, locale)],
        [t.groupingMaxTokens, formatNumber(groupingPreparation.max_estimated_tokens, locale)],
        [t.groupingWithinLimit, formatNumber(groupingPreparation.groups_within_hard_limit, locale)],
        [t.groupingCompressionReview, formatNumber(groupingPreparation.groups_requiring_compression_review, locale)],
        [t.groupingCoverageRecords, formatNumber(groupingPreparation.coverage_records, locale)],
        [t.groupingCoverageReconciled, groupingPreparation.coverage_reconciled ? "true" : "false"],
        [t.groupingPilotCandidates, formatNumber(groupingPreparation.pilot_candidates, locale)],
        [t.groupingTranscriptFocus, formatNumber(groupingPreparation.focus_variants_total, locale)],
        [t.groupingFocusReclassified, formatNumber(groupingPreparation.focus_rows_reclassified, locale)],
        [t.groupingApprovedMechanisms, formatNumber(groupingPreparation.groups_with_approved_mechanism, locale)],
        [t.groupingApprovedGwas, formatNumber(groupingPreparation.groups_with_approved_gwas_relevance, locale)],
        [t.groupingPayloadReady, formatNumber(groupingPreparation.groups_payload_ready, locale)],
        [t.groupingSourceFailureGroups, formatNumber(groupingPreparation.source_failure_groups, locale)],
      ]
    : [];
  const llm1CanaryGroups = Array.isArray(groupingPreparation.pilot_manifest)
    ? groupingPreparation.pilot_manifest
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
  const groupedPrototypeCards = groupedPrototype.status
    ? [
        [t.groupedPrototypeStatus, groupedPrototype.status],
        [t.groupedPrototypeCovered, formatNumber(groupedPrototype.counts?.scientifically_covered, locale)],
        [t.groupedPrototypeObserved, formatNumber(groupedPrototype.counts?.covered_with_observed_variant, locale)],
        [t.groupedPrototypeNoObserved, formatNumber(groupedPrototype.counts?.covered_no_observed_variant, locale)],
        [t.groupedPrototypeValid, formatNumber(groupedPrototype.counts?.valid_llm1_cards, locale)],
        [t.groupedPrototypeQuarantined, formatNumber(groupedPrototype.counts?.quarantined, locale)],
        [t.groupedPrototypeNotCovered, formatNumber(groupedPrototype.counts?.not_covered, locale)],
        [t.groupedPrototypeCost, `$${Number(groupedPrototype.telemetry?.estimated_cost_usd || 0).toFixed(4)} USD`],
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

  async function downloadEnrichmentPhysicalMatrix() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-physical-matrix`,
      "v2_enrichment_physical_matrix.csv",
    );
  }

  async function downloadEnrichmentResolutionAudit() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-resolution-audit`, "v2_enrichment_resolution_audit.jsonl");
  }

  async function downloadEnrichmentPhysicalEvidenceAudit() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-physical-evidence-audit`,
      "v2_enrichment_physical_evidence_audit.jsonl.gz",
    );
  }

  async function downloadEnrichmentModuleProjection() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-module-projection`,
      "v2_enrichment_module_projection.csv",
    );
  }

  async function downloadEnrichmentRetryQueue() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-retry-queue`, "enrichment_retry_queue.jsonl");
  }

  async function downloadEnrichmentIdentitySummary() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-identity-summary`,
      "enrichment_identity_resolution_summary.json",
    );
  }

  async function downloadEnrichmentPerformance() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment-performance`, "enrichment_performance_summary.json");
  }

  async function downloadCuratedArtifact(kind) {
    const artifacts = {
      physicalMatrix: ["curated-physical-matrix", "v2_curated_physical_variant_matrix.csv"],
      physicalRegistry: ["curated-physical-registry", "v2_curated_physical_variant_registry.csv"],
      moduleProjection: ["curated-module-projection", "v2_curated_gene_module_projection.csv"],
      canonicalStatus: ["canonical-gene-module-status", "v2_canonical_gene_module_status.csv"],
      clinvarAggregate: ["clinvar-aggregate", "clinvar_variant_aggregate.csv"],
      clinvarAssertions: ["clinvar-assertions", "clinvar_submitter_assertions.csv"],
      clinpgxClinical: ["clinpgx-clinical-annotations", "clinpgx_clinical_annotations.csv"],
      clinpgxVariant: ["clinpgx-variant-annotations", "clinpgx_variant_annotations.csv"],
      gwasAssociations: ["gwas-associations", "gwas_variant_associations.csv"],
      gwasVariantTraits: ["gwas-variant-traits", "gwas_variant_trait_summary.csv"],
      gwasGeneModule: ["gwas-gene-module-summary", "gwas_gene_module_summary.csv"],
      gwasClusters: ["gwas-evidence-clusters", "gwas_evidence_clusters.csv"],
      gwasRelevanceTemplate: ["gwas-relevance-template", "gwas_trait_module_relevance_template.csv"],
      gwasMetadataRetry: ["gwas-metadata-retry-queue", "gwas_metadata_retry_queue.jsonl"],
      publications: ["publication-evidence", "publication_evidence.csv"],
      raw: ["evidence-refinement-raw", "evidence_refinement_raw.jsonl.gz"],
      retryQueue: ["evidence-refinement-retry-queue", "evidence_refinement_retry_queue.jsonl"],
      summary: ["evidence-refinement-summary", "evidence_refinement_summary.json"],
    };
    const artifact = artifacts[kind];
    if (!artifact) return;
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/${artifact[0]}`, artifact[1]);
  }

  async function downloadGroupedPayloads() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/grouped-payloads`, "gene_module_group_payloads.csv");
  }

  async function downloadGroupedV4Artifact(kind) {
    const artifacts = {
      payloads: ["grouped-payloads-v4", "gene_module_group_payloads_v4.csv"],
      mechanisms: ["mechanism-registry", "mechanism_registry_v1.csv"],
      manifest: ["llm1-pilot-manifest", "llm1_pilot_manifest_v1.csv"],
    };
    const artifact = artifacts[kind];
    if (artifact) await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/${artifact[0]}`, artifact[1]);
  }

  async function downloadGroupedV5Artifact(kind) {
    const artifacts = {
      payloads: ["grouped-payloads-v5", "llm1_group_payloads_v5.csv"],
      packets: ["group-evidence-packets", "group_evidence_packets.jsonl.gz"],
      digests: ["group-evidence-digests", "group_evidence_digests.jsonl"],
      digestErrors: ["group-evidence-digest-errors", "group_evidence_digest_errors.csv"],
      tokens: ["group-token-budget-audit", "group_token_budget_audit.csv"],
      coverage: ["group-evidence-coverage-audit", "group_evidence_coverage_audit.csv"],
      errors: ["group-compression-errors", "group_compression_errors.csv"],
      summary: ["group-compression-summary", "group_compression_summary.json"],
      manifest: ["llm1-pilot-candidate-manifest-v2", "llm1_pilot_candidate_manifest_v2.csv"],
      schema: ["grouped-payload-v5-schema", "llm1_group_payload_v5.schema.json"],
    };
    const artifact = artifacts[kind];
    if (artifact) await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/${artifact[0]}`, artifact[1]);
  }

  async function downloadGroupedV6Artifact(kind) {
    const artifacts = {
      payloads: ["grouped-payloads-v6", "llm1_group_payloads_v6.csv"],
      targetGene: ["target-gene-consequence-audit", "target_gene_consequence_audit.csv"],
      frequency: ["allele-specific-frequency-audit", "allele_specific_frequency_audit.csv"],
      clinvar: ["clinvar-condition-conflict-audit", "clinvar_condition_conflict_audit.csv"],
      tokens: ["group-token-budget-audit-v6", "group_token_budget_audit_v6.csv"],
      summary: ["group-payload-v6-summary", "llm1_group_payload_v6_summary.json"],
      errors: ["group-payload-v6-errors", "group_payload_v6_errors.csv"],
      manifest: ["llm1-pilot-candidate-manifest-v3", "llm1_pilot_candidate_manifest_v3.csv"],
      schema: ["grouped-payload-v6-schema", "llm1_group_payload_v6.schema.json"],
      mechanisms: ["persistent-mechanism-registry", "mechanism_registry_v1.csv"],
      gwas: ["persistent-gwas-registry", "gwas_module_relevance_registry_v1.csv"],
    };
    const artifact = artifacts[kind];
    if (artifact) await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/${artifact[0]}`, artifact[1]);
  }

  async function downloadGroupedV7Artifact(kind) {
    const artifacts = {
      payloads: ["grouped-payloads-v7", "llm1_group_payloads_v7.csv"],
      preflight: ["group-preflight-v7", "llm1_group_preflight_v7.csv"],
      summary: ["group-payload-v7-summary", "llm1_group_payload_v7_summary.json"],
      schema: ["grouped-payload-v7-schema", "llm1_group_payload_v7.schema.json"],
      cards: ["llm1-group-cards", "llm1_group_cards.json"],
    };
    const artifact = artifacts[kind];
    if (artifact) await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/${artifact[0]}`, artifact[1]);
  }

  async function submitLlm1InternalReview(decision) {
    const curationToken = window.prompt("Internal scientific curation token");
    if (!curationToken) return;
    const reviewer = window.prompt("Reviewer name or internal identifier");
    if (!reviewer) return;
    const notes = window.prompt("Review notes (optional)") || "";
    try {
      const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${result.jobId}/llm1-internal-review`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-HEAL-Curation-Token": curationToken,
          ...accessHeaders(result.accessToken || getJobAccessToken(result.jobId)),
        },
        body: JSON.stringify({ decision, reviewer, notes }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not store the LLM1 internal review.");
      window.alert(`LLM1 internal review stored: ${payload.decision}`);
    } catch (reviewError) {
      setDownloadError(reviewError.message || String(reviewError));
    }
  }

  async function startLlm1PreflightV7() {
    const curationToken = window.prompt("Internal scientific curation token");
    if (!curationToken) return;
    try {
      const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${result.jobId}/llm1-preflight-v7`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-HEAL-Curation-Token": curationToken,
          ...accessHeaders(result.accessToken || getJobAccessToken(result.jobId)),
        },
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not regenerate the LLM1 v7 preflight.");
      window.alert(`LLM1 v7 preflight: ${payload.status}. The job view will reload with the new artifacts.`);
      window.location.reload();
    } catch (preflightError) {
      setDownloadError(preflightError.message || String(preflightError));
    }
  }

  function uploadProfessionalCuration(kind) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".csv,text/csv";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const curationToken = window.prompt("Internal scientific curation token");
      if (!curationToken) return;
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 0x8000) {
          binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        }
        const response = await fetch(`/api/vcf-canon-matches/${result.jobId}/curation/${kind}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-HEAL-Curation-Token": curationToken,
            ...accessHeaders(result.accessToken || getJobAccessToken(result.jobId)),
          },
          body: JSON.stringify({ csvBase64: window.btoa(binary) }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || "Could not upload internal scientific curation.");
        window.alert("Internal scientific curation accepted. V7 preflight and eligibility were recalculated.");
      } catch (error) {
        setDownloadError(error.message || String(error));
      }
    };
    input.click();
  }

  async function startEvidenceDigest() {
    try {
      const response = await fetch(`/api/vcf-canon-matches/${result.jobId}/evidence-digest`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...accessHeaders(result.accessToken || getJobAccessToken(result.jobId)) },
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not start evidence digest generation.");
    } catch (error) {
      setDownloadError(error.message || String(error));
    }
  }

  async function startLlm1Pilot() {
    try {
      const response = await fetch(`/api/vcf-canon-matches/${result.jobId}/llm1-pilot`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...accessHeaders(result.accessToken || getJobAccessToken(result.jobId)) },
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not start the controlled LLM1 pilot.");
    } catch (error) {
      window.alert(error.message || String(error));
    }
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

  async function downloadLlm1PilotArtifact(kind) {
    const artifacts = {
      payloads: ["llm1-pilot-approved-payloads", "llm1_pilot_approved_payloads_v6.jsonl"],
      raw: ["grouped-interpretation-raw-responses", "gene_module_group_interpretation_raw_responses.jsonl"],
      calls: ["grouped-interpretation-call-audit", "gene_module_group_interpretation_call_audit.csv"],
      prompt: ["llm1-pilot-prompt-snapshot", "llm1_pilot_prompt_snapshot.md"],
      schema: ["llm1-pilot-response-schema-snapshot", "llm1_pilot_response_schema_snapshot.json"],
    };
    const artifact = artifacts[kind];
    if (artifact) await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/${artifact[0]}`, artifact[1]);
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

  async function downloadGroupedPrototypeArtifact(kind, fallbackName) {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/grouped-prototype/download/${kind}`, fallbackName);
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
      {evidenceRefinementCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.evidenceRefinementTitle}</h3>
          <div className="metrics-grid">
            {evidenceRefinementCards.map(([label, value]) => (
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
      {isGeneModuleV2 && cardsReady && (
        <>
          <h3 className="result-subtitle">{t.llm1CardsTitle}</h3>
          <Llm1CardsPanel
            cards={llm1Cards}
            loading={llm1CardsLoading}
            error={llm1CardsError}
            language={llm1CardLanguage}
            onLanguageChange={setLlm1CardLanguage}
            t={t}
          />
          {!prototypeCardsReady && <div className="match-download-actions">
            <button className="secondary-button match-download-button" type="button" onClick={() => submitLlm1InternalReview("approved")}>{t.llm1ReviewApprove}</button>
            <button className="secondary-button match-download-button" type="button" onClick={() => submitLlm1InternalReview("rejected")}>{t.llm1ReviewReject}</button>
          </div>}
        </>
      )}
      {groupedPrototypeCards.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.groupedPrototypeTitle}</h3>
          <p className="llm1-card-notice">
            {t.groupedPrototypeNotice
              .replace("{covered}", formatNumber(groupedPrototype.counts?.scientifically_covered || 0, locale))
              .replace("{canonical}", formatNumber(groupedPrototype.counts?.canonical_groups || 180, locale))}
          </p>
          <div className="metrics-grid">
            {groupedPrototypeCards.map(([label, value]) => (
              <MetricCard label={label} value={value} key={label} />
            ))}
          </div>
        </>
      )}
      {llm1CanaryGroups.length > 0 && (
        <>
          <h3 className="result-subtitle">{t.llm1CanaryTitle}</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Gene + module</th>
                  <th>{t.llm1CanaryCategory}</th>
                  <th>{t.llm1CanaryFocus}</th>
                  <th>{t.llm1CanaryAltFrequency}</th>
                  <th>{t.llm1CanaryMechanism}</th>
                  <th>{t.llm1CanaryGwas}</th>
                  <th>{t.llm1CanaryErrors}</th>
                  <th>{t.llm1CanaryTokens}</th>
                  <th>{t.llm1CanaryBlockers}</th>
                </tr>
              </thead>
              <tbody>
                {llm1CanaryGroups.map((group) => (
                  <tr key={group.group_id}>
                    <td>{group.group_id}</td>
                    <td>{group.selection_category || "-"}</td>
                    <td>{group.target_concordant_focus_count}/{group.focus_variant_count}</td>
                    <td>{group.observed_alt_frequency_count}/{group.focus_variant_count}</td>
                    <td>{group.mechanism_status || "-"}</td>
                    <td>{group.gwas_relevance_status || "-"}</td>
                    <td>{formatNumber(group.source_error_count, locale)}</td>
                    <td>{formatNumber(group.estimated_tokens, locale)}</td>
                    <td>{group.blockers || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
        {artifactReady.groupedPrototypeDocx && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedPrototypeArtifact("docx", "HEAL_prototipo_desarrollo.docx")}><Download size={17} />{t.groupedPrototypeDocx}</button>}
        {artifactReady.groupedPrototypePdf && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedPrototypeArtifact("pdf", "HEAL_prototipo_desarrollo.pdf")}><Download size={17} />{t.groupedPrototypePdf}</button>}
        {artifactReady.groupedPrototypeCards && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedPrototypeArtifact("cards", "HEAL_prototipo_tarjetas.csv")}><Download size={17} />{t.groupedPrototypeCardsDownload}</button>}
        {artifactReady.groupedPrototype && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedPrototypeArtifact("coverage", "HEAL_prototipo_cobertura.csv")}><Download size={17} />{t.groupedPrototypeCoverageDownload}</button>}
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
        {isGeneModuleV2 && artifactReady.enrichmentPhysicalMatrix && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentPhysicalMatrix}>
          <Download size={17} />
          {t.enrichmentPhysicalMatrixDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentPhysicalEvidenceAudit && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentPhysicalEvidenceAudit}>
          <Download size={17} />
          {t.enrichmentPhysicalEvidenceAuditDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentModuleProjection && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentModuleProjection}>
          <Download size={17} />
          {t.enrichmentModuleProjectionDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentRetryQueue && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentRetryQueue}>
          <Download size={17} />
          {t.enrichmentRetryQueueDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentIdentitySummary && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentIdentitySummary}>
          <Download size={17} />
          {t.enrichmentIdentitySummaryDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentResolutionAudit && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentResolutionAudit}>
          <Download size={17} />
          {t.enrichmentResolutionAuditDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.enrichmentPerformance && <button className="secondary-button match-download-button" type="button" onClick={downloadEnrichmentPerformance}>
          <Download size={17} />
          {t.enrichmentPerformanceDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.curatedPhysicalMatrix && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("physicalMatrix")}>
          <Download size={17} />
          {t.curatedPhysicalMatrixDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.curatedPhysicalRegistry && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("physicalRegistry")}>
          <Download size={17} />
          {t.curatedPhysicalRegistryDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.curatedModuleProjection && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("moduleProjection")}>
          <Download size={17} />
          {t.curatedModuleProjectionDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.canonicalGeneModuleStatus && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("canonicalStatus")}>
          <Download size={17} />
          {t.canonicalStatusDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.clinvarAggregate && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("clinvarAggregate")}>
          <Download size={17} />
          {t.clinvarAggregateDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.clinvarAssertions && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("clinvarAssertions")}>
          <Download size={17} />
          {t.clinvarAssertionsDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.clinpgxClinicalAnnotations && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("clinpgxClinical")}>
          <Download size={17} />
          {t.clinpgxClinicalDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.clinpgxVariantAnnotations && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("clinpgxVariant")}>
          <Download size={17} />
          {t.clinpgxVariantDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.gwasAssociations && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("gwasAssociations")}>
          <Download size={17} />
          {t.gwasAssociationsDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.gwasVariantTraitSummary && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("gwasVariantTraits")}>
          <Download size={17} />
          {t.gwasVariantTraitsDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.gwasGeneModuleSummary && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("gwasGeneModule")}>
          <Download size={17} />
          {t.gwasGeneModuleDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.gwasEvidenceClusters && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("gwasClusters")}>
          <Download size={17} />
          {t.gwasClustersDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.gwasTraitModuleRelevanceTemplate && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("gwasRelevanceTemplate")}>
          <Download size={17} />
          {t.gwasRelevanceTemplateDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.gwasMetadataRetryQueue && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("gwasMetadataRetry")}>
          <Download size={17} />
          {t.gwasMetadataRetryDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.publicationEvidence && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("publications")}>
          <Download size={17} />
          {t.publicationEvidenceDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.evidenceRefinementRaw && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("raw")}>
          <Download size={17} />
          {t.evidenceRefinementRawDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.evidenceRefinementRetryQueue && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("retryQueue")}>
          <Download size={17} />
          {t.evidenceRefinementRetryDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.evidenceRefinementSummary && <button className="secondary-button match-download-button" type="button" onClick={() => downloadCuratedArtifact("summary")}>
          <Download size={17} />
          {t.evidenceRefinementSummaryDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedPayloads && <button className="secondary-button match-download-button" type="button" onClick={downloadGroupedPayloads}>
          <Download size={17} />
          {t.groupingPayloadsDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedPayloadsV4 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV4Artifact("payloads")}>
          <Download size={17} />
          {t.groupingPayloadsV4Download}
        </button>}
        {isGeneModuleV2 && artifactReady.groupedPayloadsV5 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("payloads")}><Download size={17} />{t.groupingPayloadsV5Download}</button>}
        {isGeneModuleV2 && artifactReady.groupEvidencePackets && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("packets")}><Download size={17} />{t.groupEvidencePacketsDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupEvidenceDigests && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("digests")}><Download size={17} />{t.groupEvidenceDigestsDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupEvidenceDigestErrors && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("digestErrors")}><Download size={17} />{t.groupEvidenceDigestErrorsDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupTokenBudgetAudit && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("tokens")}><Download size={17} />{t.groupTokenBudgetDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupEvidenceCoverageAudit && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("coverage")}><Download size={17} />{t.groupEvidenceCoverageDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupCompressionErrors && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("errors")}><Download size={17} />{t.groupCompressionErrorsDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupCompressionSummary && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("summary")}><Download size={17} />{t.groupCompressionSummaryDownload}</button>}
        {isGeneModuleV2 && artifactReady.llm1PilotCandidateManifestV2 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("manifest")}><Download size={17} />{t.llm1PilotCandidateManifestV2Download}</button>}
        {isGeneModuleV2 && artifactReady.groupPayloadSchemaV5 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV5Artifact("schema")}><Download size={17} />{t.groupPayloadSchemaV5Download}</button>}
        {isGeneModuleV2 && artifactReady.groupedPayloadsV6 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("payloads")}><Download size={17} />{t.groupingPayloadsV6Download}</button>}
        {isGeneModuleV2 && artifactReady.groupedPayloadsV7 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV7Artifact("payloads")}><Download size={17} />{t.groupingPayloadsV7Download}</button>}
        {isGeneModuleV2 && artifactReady.groupPreflightV7 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV7Artifact("preflight")}><Download size={17} />{t.groupPreflightV7Download}</button>}
        {isGeneModuleV2 && artifactReady.groupPayloadV7Summary && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV7Artifact("summary")}><Download size={17} />{t.groupPayloadV7SummaryDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupPayloadSchemaV7 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV7Artifact("schema")}><Download size={17} />{t.groupPayloadSchemaV7Download}</button>}
        {isGeneModuleV2 && artifactReady.groupCardsV7 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV7Artifact("cards")}><Download size={17} />{t.llm1CardsDownload}</button>}
        {isGeneModuleV2 && artifactReady.targetGeneConsequenceAudit && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("targetGene")}><Download size={17} />{t.targetGeneAuditDownload}</button>}
        {isGeneModuleV2 && artifactReady.alleleSpecificFrequencyAudit && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("frequency")}><Download size={17} />{t.alleleFrequencyAuditDownload}</button>}
        {isGeneModuleV2 && artifactReady.clinvarConditionConflictAudit && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("clinvar")}><Download size={17} />{t.clinvarConflictAuditDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupTokenBudgetAuditV6 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("tokens")}><Download size={17} />{t.groupTokenBudgetV6Download}</button>}
        {isGeneModuleV2 && artifactReady.groupPayloadV6Summary && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("summary")}><Download size={17} />{t.groupPayloadV6SummaryDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupPayloadV6Errors && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("errors")}><Download size={17} />{t.groupPayloadV6ErrorsDownload}</button>}
        {isGeneModuleV2 && artifactReady.llm1PilotCandidateManifestV3 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("manifest")}><Download size={17} />{t.llm1PilotCandidateManifestV3Download}</button>}
        {isGeneModuleV2 && artifactReady.groupPayloadSchemaV6 && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("schema")}><Download size={17} />{t.groupPayloadSchemaV6Download}</button>}
        {isGeneModuleV2 && artifactReady.persistentMechanismRegistry && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("mechanisms")}><Download size={17} />{t.persistentMechanismRegistryDownload}</button>}
        {isGeneModuleV2 && artifactReady.persistentGwasRegistry && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV6Artifact("gwas")}><Download size={17} />{t.persistentGwasRegistryDownload}</button>}
        {isGeneModuleV2 && artifactReady.persistentMechanismRegistry && <button className="secondary-button match-download-button" type="button" onClick={() => uploadProfessionalCuration("mechanism")}>{t.curationMechanismUpload}</button>}
        {isGeneModuleV2 && artifactReady.persistentGwasRegistry && <button className="secondary-button match-download-button" type="button" onClick={() => uploadProfessionalCuration("gwas")}>{t.curationGwasUpload}</button>}
        {isGeneModuleV2 && artifactReady.llm1PilotCandidateManifestV3 && <button className="secondary-button match-download-button" type="button" onClick={() => uploadProfessionalCuration("manifest")}>{t.curationManifestUpload}</button>}
        {isGeneModuleV2 && artifactReady.groupedPayloadsV6 && <button className="secondary-button match-download-button" type="button" onClick={startLlm1PreflightV7}>{t.llm1PreflightV7Start}</button>}
        {isGeneModuleV2 && Number(groupingPreparation.groups_requiring_compression_review || 0) > 0 && artifactReady.groupEvidencePackets && <button className="secondary-button match-download-button" type="button" onClick={startEvidenceDigest}>{t.evidenceDigestStart}</button>}
        {isGeneModuleV2 && artifactReady.mechanismRegistry && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV4Artifact("mechanisms")}>
          <Download size={17} />
          {t.mechanismRegistryDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.llm1PilotManifest && <button className="secondary-button match-download-button" type="button" onClick={() => downloadGroupedV4Artifact("manifest")}>
          <Download size={17} />
          {t.llm1PilotManifestDownload}
        </button>}
        {isGeneModuleV2 && artifactReady.llm1PilotCandidateManifestV3 && <button className="secondary-button match-download-button" type="button" onClick={startLlm1Pilot}>
          {t.llm1PilotStart}
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
        {isGeneModuleV2 && artifactReady.groupedInterpretationRawResponses && <button className="secondary-button match-download-button" type="button" onClick={() => downloadLlm1PilotArtifact("raw")}><Download size={17} />{t.groupedInterpretationRawDownload}</button>}
        {isGeneModuleV2 && artifactReady.llm1PilotApprovedPayloads && <button className="secondary-button match-download-button" type="button" onClick={() => downloadLlm1PilotArtifact("payloads")}><Download size={17} />{t.llm1PilotApprovedPayloadsDownload}</button>}
        {isGeneModuleV2 && artifactReady.groupedInterpretationCallAudit && <button className="secondary-button match-download-button" type="button" onClick={() => downloadLlm1PilotArtifact("calls")}><Download size={17} />{t.groupedInterpretationCallAuditDownload}</button>}
        {isGeneModuleV2 && artifactReady.llm1PilotPromptSnapshot && <button className="secondary-button match-download-button" type="button" onClick={() => downloadLlm1PilotArtifact("prompt")}><Download size={17} />{t.llm1PilotPromptDownload}</button>}
        {isGeneModuleV2 && artifactReady.llm1PilotResponseSchemaSnapshot && <button className="secondary-button match-download-button" type="button" onClick={() => downloadLlm1PilotArtifact("schema")}><Download size={17} />{t.llm1PilotSchemaDownload}</button>}
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

function Tier1CurationV2Modal({ open, onClose, language }) {
  const [token, setToken] = useState("");
  const [snapshotId, setSnapshotId] = useState("tier1-v2-20260805");
  const [status, setStatus] = useState(null);
  const [review, setReview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const es = language === "es";

  async function request(endpoint, options = {}) {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers: { "Content-Type": "application/json", "X-HEAL-Curation-Token": token, ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  async function refresh() {
    if (!token) return;
    setBusy(true); setError("");
    try {
      const next = await request("/api/tier1-curation-v2/status");
      setStatus(next);
      const selected = next.snapshots?.find((item) => item.snapshotId === snapshotId);
      if (selected?.fullCandidate) setReview(await request(`/api/tier1-curation-v2/snapshots/${encodeURIComponent(snapshotId)}/review`));
    } catch (caught) { setError(caught.message || String(caught)); }
    finally { setBusy(false); }
  }

  async function start(operation, extra = {}) {
    setBusy(true); setError("");
    try {
      await request("/api/tier1-curation-v2/tasks", { method: "POST", body: JSON.stringify({ operation, snapshotId, ...extra }) });
      await refresh();
    } catch (caught) { setError(caught.message || String(caught)); setBusy(false); }
  }

  async function uploadJson(file, kind) {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const value = JSON.parse(await file.text());
      if (kind === "gold") {
        await request(`/api/tier1-curation-v2/snapshots/${encodeURIComponent(snapshotId)}/gold`, { method: "PUT", body: JSON.stringify(value) });
      } else {
        const owner = window.prompt(es ? "Responsable interno" : "Internal owner") || "";
        const approvalReference = window.prompt(es ? "Referencia de aprobación" : "Approval reference") || "";
        await request(`/api/tier1-curation-v2/snapshots/${encodeURIComponent(snapshotId)}/review`, { method: "PUT", body: JSON.stringify({ reviewManifest: value, owner, approvalReference }) });
      }
      await refresh();
    } catch (caught) { setError(caught.message || String(caught)); }
    finally { setBusy(false); }
  }

  if (!open) return null;
  const selected = status?.snapshots?.find((item) => item.snapshotId === snapshotId);
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="tier1-curation-title">
      <section className="canon-modal tier1-curation-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">{es ? "Curación científica interna" : "Internal scientific curation"}</p>
            <h2 id="tier1-curation-title">Tier 1 mechanism curation v2</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="curation-toolbar">
          <label><span>Token</span><input type="password" value={token} onChange={(event) => setToken(event.target.value)} /></label>
          <label><span>Snapshot ID</span><input value={snapshotId} onChange={(event) => setSnapshotId(event.target.value)} /></label>
          <button className="secondary-button" type="button" disabled={!token || busy} onClick={refresh}>{busy ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />}{es ? "Actualizar" : "Refresh"}</button>
        </div>
        {error && <p className="error-message">{error}</p>}
        {status && (
          <>
            <div className="canon-mini-grid">
              <MetricCard label={es ? "Estado" : "State"} value={status.enabled ? "enabled" : "disabled"} />
              <MetricCard label="Model" value={status.model} />
              <MetricCard label="Cutoff" value={status.evidenceCutoff} />
              <MetricCard label={es ? "Registro activo modificado" : "Active registry changed"} value={status.activeRegistryModified ? "yes" : "no"} />
            </div>
            <div className="match-download-actions">
              <button className="secondary-button" type="button" disabled={!status.enabled || busy || Boolean(selected?.evidence)} onClick={() => start("collect_evidence", { scope: "all" })}>{es ? "Recolectar evidencia 105 grupos" : "Collect evidence for 105 groups"}</button>
              <button className="secondary-button" type="button" disabled={!status.enabled || busy || !selected?.evidence || Boolean(selected?.gold)} onClick={() => start("prepare_gold")}>{es ? "Preparar gold de 12" : "Prepare 12-group gold"}</button>
              <label className="secondary-button file-action">{es ? "Cargar gold aprobado" : "Upload approved gold"}<input type="file" accept="application/json,.json" onChange={(event) => uploadJson(event.target.files?.[0], "gold")} /></label>
              <label className="secondary-button file-action">{es ? "Cargar revisión del snapshot" : "Upload snapshot review"}<input type="file" accept="application/json,.json" onChange={(event) => uploadJson(event.target.files?.[0], "review")} /></label>
            </div>
            <p className="curation-safety-note">{es ? "Este panel sólo crea candidatos. La activación de Luna y la modificación del registro activo permanecen bloqueadas." : "This panel only creates candidates. Luna activation and active-registry changes remain blocked."}</p>
          </>
        )}
        {selected && (
          <div className="canon-preview">
            <h3>{es ? "Estado del snapshot" : "Snapshot state"}</h3>
            <div className="canon-mini-grid">
              <MetricCard label={es ? "Paquetes" : "Packets"} value={selected.evidence?.completed_groups ?? 0} />
              <MetricCard label="Gold" value={selected.gold?.approval_status || "not prepared"} />
              <MetricCard label={es ? "Candidato completo" : "Full candidate"} value={selected.fullCandidate?.groups ?? 0} />
              <MetricCard label={es ? "Aprobación" : "Approval"} value={selected.approval?.status || "pending"} />
            </div>
          </div>
        )}
        {review?.promptVersions?.length > 0 && (
          <div className="canon-preview"><h3>{es ? "Versiones del prompt" : "Prompt versions"}</h3><div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Hash</th><th>Phase</th><th>Passed</th><th>Dominant failure</th></tr></thead><tbody>{review.promptVersions.map((row) => <tr key={row.candidate}><td>{row.candidate}</td><td>{row.manifest?.prompt_sha256?.slice(0, 12) || "-"}</td><td>{row.acceptance?.phase || "-"}</td><td>{String(Boolean(row.acceptance?.passed))}</td><td>{row.acceptance?.dominant_failure_signature || "-"}</td></tr>)}</tbody></table></div></div>
        )}
        {review?.candidates?.length > 0 && (
          <div className="canon-preview">
            <h3>{es ? "Registro actual vs candidato v2" : "Current registry vs v2 candidate"}</h3>
            <div className="table-wrap"><table><thead><tr><th>Group</th><th>Current</th><th>Candidate</th><th>Context</th><th>Ceiling</th><th>Confidence</th><th>Evidence</th><th>Arbiter</th></tr></thead><tbody>{review.candidates.map((row) => <tr key={row.group_id}><td>{row.group_id}</td><td>{row.current_core_status}</td><td>{row.core_status}</td><td>{String(row.context_usable)}</td><td>{row.inference_ceiling}</td><td>{row.confidence}</td><td>{row.evidence_selected_count}</td><td>{String(row.adjudicated)}</td></tr>)}</tbody></table></div>
          </div>
        )}
        {review?.flags?.length > 0 && <div className="canon-preview"><h3>{es ? "Flags transversales (no modifican decisiones)" : "Cross-group flags (do not change decisions)"}</h3>{review.flags.map((flag) => <p className="warning" key={`${flag.module_id}-${flag.flag}`}>{flag.module_id}: {flag.flag} ({flag.count})</p>)}</div>}
      </section>
    </div>
  );
}

function App() {
  const fileInputRef = useRef(null);
  const [language, setLanguage] = useState("es");
  const [tier1CurationOpen, setTier1CurationOpen] = useState(false);
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
  const [evidenceRefinementProgress, setEvidenceRefinementProgress] = useState(0);
  const [groupingPreparationProgress, setGroupingPreparationProgress] = useState(0);
  const [groupedInterpretationProgress, setGroupedInterpretationProgress] = useState(0);
  const [groupedPrototypeProgress, setGroupedPrototypeProgress] = useState(0);
  const [individualInterpretationProgress, setIndividualInterpretationProgress] = useState(0);
  const [interpretationNormalizationProgress, setInterpretationNormalizationProgress] = useState(0);
  const [globalInterpretationProgress, setGlobalInterpretationProgress] = useState(0);
  const [finalReportProgress, setFinalReportProgress] = useState(0);
  const [stageProgressDetails, setStageProgressDetails] = useState({});
  const [executionLogs, setExecutionLogs] = useState([]);
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
  const [activeMatchJobId, setActiveMatchJobId] = useState(null);
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
    enrichmentPhysicalMatrix: false,
    enrichmentPhysicalEvidenceAudit: false,
    enrichmentModuleProjection: false,
    enrichmentRetryQueue: false,
    enrichmentIdentitySummary: false,
    enrichmentPerformance: false,
    enrichmentInterpretive: false,
    enrichmentPlus: false,
    enrichmentQuality: false,
    curatedPhysicalMatrix: false,
    curatedPhysicalRegistry: false,
    curatedModuleProjection: false,
    canonicalGeneModuleStatus: false,
    clinvarAggregate: false,
    clinvarAssertions: false,
    clinpgxClinicalAnnotations: false,
    clinpgxVariantAnnotations: false,
    gwasAssociations: false,
    gwasVariantTraitSummary: false,
    gwasGeneModuleSummary: false,
    gwasEvidenceClusters: false,
    gwasTraitModuleRelevanceTemplate: false,
    gwasMetadataRetryQueue: false,
    publicationEvidence: false,
    evidenceRefinementRaw: false,
    evidenceRefinementRetryQueue: false,
    evidenceRefinementSummary: false,
    groupedPayloads: false,
    groupedPayloadsV4: false,
    groupedPayloadsV5: false,
    groupedPayloadsV6: false,
    groupEvidencePackets: false,
    groupEvidenceDigests: false,
    groupEvidenceDigestErrors: false,
    groupTokenBudgetAudit: false,
    groupEvidenceCoverageAudit: false,
    groupCompressionErrors: false,
    groupCompressionSummary: false,
    groupPayloadSchemaV5: false,
    groupPayloadSchemaV6: false,
    targetGeneConsequenceAudit: false,
    alleleSpecificFrequencyAudit: false,
    clinvarConditionConflictAudit: false,
    groupTokenBudgetAuditV6: false,
    groupPayloadV6Summary: false,
    groupPayloadV6Errors: false,
    persistentMechanismRegistry: false,
    persistentGwasRegistry: false,
    mechanismRegistry: false,
    llm1PilotManifest: false,
    llm1PilotCandidateManifestV2: false,
    llm1PilotCandidateManifestV3: false,
    groupedVariantDetail: false,
    groupedInterpretation: false,
    llm1PilotApprovedPayloads: false,
    groupedInterpretationRawResponses: false,
    groupedInterpretationCallAudit: false,
    llm1PilotPromptSnapshot: false,
    llm1PilotResponseSchemaSnapshot: false,
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
    setEvidenceRefinementProgress(0);
    setGroupingPreparationProgress(0);
    setGroupedInterpretationProgress(0);
    setGroupedPrototypeProgress(0);
    setIndividualInterpretationProgress(0);
    setInterpretationNormalizationProgress(0);
    setGlobalInterpretationProgress(0);
    setFinalReportProgress(0);
    setStageProgressDetails({});
    setExecutionLogs([]);
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
      enrichmentPhysicalMatrix: false,
      enrichmentPhysicalEvidenceAudit: false,
      enrichmentModuleProjection: false,
      enrichmentRetryQueue: false,
      enrichmentIdentitySummary: false,
      enrichmentPerformance: false,
      enrichmentInterpretive: false,
      enrichmentPlus: false,
      enrichmentQuality: false,
      curatedPhysicalMatrix: false,
      curatedPhysicalRegistry: false,
      curatedModuleProjection: false,
      canonicalGeneModuleStatus: false,
      clinvarAggregate: false,
      clinvarAssertions: false,
      clinpgxClinicalAnnotations: false,
      clinpgxVariantAnnotations: false,
      gwasAssociations: false,
      publicationEvidence: false,
      evidenceRefinementRaw: false,
      evidenceRefinementRetryQueue: false,
      evidenceRefinementSummary: false,
      groupedPayloads: false,
      groupedPayloadsV4: false,
      groupedPayloadsV5: false,
      groupedPayloadsV6: false,
      groupEvidencePackets: false,
      groupEvidenceDigests: false,
      groupEvidenceDigestErrors: false,
      groupTokenBudgetAudit: false,
      groupEvidenceCoverageAudit: false,
      groupCompressionErrors: false,
      groupCompressionSummary: false,
      groupPayloadSchemaV5: false,
      groupPayloadSchemaV6: false,
      targetGeneConsequenceAudit: false,
      alleleSpecificFrequencyAudit: false,
      clinvarConditionConflictAudit: false,
      groupTokenBudgetAuditV6: false,
      groupPayloadV6Summary: false,
      groupPayloadV6Errors: false,
      persistentMechanismRegistry: false,
      persistentGwasRegistry: false,
      mechanismRegistry: false,
      llm1PilotManifest: false,
      llm1PilotCandidateManifestV2: false,
      llm1PilotCandidateManifestV3: false,
      groupedVariantDetail: false,
      groupedInterpretation: false,
      llm1PilotApprovedPayloads: false,
      groupedInterpretationRawResponses: false,
      groupedInterpretationCallAudit: false,
      llm1PilotPromptSnapshot: false,
      llm1PilotResponseSchemaSnapshot: false,
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

  async function refreshExecutionLogs(jobId) {
    if (!jobId) return;
    const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}/logs?limit=250`, {
      headers: accessHeaders(activeAccessTokenRef.current || getJobAccessToken(jobId)),
    });
    if (!response.ok) return;
    const payload = await response.json();
    setExecutionLogs(Array.isArray(payload.logs) ? payload.logs : []);
  }

  async function downloadExecutionLogs() {
    const jobId = activeMatchJobId || matchResult?.jobId;
    if (!jobId) return;
    const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}/logs?limit=500&download=1`, {
      headers: accessHeaders(activeAccessTokenRef.current || getJobAccessToken(jobId)),
    });
    if (!response.ok) return;
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${jobId}.jsonl`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
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
      } else if (kind === "enrichmentPhysicalEvidenceAudit") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-physical-evidence-audit`,
          "v2_enrichment_physical_evidence_audit.jsonl.gz",
        );
      } else if (kind === "enrichmentModuleProjection") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-module-projection`,
          "v2_enrichment_module_projection.csv",
        );
      } else if (kind === "enrichmentRetryQueue") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/enrichment-retry-queue`, "enrichment_retry_queue.jsonl");
      } else if (kind === "enrichmentIdentitySummary") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-identity-summary`,
          "enrichment_identity_resolution_summary.json",
        );
      } else if (kind === "enrichmentPerformance") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/enrichment-performance`, "enrichment_performance_summary.json");
      } else if (kind === "curatedPhysicalMatrix") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/curated-physical-matrix`,
          "v2_curated_physical_variant_matrix.csv",
        );
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
      enrichmentPhysicalMatrix: Boolean(ready.enrichmentPhysicalMatrix),
      enrichmentPhysicalEvidenceAudit: Boolean(ready.enrichmentPhysicalEvidenceAudit),
      enrichmentModuleProjection: Boolean(ready.enrichmentModuleProjection),
      enrichmentRetryQueue: Boolean(ready.enrichmentRetryQueue),
      enrichmentIdentitySummary: Boolean(ready.enrichmentIdentitySummary),
      enrichmentPerformance: Boolean(ready.enrichmentPerformance),
      enrichmentInterpretive: Boolean(ready.enrichmentInterpretive),
      enrichmentPlus: Boolean(ready.enrichmentPlus),
      enrichmentQuality: Boolean(ready.enrichmentQuality),
      curatedPhysicalMatrix: Boolean(ready.curatedPhysicalMatrix),
      curatedPhysicalRegistry: Boolean(ready.curatedPhysicalRegistry),
      curatedModuleProjection: Boolean(ready.curatedModuleProjection),
      canonicalGeneModuleStatus: Boolean(ready.canonicalGeneModuleStatus),
      clinvarAggregate: Boolean(ready.clinvarAggregate),
      clinvarAssertions: Boolean(ready.clinvarAssertions),
      clinpgxClinicalAnnotations: Boolean(ready.clinpgxClinicalAnnotations),
      clinpgxVariantAnnotations: Boolean(ready.clinpgxVariantAnnotations),
      gwasAssociations: Boolean(ready.gwasAssociations),
      gwasVariantTraitSummary: Boolean(ready.gwasVariantTraitSummary),
      gwasGeneModuleSummary: Boolean(ready.gwasGeneModuleSummary),
      gwasEvidenceClusters: Boolean(ready.gwasEvidenceClusters),
      gwasTraitModuleRelevanceTemplate: Boolean(ready.gwasTraitModuleRelevanceTemplate),
      gwasMetadataRetryQueue: Boolean(ready.gwasMetadataRetryQueue),
      publicationEvidence: Boolean(ready.publicationEvidence),
      evidenceRefinementRaw: Boolean(ready.evidenceRefinementRaw),
      evidenceRefinementRetryQueue: Boolean(ready.evidenceRefinementRetryQueue),
      evidenceRefinementSummary: Boolean(ready.evidenceRefinementSummary),
      groupedPayloads: Boolean(ready.groupedPayloads),
      groupedPayloadsV4: Boolean(ready.groupedPayloadsV4),
      groupedPayloadsV5: Boolean(ready.groupedPayloadsV5),
      groupedPayloadsV6: Boolean(ready.groupedPayloadsV6),
      groupEvidencePackets: Boolean(ready.groupEvidencePackets),
      groupEvidenceDigests: Boolean(ready.groupEvidenceDigests),
      groupEvidenceDigestErrors: Boolean(ready.groupEvidenceDigestErrors),
      groupTokenBudgetAudit: Boolean(ready.groupTokenBudgetAudit),
      groupEvidenceCoverageAudit: Boolean(ready.groupEvidenceCoverageAudit),
      groupCompressionErrors: Boolean(ready.groupCompressionErrors),
      groupCompressionSummary: Boolean(ready.groupCompressionSummary),
      groupPayloadSchemaV5: Boolean(ready.groupPayloadSchemaV5),
      groupPayloadSchemaV6: Boolean(ready.groupPayloadSchemaV6),
      targetGeneConsequenceAudit: Boolean(ready.targetGeneConsequenceAudit),
      alleleSpecificFrequencyAudit: Boolean(ready.alleleSpecificFrequencyAudit),
      clinvarConditionConflictAudit: Boolean(ready.clinvarConditionConflictAudit),
      groupTokenBudgetAuditV6: Boolean(ready.groupTokenBudgetAuditV6),
      groupPayloadV6Summary: Boolean(ready.groupPayloadV6Summary),
      groupPayloadV6Errors: Boolean(ready.groupPayloadV6Errors),
      persistentMechanismRegistry: Boolean(ready.persistentMechanismRegistry),
      persistentGwasRegistry: Boolean(ready.persistentGwasRegistry),
      mechanismRegistry: Boolean(ready.mechanismRegistry),
      llm1PilotManifest: Boolean(ready.llm1PilotManifest),
      llm1PilotCandidateManifestV2: Boolean(ready.llm1PilotCandidateManifestV2),
      llm1PilotCandidateManifestV3: Boolean(ready.llm1PilotCandidateManifestV3),
      groupedVariantDetail: Boolean(ready.groupedVariantDetail),
      groupedInterpretation: Boolean(ready.groupedInterpretation),
      llm1PilotApprovedPayloads: Boolean(ready.llm1PilotApprovedPayloads),
      groupedInterpretationRawResponses: Boolean(ready.groupedInterpretationRawResponses),
      groupedInterpretationCallAudit: Boolean(ready.groupedInterpretationCallAudit),
      llm1PilotPromptSnapshot: Boolean(ready.llm1PilotPromptSnapshot),
      llm1PilotResponseSchemaSnapshot: Boolean(ready.llm1PilotResponseSchemaSnapshot),
      individualInterpretation: Boolean(ready.individualInterpretation),
      interpretationNormalization: Boolean(ready.interpretationNormalization),
      globalInterpretation: Boolean(ready.globalInterpretation),
      finalReport: Boolean(ready.finalReport),
      groupedPrototype: Boolean(ready.groupedPrototype),
      groupedPrototypeDocx: Boolean(ready.groupedPrototypeDocx),
      groupedPrototypePdf: Boolean(ready.groupedPrototypePdf),
      groupedPrototypeCards: Boolean(ready.groupedPrototypeCards),
      groupedPrototypeAudit: Boolean(ready.groupedPrototypeAudit),
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
      ready.enrichmentPhysicalMatrix ||
      ready.enrichmentPhysicalEvidenceAudit ||
      ready.enrichmentModuleProjection ||
      ready.enrichmentRetryQueue ||
      ready.enrichmentIdentitySummary ||
      ready.enrichmentPerformance ||
      ready.enrichmentInterpretive ||
      ready.enrichmentPlus ||
      ready.enrichmentQuality ||
      ready.curatedPhysicalMatrix ||
      ready.curatedPhysicalRegistry ||
      ready.curatedModuleProjection ||
      ready.evidenceRefinementSummary ||
      ready.groupedPayloads ||
      ready.groupedVariantDetail ||
      ready.groupedInterpretation ||
      ready.individualInterpretation ||
      ready.interpretationNormalization ||
      ready.globalInterpretation ||
      ready.finalReport ||
      ready.groupedPrototype
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
      setActiveMatchJobId(job.id);
      updateMatchSnapshot(job);
      refreshExecutionLogs(job.id).catch(() => {});
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
      } else if (job.stage === "enrichment_identity") {
        setPhase("enrichment_identity");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentVepBaseProgress(100);
        setEnrichmentCompleteProgress(0);
        setEnrichmentVepOnlyProgress(job.stageProgress ?? job.progress ?? 0);
        setEnrichmentProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.enrichmentVepOnlyProgress);
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
      } else if (job.stage === "evidence_refinement" || job.stage === "evidence_refinement_quality_gate") {
        setPhase(job.stage);
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentVepBaseProgress(100);
        setEnrichmentCompleteProgress(100);
        setEnrichmentVepOnlyProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setEvidenceRefinementProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.evidenceRefining);
      } else if (job.stage === "grouping_preparation") {
        setPhase("grouping_preparation");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setEvidenceRefinementProgress(100);
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
        setEvidenceRefinementProgress(100);
        setGroupingPreparationProgress(100);
        setGroupedInterpretationProgress(job.stageProgress ?? job.progress ?? 0);
        setGroupedInterpretationDetail(groupedInterpretationDetailFromMessage(job.message, t));
        setCustomMessage(job.message || t.groupedInterpreting);
      } else if (job.stage === "grouped_prototype") {
        setPhase("grouped_prototype");
        setMatchProgress(100);
        setNormalizationProgress(100);
        setPreparationProgress(100);
        setAiTriageProgress(100);
        setEnrichmentProgress(100);
        setEnrichmentQualityProgress(100);
        setEvidenceRefinementProgress(100);
        setGroupingPreparationProgress(100);
        setGroupedInterpretationProgress(100);
        setGroupedPrototypeProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.groupedPrototypeProgress);
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
        if (job.artifactsReady?.evidenceRefinementSummary || job.result?.evidenceRefinement) {
          setEvidenceRefinementProgress(100);
        }
        if (job.artifactsReady?.groupedPayloads || job.result?.groupPrep) {
          setGroupingPreparationProgress(100);
        }
        if (job.artifactsReady?.groupedInterpretation || job.result?.groupedIndividualInterpretation) {
          setGroupedInterpretationProgress(100);
        }
        if (job.artifactsReady?.groupedPrototype || job.result?.groupedPrototype) {
          setGroupedPrototypeProgress(100);
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
              : isVariantEnrichmentStage(job.stage)
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
          setEvidenceRefinementProgress(0);
          setGroupingPreparationProgress(0);
          setGroupedInterpretationProgress(0);
          setGroupedPrototypeProgress(0);
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
            enrichmentPhysicalMatrix: false,
            enrichmentPerformance: false,
            enrichmentInterpretive: false,
            enrichmentPlus: false,
            enrichmentQuality: false,
            curatedPhysicalMatrix: false,
            curatedPhysicalRegistry: false,
            curatedModuleProjection: false,
            canonicalGeneModuleStatus: false,
            clinvarAggregate: false,
            clinvarAssertions: false,
            clinpgxClinicalAnnotations: false,
            clinpgxVariantAnnotations: false,
            gwasAssociations: false,
            publicationEvidence: false,
            evidenceRefinementRaw: false,
            evidenceRefinementRetryQueue: false,
            evidenceRefinementSummary: false,
            groupedPayloads: false,
            groupedPayloadsV4: false,
            groupedPayloadsV5: false,
            groupedPayloadsV6: false,
            groupEvidencePackets: false,
            groupEvidenceDigests: false,
            groupEvidenceDigestErrors: false,
            groupTokenBudgetAudit: false,
            groupEvidenceCoverageAudit: false,
            groupCompressionErrors: false,
            groupCompressionSummary: false,
            groupPayloadSchemaV5: false,
            groupPayloadSchemaV6: false,
            targetGeneConsequenceAudit: false,
            alleleSpecificFrequencyAudit: false,
            clinvarConditionConflictAudit: false,
            groupTokenBudgetAuditV6: false,
            groupPayloadV6Summary: false,
            groupPayloadV6Errors: false,
            persistentMechanismRegistry: false,
            persistentGwasRegistry: false,
            mechanismRegistry: false,
            llm1PilotManifest: false,
            llm1PilotCandidateManifestV2: false,
            llm1PilotCandidateManifestV3: false,
            groupedVariantDetail: false,
            groupedInterpretation: false,
            llm1PilotApprovedPayloads: false,
            groupedInterpretationRawResponses: false,
            groupedInterpretationCallAudit: false,
            llm1PilotPromptSnapshot: false,
            llm1PilotResponseSchemaSnapshot: false,
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
    setEvidenceRefinementProgress(0);
    setGroupingPreparationProgress(0);
    setGroupedInterpretationProgress(0);
    setGroupedPrototypeProgress(0);
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
        : nextMatchResult?.evidenceRefinement
          ? "evidenceRefinementComplete"
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
    if (nextMatchResult?.artifactsReady?.evidenceRefinementSummary || nextMatchResult?.evidenceRefinement) {
      setEvidenceRefinementProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.groupedPayloads || nextMatchResult?.groupPrep) {
      setGroupingPreparationProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.groupedInterpretation || nextMatchResult?.groupedIndividualInterpretation) {
      setGroupedInterpretationProgress(100);
    }
    if (nextMatchResult?.artifactsReady?.groupedPrototype || nextMatchResult?.groupedPrototype) {
      setGroupedPrototypeProgress(100);
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
      if (isVariantEnrichmentStage(caught.stage)) {
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
    setActiveMatchJobId(null);
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
      curatedPhysicalMatrix: false,
      curatedPhysicalRegistry: false,
      curatedModuleProjection: false,
      canonicalGeneModuleStatus: false,
      clinvarAggregate: false,
      clinvarAssertions: false,
      clinpgxClinicalAnnotations: false,
      clinpgxVariantAnnotations: false,
      gwasAssociations: false,
      publicationEvidence: false,
      evidenceRefinementRaw: false,
      evidenceRefinementRetryQueue: false,
      evidenceRefinementSummary: false,
      groupedPayloads: false,
      groupedPayloadsV4: false,
      groupedPayloadsV5: false,
      groupedPayloadsV6: false,
      groupEvidencePackets: false,
      groupEvidenceDigests: false,
      groupEvidenceDigestErrors: false,
      groupTokenBudgetAudit: false,
      groupEvidenceCoverageAudit: false,
      groupCompressionErrors: false,
      groupCompressionSummary: false,
      groupPayloadSchemaV5: false,
      groupPayloadSchemaV6: false,
      targetGeneConsequenceAudit: false,
      alleleSpecificFrequencyAudit: false,
      clinvarConditionConflictAudit: false,
      groupTokenBudgetAuditV6: false,
      groupPayloadV6Summary: false,
      groupPayloadV6Errors: false,
      persistentMechanismRegistry: false,
      persistentGwasRegistry: false,
      mechanismRegistry: false,
      llm1PilotManifest: false,
      llm1PilotCandidateManifestV2: false,
      llm1PilotCandidateManifestV3: false,
      groupedVariantDetail: false,
      groupedInterpretation: false,
      llm1PilotApprovedPayloads: false,
      groupedInterpretationRawResponses: false,
      groupedInterpretationCallAudit: false,
      llm1PilotPromptSnapshot: false,
      llm1PilotResponseSchemaSnapshot: false,
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
    setEvidenceRefinementProgress(0);
    setGroupingPreparationProgress(0);
    setGroupedInterpretationProgress(0);
    setGroupedPrototypeProgress(0);
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
      if (isVariantEnrichmentStage(caught.stage)) {
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
          <button className="secondary-button small" type="button" onClick={() => setTier1CurationOpen(true)}>
            <ShieldCheck size={16} />
            {language === "es" ? "Curación Tier 1" : "Tier 1 curation"}
          </button>
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

      <Tier1CurationV2Modal open={tier1CurationOpen} onClose={() => setTier1CurationOpen(false)} language={language} />

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
        {isGeneModuleV2 && (
          <ProgressBar
            label={t.evidenceRefinementProgress}
            value={evidenceRefinementProgress}
            detail={stageProgressDetails.evidence_refinement || stageProgressDetails.evidence_refinement_quality_gate || ""}
            tone="green"
            downloadLabel={t.curatedPhysicalMatrixDownload}
            onDownload={matchResult?.jobId ? () => downloadMatchArtifact("curatedPhysicalMatrix") : null}
            downloadReady={matchArtifactsReady.curatedPhysicalMatrix}
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
            <ProgressBar
              label={t.groupedPrototypeProgress}
              value={groupedPrototypeProgress}
              detail={stageProgressDetails.grouped_prototype || ""}
              tone="green"
              downloadLabel={t.groupedPrototypePdf}
              onDownload={matchResult?.jobId ? () => downloadGroupedPrototypeArtifact("pdf", "HEAL_prototipo_desarrollo.pdf") : null}
              downloadReady={matchArtifactsReady.groupedPrototypePdf}
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
        <ExecutionLogPanel
          jobId={activeMatchJobId || matchResult?.jobId || null}
          logs={executionLogs}
          onDownload={activeMatchJobId || matchResult?.jobId ? downloadExecutionLogs : null}
          t={t}
          locale={locale}
        />
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
