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

const COPY = {
  es: {
    languageLabel: "Idioma",
    langEs: "Espanol",
    langEn: "English",
    eyebrow: "HEAL by FON",
    title: "VCF Integrity Check",
    lede: "Carga un archivo VCF o VCF.GZ para validar estructura, headers, primeras variantes y metricas tecnicas.",
    pipelineLabel: "Pipeline",
    steps: ["Carga del VCF", "Validacion de integridad", "Match VCF-Canon", "Analisis posterior"],
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
    preparationProgress: "Preparacion del match",
    enrichmentProgress: "Enriquecimiento externo",
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
    matchStarting: "Iniciando match VCF-Canon...",
    matching: "Matcheando VCF contra canon...",
    preparing: "Preparando CSVs de auditoria...",
    enriching: "Enriqueciendo variantes observadas...",
    enrichmentFailed: "No se pudo completar el enriquecimiento externo.",
    matchFailed: "No se pudo completar el match VCF-Canon.",
    validationFailed: "La validacion fallo.",
    uploadFailed: "No se pudo completar la carga.",
    processFailed: "No se pudo completar el proceso.",
    complete: "Validacion finalizada.",
    matchComplete: "Match VCF-Canon finalizado.",
    preparationComplete: "Preparacion del match finalizada.",
    enrichmentComplete: "Enriquecimiento externo finalizado.",
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
    enrichmentTitle: "Enriquecimiento de variantes observadas",
    preparationRows: "Filas preparadas",
    preparationObserved: "Con genotipo observado",
    preparationHigh: "Confianza alta",
    preparationModerate: "Confianza moderada",
    preparationLow: "Confianza baja",
    enrichmentObserved: "Filas enriquecidas",
    enrichmentUniqueRsids: "rsIDs unicos",
    enrichmentCacheHits: "Cache hits",
    enrichmentSourceErrors: "Errores de fuentes",
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
    canonLoaded: "Canon cargado",
    canonRows: "Filas no vacias",
    canonUniqueRsids: "rsIDs unicos",
    canonRepeatedRsids: "rsIDs repetidos",
    canonManualReview: "Revision manual",
    canonPreview: "Vista previa limpia",
    canonDownload: "Descargar canon completo",
    rsidMasterDownload: "Descargar rsID master",
    matchDownload: "Descargar CSV de matches",
    matchPreparationAuditDownload: "Descargar CSV preparado",
    matchPreparationMinimalDownload: "Descargar CSV minimo",
    enrichmentDownload: "Descargar CSV interpretativo",
    enrichmentQaDownload: "Descargar CSV tecnico QA",
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
    title: "VCF Integrity Check",
    lede: "Upload a VCF or VCF.GZ file to validate structure, headers, first variants, and technical metrics.",
    pipelineLabel: "Pipeline",
    steps: ["VCF upload", "Integrity validation", "VCF-Canon match", "Downstream analysis"],
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
    preparationProgress: "Match preparation",
    enrichmentProgress: "External enrichment",
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
    matchStarting: "Starting VCF-Canon match...",
    matching: "Matching VCF against canon...",
    preparing: "Preparing audit CSVs...",
    enriching: "Enriching observed variants...",
    enrichmentFailed: "Could not complete external enrichment.",
    matchFailed: "Could not complete the VCF-Canon match.",
    validationFailed: "Validation failed.",
    uploadFailed: "Could not complete the upload.",
    processFailed: "Could not complete the process.",
    complete: "Validation finished.",
    matchComplete: "VCF-Canon match finished.",
    preparationComplete: "Match preparation finished.",
    enrichmentComplete: "External enrichment finished.",
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
    enrichmentTitle: "Observed variant enrichment",
    preparationRows: "Prepared rows",
    preparationObserved: "Observed genotypes",
    preparationHigh: "High confidence",
    preparationModerate: "Moderate confidence",
    preparationLow: "Low confidence",
    enrichmentObserved: "Enriched rows",
    enrichmentUniqueRsids: "Unique rsIDs",
    enrichmentCacheHits: "Cache hits",
    enrichmentSourceErrors: "Source errors",
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
    canonLoaded: "Canon loaded",
    canonRows: "Non-empty rows",
    canonUniqueRsids: "Unique rsIDs",
    canonRepeatedRsids: "Repeated rsIDs",
    canonManualReview: "Manual review",
    canonPreview: "Clean preview",
    canonDownload: "Download full canon",
    rsidMasterDownload: "Download rsID master",
    matchDownload: "Download matches CSV",
    matchPreparationAuditDownload: "Download prepared CSV",
    matchPreparationMinimalDownload: "Download minimal CSV",
    enrichmentDownload: "Download interpretive CSV",
    enrichmentQaDownload: "Download technical QA CSV",
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

function ProgressBar({
  label,
  value,
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

function PipelineStepper({ phase, t }) {
  const steps = [
    { key: "upload", label: t.steps[0] },
    { key: "validation", label: t.steps[1] },
    { key: "match", label: t.steps[2] },
    { key: "analysis", label: t.steps[3] },
  ];
  const activeIndex =
    phase === "uploading"
      ? 0
      : phase === "validating"
        ? 1
        : phase === "matching" || phase === "preparing" || phase === "enriching" || phase === "done"
          ? 2
          : 0;
  const completeIndex =
    phase === "done"
      ? 2
      : phase === "matching" || phase === "preparing" || phase === "enriching"
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

function ErrorDialog({ message, onClose, t }) {
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
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [canonProgress, setCanonProgress] = useState(0);
  const [error, setError] = useState(null);
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
      setCanonProgress(0);
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    }
  }, [open]);

  function postCanonWithProgress(selectedFile) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let processingTimer = null;
      const clearProcessingTimer = () => {
        if (processingTimer) {
          window.clearInterval(processingTimer);
          processingTimer = null;
        }
      };

      xhr.open("POST", `${API_BASE}/api/canon/upload`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.setRequestHeader("X-Canon-File-Name", encodeURIComponent(selectedFile.name));
      xhr.setRequestHeader("X-Turnstile-Token", turnstileToken);

      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const uploadRatio = event.loaded / event.total;
        setCanonProgress(Math.max(5, Math.min(50, Math.round(uploadRatio * 50))));
      };

      xhr.upload.onload = () => {
        setCanonProgress((current) => Math.max(current, 55));
        processingTimer = window.setInterval(() => {
          setCanonProgress((current) => Math.min(92, current + 4));
        }, 450);
      };

      xhr.onload = () => {
        clearProcessingTimer();
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
        clearProcessingTimer();
        reject(new Error("Could not upload canon."));
      };
      xhr.onabort = () => {
        clearProcessingTimer();
        reject(new Error("Canon upload was aborted."));
      };

      setCanonProgress(5);
      xhr.send(selectedFile);
    });
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
      setCanonState(payload);
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
  const sourceGroups = current?.metadata?.source_group_counts || {};
  const loadedAt = current?.createdAt || current?.timestamps?.completedAt;

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
                <MetricCard label={t.canonRows} value={formatNumber(current.metadata?.rows_nonempty, locale)} />
                <MetricCard label={t.canonUniqueRsids} value={formatNumber(current.metadata?.unique_rsids, locale)} />
                <MetricCard label={t.canonRepeatedRsids} value={formatNumber(current.metadata?.duplicate_rsids, locale)} />
                <MetricCard label={t.canonManualReview} value={formatNumber(sourceGroups.revision_manual || 0, locale)} />
              </div>
              <button className="secondary-button canon-download-button" type="button" onClick={downloadCanon}>
                <Download size={17} />
                {t.canonDownload}
              </button>
              <button className="secondary-button canon-download-button" type="button" onClick={downloadRsidMaster}>
                <Download size={17} />
                {t.rsidMasterDownload}
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
          <TurnstileBox
            siteKey={TURNSTILE_SITE_KEY}
            language={language}
            onToken={setTurnstileToken}
            resetKey={turnstileResetKey}
            t={t}
          />
          {error && <p className="error-message">{error}</p>}
          {(uploading || canonProgress > 0) && <ProgressBar label={t.canonProgress} value={canonProgress} tone="green" />}
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
                  <th>row_id</th>
                  <th>source_group</th>
                  <th>category</th>
                  <th>gene</th>
                  <th>rsid</th>
                  <th>effect</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 60).map((row) => (
                  <tr key={row.row_id}>
                    <td>{row.row_id}</td>
                    <td>{row.source_group}</td>
                    <td>{row.category || "-"}</td>
                    <td>{row.gene || "-"}</td>
                    <td>{row.rsid || "-"}</td>
                    <td>{row.effect || "-"}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan="6">{t.canonNone}</td>
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
  const metadata = result.metadata || {};
  const statusCounts = metadata.match_status_counts || {};
  const preparation = result.matchPreparation?.metadata || {};
  const confidenceCounts = preparation.confidence_level_counts || {};
  const enrichment = result.variantEnrichment?.metadata || {};
  const enrichmentSourceErrors = Object.values(enrichment.source_error_counts || {}).reduce(
    (total, value) => total + Number(value || 0),
    0,
  );
  const fileLabel = metadata.file_name || metadata.upload_id || "";
  const cards = [
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
    ? [
        [t.preparationRows, formatNumber(preparation.rows_total, locale)],
        [t.preparationObserved, formatNumber(preparation.rows_with_genotype, locale)],
        [t.preparationHigh, formatNumber(confidenceCounts.High || 0, locale)],
        [t.preparationModerate, formatNumber(confidenceCounts.Moderate || 0, locale)],
        [t.preparationLow, formatNumber(confidenceCounts.Low || 0, locale)],
      ]
    : [];
  const enrichmentCards = result.variantEnrichment
    ? [
        [t.enrichmentObserved, formatNumber(enrichment.output_rows, locale)],
        [t.enrichmentUniqueRsids, formatNumber(enrichment.unique_rsids, locale)],
        [t.enrichmentCacheHits, formatNumber(enrichment.cache_hits, locale)],
        [t.enrichmentSourceErrors, formatNumber(enrichmentSourceErrors, locale)],
      ]
    : [];

  async function downloadCsv(endpoint, fallbackName) {
    if (!result.jobId) return;
    setDownloadError(null);
    try {
      const response = await fetch(`${API_BASE}${endpoint}`);
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

  async function downloadEnrichment() {
    await downloadCsv(
      `/api/vcf-canon-matches/${result.jobId}/enrichment-interpretive`,
      "heal-fon-interpretation-enriched-observed69.csv",
    );
  }

  async function downloadEnrichmentQa() {
    await downloadCsv(`/api/vcf-canon-matches/${result.jobId}/enrichment`, "heal-observed-variant-enrichment.csv");
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
      <div className="match-download-actions">
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={downloadMatches}>
          <Download size={17} />
          {t.matchDownload}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={downloadPreparedAudit}>
          <Download size={17} />
          {t.matchPreparationAuditDownload}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={downloadPreparedMinimal}>
          <Download size={17} />
          {t.matchPreparationMinimalDownload}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={downloadEnrichment}>
          <Download size={17} />
          {t.enrichmentDownload}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={downloadEnrichmentQa}>
          <Download size={17} />
          {t.enrichmentQaDownload}
        </button>
      </div>
      <h3 className="result-subtitle">{t.debugDownloads}</h3>
      <div className="match-download-actions">
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={() => downloadDebugArtifact("vcf_candidates", "heal-vcf-candidates.csv")}>
          <Download size={17} />
          {t.qaVcfCandidates}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={() => downloadDebugArtifact("vcf_joined_chr_pos", "heal-vcf-joined-chr-pos.csv")}>
          <Download size={17} />
          {t.qaVcfJoined}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={() => downloadDebugArtifact("match_strict", "heal-match-strict.csv")}>
          <Download size={17} />
          {t.qaStrict}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={() => downloadDebugArtifact("alt_review", "heal-match-alt-review.csv")}>
          <Download size={17} />
          {t.qaAltReview}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={() => downloadDebugArtifact("position_review", "heal-match-position-review.csv")}>
          <Download size={17} />
          {t.qaPositionReview}
        </button>
        <button className="secondary-button match-download-button" type="button" disabled={!result.jobId} onClick={() => downloadDebugArtifact("no_vcf_match", "heal-match-no-vcf-match.csv")}>
          <Download size={17} />
          {t.qaNoVcfMatch}
        </button>
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
  const [file, setFile] = useState(null);
  const [uploadRecord, setUploadRecord] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [validationProgress, setValidationProgress] = useState(0);
  const [matchProgress, setMatchProgress] = useState(0);
  const [preparationProgress, setPreparationProgress] = useState(0);
  const [enrichmentProgress, setEnrichmentProgress] = useState(0);
  const [maxVariants, setMaxVariants] = useState(20);
  const [phase, setPhase] = useState("idle");
  const [messageKey, setMessageKey] = useState("initialMessage");
  const [customMessage, setCustomMessage] = useState("");
  const [result, setResult] = useState(null);
  const [matchResult, setMatchResult] = useState(null);
  const [matchArtifactsReady, setMatchArtifactsReady] = useState({
    matches: false,
    debug: false,
    preparation: false,
    enrichment: false,
    enrichmentInterpretive: false,
  });
  const [error, setError] = useState(null);
  const [errorDialog, setErrorDialog] = useState(null);
  const [turnstileToken, setTurnstileToken] = useState("");
  const [turnstileResetKey, setTurnstileResetKey] = useState(0);
  const [duplicateCandidate, setDuplicateCandidate] = useState(null);
  const [canonOpen, setCanonOpen] = useState(false);

  const t = COPY[language];
  const locale = language === "es" ? "es-AR" : "en-US";
  const canSend = useMemo(
    () => file && !["uploading", "validating", "matching", "preparing", "enriching"].includes(phase),
    [file, phase],
  );
  const statusMessage = customMessage || t[messageKey] || t.initialMessage;

  function pickFile(nextFile) {
    setFile(nextFile || null);
    setUploadProgress(0);
    setValidationProgress(0);
    setMatchProgress(0);
    setPreparationProgress(0);
    setEnrichmentProgress(0);
    setResult(null);
    setMatchResult(null);
    setMatchArtifactsReady({
      matches: false,
      debug: false,
      preparation: false,
      enrichment: false,
      enrichmentInterpretive: false,
    });
    setError(null);
    setErrorDialog(null);
    setDuplicateCandidate(null);
    setUploadRecord(null);
    setPhase("idle");
    setCustomMessage("");
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
    const response = await fetch(`${API_BASE}${endpoint}`);
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

  async function downloadMatchArtifact(kind) {
    if (!matchResult?.jobId) return;
    setError(null);
    try {
      if (kind === "matches") {
        await downloadCsv(`/api/vcf-canon-matches/${matchResult.jobId}/download`, "heal-vcf-canon-matches.csv");
      } else if (kind === "preparation") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/preparation-audit`,
          "heal-match-preparation-audit.csv",
        );
      } else if (kind === "enrichment") {
        await downloadCsv(
          `/api/vcf-canon-matches/${matchResult.jobId}/enrichment-interpretive`,
          "heal-fon-interpretation-enriched-observed69.csv",
        );
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
        },
        body: chunk,
      });
      const chunkResult = await readJsonResponse(chunkResponse);
      if (!chunkResponse.ok) throw new Error(chunkResult.error || t.uploadFailed);
      setUploadProgress(Math.round(((chunkIndex + 1) / totalChunks) * 96));
    }

    const completeResponse = await fetch(`${API_BASE}/api/uploads/${initUpload.uploadId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const completeUpload = await readJsonResponse(completeResponse);
    if (!completeResponse.ok) throw new Error(completeUpload.error || t.uploadFailed);
    setUploadProgress(100);
    return completeUpload;
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
    for (;;) {
      const response = await fetch(`${API_BASE}/api/validations/${jobId}`);
      if (!response.ok) throw new Error(await response.text());
      const job = await response.json();
      setValidationProgress(job.progress || 0);
      setCustomMessage(job.message || t.validating);

      if (job.status === "complete") return { ...(job.result || {}), jobId: job.id };
      if (job.status === "failed") throw new Error(job.error || t.validationFailed);
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }

  function updateMatchSnapshot(job) {
    const ready = job.artifactsReady || {};
    setMatchArtifactsReady({
      matches: Boolean(ready.matches),
      debug: Boolean(ready.debug),
      preparation: Boolean(ready.preparation),
      enrichment: Boolean(ready.enrichment),
      enrichmentInterpretive: Boolean(ready.enrichmentInterpretive),
    });
    if (job.result || ready.matches || ready.preparation || ready.enrichment || ready.enrichmentInterpretive) {
      setMatchResult({
        ...(job.result || {}),
        jobId: job.id,
        artifactsReady: ready,
      });
    }
  }

  async function pollMatch(jobId) {
    for (;;) {
      const response = await fetch(`${API_BASE}/api/vcf-canon-matches/${jobId}`);
      if (!response.ok) throw new Error(await response.text());
      const job = await response.json();
      updateMatchSnapshot(job);
      setMatchProgress(job.progress || 0);
      if (job.stage === "preparing") {
        setPhase("preparing");
        setMatchProgress(100);
        setPreparationProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.preparing);
      } else if (job.stage === "enriching") {
        setPhase("enriching");
        setMatchProgress(100);
        setPreparationProgress(100);
        setEnrichmentProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.enriching);
      } else {
        setMatchProgress(job.stageProgress ?? job.progress ?? 0);
        setCustomMessage(job.message || t.matching);
      }

      if (job.status === "complete") {
        setPreparationProgress(100);
        setEnrichmentProgress(100);
        return { ...(job.result || {}), jobId: job.id, artifactsReady: job.artifactsReady || {} };
      }
      if (job.status === "failed") {
        const failed = new Error(job.error || (job.stage === "enriching" ? t.enrichmentFailed : t.matchFailed));
        failed.stage = job.stage;
        failed.jobId = job.id;
        failed.artifactsReady = job.artifactsReady || {};
        failed.result = job.result || null;
        throw failed;
      }
      await new Promise((resolve) => setTimeout(resolve, 900));
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
          setPreparationProgress(0);
          setEnrichmentProgress(0);
          setMatchArtifactsReady({
            matches: false,
            debug: false,
            preparation: false,
            enrichment: false,
            enrichmentInterpretive: false,
          });
          setDuplicateCandidate(existingUpload);
          setMessageKey("fileReady");
          return null;
        }
      }
      upload = await uploadFile(file);
    }
    setUploadRecord(upload);
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
    setPreparationProgress(0);
    setEnrichmentProgress(0);

    const matchStart = await fetch(`${API_BASE}/api/vcf-canon-matches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uploadId: upload.uploadId, vcfParser }),
    });
    if (!matchStart.ok) throw new Error(await matchStart.text());
    const matchJob = await matchStart.json();
    const nextMatchResult = await pollMatch(matchJob.id);
    setMatchResult(nextMatchResult);
    setPhase("done");
    setMessageKey("preparationComplete");
    setCustomMessage("");
    setMatchProgress(100);
    setPreparationProgress(100);
    setEnrichmentProgress(100);
    return nextMatchResult;
  }

  async function runQaUpload() {
    setError(null);
    setErrorDialog(null);
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
      } else {
        setError(caught.message || String(caught));
      }
      setMessageKey("processFailed");
      setCustomMessage("");
    }
  }

  async function submit({ skipDuplicateCheck = false, reuseUpload = null } = {}) {
    if (!file) return;
    setError(null);
    setErrorDialog(null);
    setResult(null);
    setMatchResult(null);
    setMatchArtifactsReady({
      matches: false,
      debug: false,
      preparation: false,
      enrichment: false,
      enrichmentInterpretive: false,
    });
    setUploadProgress(0);
    setValidationProgress(0);
    setMatchProgress(0);
    setPreparationProgress(0);
    setEnrichmentProgress(0);
    setUploadRecord(null);

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
      await runMatch(upload);
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    } catch (caught) {
      setPhase("error");
      if (caught.stage === "enriching") {
        setError(t.enrichmentFailed);
        setErrorDialog(true);
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
          {phase === "validating" || phase === "matching" || phase === "preparing" || phase === "enriching" ? (
            <Loader2 className="spin" size={20} />
          ) : (
            <ShieldCheck size={20} />
          )}
          <span>{statusMessage}</span>
        </div>

        <ModeSelector mode={analysisMode} setMode={setAnalysisMode} t={t} />
        {analysisMode === "qa" && (
          <label className="parser-control">
            <span>{t.parserLabel}</span>
            <select value={vcfParser} onChange={(event) => setVcfParser(event.target.value)}>
              <option value="streaming">{t.parserStreaming}</option>
              <option value="pysam">{t.parserPysam}</option>
            </select>
            <small>{t.parserHelp}</small>
          </label>
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
          playDisabled={!file || ["uploading", "validating", "matching", "preparing", "enriching"].includes(phase)}
        />
        <ProgressBar
          label={t.validationProgress}
          value={validationProgress}
          tone="blue"
          onPlay={analysisMode === "qa" ? runQaValidation : null}
          playLabel={`${t.playStage}: ${t.validationProgress}`}
          playDisabled={!file || ["uploading", "validating", "matching", "preparing", "enriching"].includes(phase)}
        />
        <ProgressBar
          label={t.matchProgress}
          value={matchProgress}
          tone="blue"
          downloadLabel={t.matchDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("matches") : null}
          downloadReady={matchArtifactsReady.matches}
          onPlay={analysisMode === "qa" ? runQaMatch : null}
          playLabel={`${t.playStage}: ${t.matchProgress}`}
          playDisabled={!uploadRecord || !result || result.status === "invalid" || ["uploading", "validating", "matching", "preparing", "enriching"].includes(phase)}
        />
        <ProgressBar
          label={t.preparationProgress}
          value={preparationProgress}
          tone="blue"
          downloadLabel={t.matchPreparationAuditDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("preparation") : null}
          downloadReady={matchArtifactsReady.preparation}
          onPlay={analysisMode === "qa" ? runQaMatch : null}
          playLabel={`${t.playStage}: ${t.preparationProgress}`}
          playDisabled={!uploadRecord || !result || result.status === "invalid" || ["uploading", "validating", "matching", "preparing", "enriching"].includes(phase)}
        />
        <ProgressBar
          label={t.enrichmentProgress}
          value={enrichmentProgress}
          tone="blue"
          downloadLabel={t.enrichmentDownload}
          onDownload={matchResult?.jobId ? () => downloadMatchArtifact("enrichment") : null}
          downloadReady={matchArtifactsReady.enrichmentInterpretive}
          onPlay={analysisMode === "qa" ? runQaMatch : null}
          playLabel={`${t.playStage}: ${t.enrichmentProgress}`}
          playDisabled={!uploadRecord || !result || result.status === "invalid" || ["uploading", "validating", "matching", "preparing", "enriching"].includes(phase)}
        />
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
      <ErrorDialog message={errorDialog} onClose={() => setErrorDialog(null)} t={t} />
      <CanonModal open={canonOpen} onClose={() => setCanonOpen(false)} language={language} locale={locale} t={t} />
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
