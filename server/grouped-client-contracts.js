const LEGACY_AUTHORITY = /\b(?:legacy\s+(?:payload|mechanism)|mechanism\s+(?:registry\s+)?(?:status\s+)?(?:is\s+)?(?:draft|withheld)|(?:estado\s+del\s+)?registro\s+(?:del\s+)?mecan(?:i|í)sm(?:o|ico)\s+(?:es|est(?:a|á))\s+(?:draft|borrador|withheld|retenido)|mecanismo\s+(?:est(?:a|á)\s+)?(?:en\s+)?(?:draft|borrador|withheld|retenido)|mechanism\s+not\s+usable)\b/i;

export function cleanClientText(value) {
  return String(value || "")
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence && !LEGACY_AUTHORITY.test(sentence))
    .join(" ")
    .replace(/\bnot_reported\b/gi, "sin clasificación reportada")
    .replace(/\bbenigna_o_probablemente_benigna\b/gi, "benigna o probablemente benigna")
    .replace(/\bbenign_or_likely_benign\b/gi, "benign or likely benign")
    .replace(/\brisk_factor\b/gi, "factor de riesgo")
    .replace(/\bcontext_only\b/gi, "interpretación contextual");
}

export function projectLegacyCardsForClient(cards = []) {
  return cards.map((card) => ({
    group_id: card.group_id,
    gene: card.gene,
    module_id: card.module_id,
    module_name: card.module_name || "",
    status: card.status,
    coverage_status: card.coverage_status,
    inference_mode: card.inference_mode,
    inference_mode_label_es: card.inference_mode === "initial_guide" ? "Guía inicial acotada" :
      card.inference_mode === "context_only" ? "Interpretación contextual" : "Sin inferencia individual",
    scientific_confidence: card.final_confidence_level,
    scientific_confidence_label_es: card.final_confidence_level === "Low" ? "Baja" :
      card.final_confidence_level === "Moderate" ? "Moderada" :
      card.final_confidence_level === "High" ? "Alta" : card.final_confidence_level || "No informada",
    interpretation_es: {
      summary: cleanClientText(card.interpretation_one_sentence_es),
      detail: cleanClientText(card.interpretation_long_es),
    },
    observed_variant_refs: Array.isArray(card.focus_variant_refs) ? card.focus_variant_refs : [],
    evidence_summary: {
      record_count: Array.isArray(card.evidence_used) ? card.evidence_used.length : 0,
      sources: [...new Set((card.evidence_used || []).map((row) => row.source).filter(Boolean))].sort(),
    },
    limitations_es: card.status === "covered_no_observed_variant" ? [
      "La entrada contiene variantes observadas; una ausencia no demuestra homocigosis de referencia ni capacidad de llamada confirmada.",
    ] : [],
    prioritization: { prioritized: false, rank: null },
    input_completeness_label_es: "VCF de variantes observadas; capacidad de llamada no confirmada",
  }));
}

export function groupedClientSummary(grouped = {}) {
  const counts = grouped.counts || {};
  const canonical = Number(counts.canonical_groups || 180);
  const covered = Number(counts.scientifically_covered || 0);
  const valid = Number(counts.valid_interpretation_count ?? counts.valid_llm1_cards ?? 0);
  const noObserved = Number(counts.covered_no_observed_variant || 0);
  const uncovered = Number(counts.not_covered || 0);
  return {
    processing: { status: "completed", label_es: "Procesamiento completado" },
    externalEvidence: {
      status: grouped.external_evidence_partial ? "partial" : "complete",
      label_es: grouped.external_evidence_partial
        ? "Algunas fuentes externas no estuvieron completamente disponibles"
        : "Fuentes externas disponibles para esta ejecución",
    },
    coverage: {
      canonical_group_count: canonical,
      scientifically_covered_count: covered,
      valid_interpretation_count: valid,
      covered_no_observed_variant_count: noObserved,
      not_covered_count: uncovered,
      prioritized_finding_count: Number(counts.prioritized_finding_count || 0),
    },
    coverageConsistent: covered + uncovered === canonical && valid + noObserved === covered,
    prototypeLabel: "Prototipo de desarrollo",
    formalValidationLabel: "Validación formal pendiente de un nuevo holdout independiente",
  };
}
