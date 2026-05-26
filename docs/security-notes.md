# Security Notes

## Public vs Private Values

Public frontend values:

```text
VITE_API_BASE
VITE_TURNSTILE_SITE_KEY
```

These are safe to expose because they are embedded in the browser bundle by design.

Private backend values:

```text
HEAL_TURNSTILE_SECRET
HEAL_N8N_WEBHOOK_TOKEN
HEAL_N8N_UPLOAD_WEBHOOK_URL
HEAL_N8N_VALIDATION_WEBHOOK_URL
Cloudflare Tunnel token
```

These must stay outside GitHub and outside Cloudflare Pages public variables.

## Active Backend Controls

The API currently enforces:

- explicit allowed origins
- required `Origin` header on mutating requests
- Cloudflare Turnstile verification for upload init
- Turnstile hostname allowlist
- VCF-like file extensions only
- max upload size
- chunked upload protocol
- max active uploads per client fingerprint
- hourly upload init rate limit per IP
- isolated upload directories
- path containment checks before validation
- public response sanitization to avoid exposing server paths

## Origin and CORS

Allowed public frontend:

```text
https://healbyfon.aeye.com.ar
```

Direct scripted calls without an `Origin` header are rejected for mutating methods.

This is not a complete authentication layer, but it reduces accidental or unsophisticated abuse while Turnstile blocks automated browserless upload initiation.

## Turnstile

Turnstile is required for:

```text
POST /api/uploads/init
```

Allowed Turnstile hostname:

```text
healbyfon.aeye.com.ar
```

The secret key is read only from the backend environment.

## File Isolation

Uploads are stored under:

```text
C:\ServerCIT\services\heal-vcf-integrity\incoming\<uploadId>
```

The browser only receives:

- `uploadId`
- file name
- file size
- timestamps
- validation result

The browser should not receive absolute server paths.

## Duplicate Reuse Safety

Duplicate reuse is intentionally scoped to the same client fingerprint. The backend does not offer reuse of another user's upload even when file name and size match.

This is a practical prototype control, not a long-term identity model. A production multi-user system should replace this with authenticated users and explicit file ownership records.

## Known Security Gaps

- No user accounts yet.
- No formal audit log database yet.
- No antivirus/malware scanning yet.
- No WAF rule set documented specifically for this API yet.
- Tunnel token should be rotated before stable production.
- Persistence tasks need administrator installation.

## Recommended Next Hardening

1. Rotate the HEAL tunnel token.
2. Install persistent tasks for API and tunnel.
3. Add Cloudflare WAF/rate limits for `heal-api.aeye.com.ar`.
4. Add a lightweight upload database for durable ownership/state.
5. Add n8n webhook authentication.
6. Add formal retention policy for uploaded VCFs.
