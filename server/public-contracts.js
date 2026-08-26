import crypto from "node:crypto";


export function requestAccessToken(req = {}) {
  return String(req.headers?.["x-heal-access-token"] || req.body?.accessToken || req.query?.accessToken || "");
}


export function tokenMatches(expected, actual) {
  if (!expected || !actual) return false;
  const expectedBuffer = Buffer.from(String(expected));
  const actualBuffer = Buffer.from(String(actual));
  return expectedBuffer.length === actualBuffer.length && crypto.timingSafeEqual(expectedBuffer, actualBuffer);
}


export function cloneAndOmit(result, omittedKeys = []) {
  const publicResult = JSON.parse(JSON.stringify(result || {}));
  for (const key of omittedKeys) delete publicResult[key];
  return publicResult;
}


export function sanitizePublicResult(value, key = "") {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizePublicResult(item, key)).filter((item) => item !== undefined);
  }
  if (value && typeof value === "object") {
    const output = {};
    for (const [childKey, childValue] of Object.entries(value)) {
      if (/path$|paths$|raw_response|authorization|headers|stack|api_key|sha256|hash|stderr|stdout|response_body|provider_body/i.test(childKey)) continue;
      const sanitized = sanitizePublicResult(childValue, childKey);
      if (sanitized !== undefined) output[childKey] = sanitized;
    }
    return output;
  }
  if (typeof value === "string" && (/^[A-Za-z]:\\/.test(value) || value.includes("HEAL_OPENAI_API_KEY"))) {
    return undefined;
  }
  return value;
}
