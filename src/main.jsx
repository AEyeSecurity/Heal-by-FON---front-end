import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BarChart3,
  CheckCircle2,
  FileUp,
  Globe2,
  Loader2,
  Send,
  ShieldCheck,
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
    steps: ["Carga del VCF", "Validacion de integridad", "Analisis posterior"],
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
    variantLimit: "Variantes iniciales a revisar",
    uploadProgress: "Carga del archivo",
    validationProgress: "Validacion del VCF",
    submit: "Enviar y validar",
    securityCheck: "Verificacion de seguridad",
    securityCheckHelp: "Protege el backend antes de aceptar VCFs grandes.",
    securityRequired: "Completa la verificacion de seguridad antes de subir el archivo.",
    uploading: "Subiendo el archivo en partes a un espacio aislado...",
    validationStarting: "Iniciando validacion por streaming...",
    validating: "Validando",
    validationFailed: "La validacion fallo.",
    uploadFailed: "No se pudo completar la carga.",
    processFailed: "No se pudo completar el proceso.",
    complete: "Validacion finalizada.",
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
  },
  en: {
    languageLabel: "Language",
    langEs: "Espanol",
    langEn: "English",
    eyebrow: "HEAL by FON",
    title: "VCF Integrity Check",
    lede: "Upload a VCF or VCF.GZ file to validate structure, headers, first variants, and technical metrics.",
    pipelineLabel: "Pipeline",
    steps: ["VCF upload", "Integrity validation", "Downstream analysis"],
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
    variantLimit: "Initial variants to inspect",
    uploadProgress: "File upload",
    validationProgress: "VCF validation",
    submit: "Send and validate",
    securityCheck: "Security check",
    securityCheckHelp: "Protects the backend before accepting large VCF files.",
    securityRequired: "Complete the security check before uploading the file.",
    uploading: "Uploading the file in chunks into an isolated workspace...",
    validationStarting: "Starting streaming validation...",
    validating: "Validating",
    validationFailed: "Validation failed.",
    uploadFailed: "Could not complete the upload.",
    processFailed: "Could not complete the process.",
    complete: "Validation finished.",
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

function ProgressBar({ label, value, tone = "blue" }) {
  return (
    <div className="progress-block">
      <div className="progress-row">
        <span>{label}</span>
        <strong>{Math.round(value)}%</strong>
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
    { key: "analysis", label: t.steps[2] },
  ];
  const activeIndex = phase === "uploading" ? 0 : phase === "validating" ? 1 : phase === "done" ? 1 : 0;
  const completeIndex = phase === "done" ? 1 : phase === "validating" ? 0 : -1;

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

function ResultPanel({ result, analysisMode, locale, t }) {
  if (!result) return null;

  const isValid = result.status === "valid";
  const isWarning = result.status === "warning";
  const Icon = isValid || isWarning ? CheckCircle2 : XCircle;
  const stats = result.variant_stats?.counts || {};
  const topChromosomes = result.variant_stats?.top_chromosomes || [];
  const hasFullStats = analysisMode === "complete" && result.variant_stats?.status === "calculated";

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
          <p>{result.metadata?.path}</p>
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

function App() {
  const fileInputRef = useRef(null);
  const [language, setLanguage] = useState("es");
  const [analysisMode, setAnalysisMode] = useState("quick");
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [validationProgress, setValidationProgress] = useState(0);
  const [maxVariants, setMaxVariants] = useState(20);
  const [phase, setPhase] = useState("idle");
  const [messageKey, setMessageKey] = useState("initialMessage");
  const [customMessage, setCustomMessage] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [turnstileToken, setTurnstileToken] = useState("");
  const [turnstileResetKey, setTurnstileResetKey] = useState(0);

  const t = COPY[language];
  const locale = language === "es" ? "es-AR" : "en-US";
  const canSend = useMemo(() => file && !["uploading", "validating"].includes(phase), [file, phase]);
  const statusMessage = customMessage || t[messageKey] || t.initialMessage;

  function pickFile(nextFile) {
    setFile(nextFile || null);
    setUploadProgress(0);
    setValidationProgress(0);
    setResult(null);
    setError(null);
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

  async function pollValidation(jobId) {
    for (;;) {
      const response = await fetch(`${API_BASE}/api/validations/${jobId}`);
      if (!response.ok) throw new Error(await response.text());
      const job = await response.json();
      setValidationProgress(job.progress || 0);
      setCustomMessage(job.message || t.validating);

      if (job.status === "complete") return job.result;
      if (job.status === "failed") throw new Error(job.error || t.validationFailed);
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }

  async function submit() {
    if (!file) return;
    if (TURNSTILE_SITE_KEY && !turnstileToken) {
      setError(t.securityRequired);
      return;
    }
    const variantLimit = clampVariantCount(maxVariants);
    const shouldCalculateStats = analysisMode === "complete";
    setMaxVariants(variantLimit);
    setError(null);
    setResult(null);
    setPhase("uploading");
    setMessageKey("uploading");
    setCustomMessage("");
    setUploadProgress(0);
    setValidationProgress(0);

    try {
      const upload = await uploadFile(file);
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
        }),
      });
      if (!validationStart.ok) throw new Error(await validationStart.text());
      const job = await validationStart.json();
      const validationResult = await pollValidation(job.id);

      setResult(validationResult);
      setPhase("done");
      setMessageKey("complete");
      setCustomMessage("");
      setValidationProgress(100);
      setTurnstileToken("");
      setTurnstileResetKey((current) => current + 1);
    } catch (caught) {
      setPhase("error");
      setError(caught.message || String(caught));
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
        <label className="language-control">
          <Globe2 size={17} />
          <span>{t.languageLabel}</span>
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="es">{t.langEs}</option>
            <option value="en">{t.langEn}</option>
          </select>
        </label>
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
          {phase === "validating" ? <Loader2 className="spin" size={20} /> : <ShieldCheck size={20} />}
          <span>{statusMessage}</span>
        </div>

        <ModeSelector mode={analysisMode} setMode={setAnalysisMode} t={t} />
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

        <ProgressBar label={t.uploadProgress} value={uploadProgress} tone="green" />
        <ProgressBar label={t.validationProgress} value={validationProgress} tone="blue" />
        {error && <p className="error-message">{error}</p>}
        <button className="primary-button" type="button" disabled={!canSend} onClick={submit}>
          <Send size={18} />
          {t.submit}
        </button>
      </section>

      <ResultPanel result={result} analysisMode={analysisMode} locale={locale} t={t} />
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
