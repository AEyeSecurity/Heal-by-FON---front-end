from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "server" / "public-contracts.js").as_uri()


def run_node(expression: str):
    script = f"""
      import {{ requestAccessToken, tokenMatches, cloneAndOmit, sanitizePublicResult }} from {json.dumps(MODULE)};
      const result = (() => {{ {expression} }})();
      process.stdout.write(JSON.stringify(result));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class ServerPublicContractTests(unittest.TestCase):
    def test_access_token_sources_have_stable_precedence(self):
        result = run_node("""
          return [
            requestAccessToken({headers: {'x-heal-access-token': 'header'}, body: {accessToken: 'body'}, query: {accessToken: 'query'}}),
            requestAccessToken({headers: {}, body: {accessToken: 'body'}, query: {accessToken: 'query'}}),
            requestAccessToken({headers: {}, body: {}, query: {accessToken: 'query'}}),
            requestAccessToken({}),
          ];
        """)
        self.assertEqual(result, ["header", "body", "query", ""])

    def test_access_token_comparison_rejects_missing_wrong_and_different_lengths(self):
        result = run_node("""
          return [
            tokenMatches('secret', 'secret'), tokenMatches('secret', 'wrong'),
            tokenMatches('secret', 'secret-long'), tokenMatches('', 'secret'), tokenMatches('secret', ''),
          ];
        """)
        self.assertEqual(result, [True, False, False, False, False])

    def test_public_sanitization_removes_internal_fields_and_secret_values(self):
        result = run_node("""
          return sanitizePublicResult({
            ok: true,
            outputPath: 'F:\\\\Heal by FON\\\\data\\\\runs\\\\private.json',
            nested: {
              sha256: 'abc', raw_response: {secret: true}, headers: {authorization: 'Bearer secret'},
              provider_body: 'private', safe: 'visible', secretValue: 'HEAL_OPENAI_API_KEY=hidden',
            },
            rows: [{safe: 1, stack: 'trace'}, {safe: 2, api_key: 'hidden'}],
          });
        """)
        self.assertEqual(result, {"ok": True, "nested": {"safe": "visible"}, "rows": [{"safe": 1}, {"safe": 2}]})

    def test_stage_sanitization_clones_and_omits_without_mutating_source(self):
        result = run_node("""
          const source = {status: 'valid', inputPath: 'private', outputDir: 'private', outputs: {raw: true}};
          const publicValue = cloneAndOmit(source, ['inputPath', 'outputDir', 'outputs']);
          return {publicValue, source};
        """)
        self.assertEqual(result["publicValue"], {"status": "valid"})
        self.assertIn("inputPath", result["source"])
        self.assertIn("outputs", result["source"])

    def test_grouped_endpoints_require_access_token_and_do_not_fallback(self):
        source = (ROOT / "server" / "dev-api.js").read_text(encoding="utf-8")
        start = source.index('app.post("/api/vcf-canon-matches/:jobId/grouped-prototype"')
        end = source.index('app.get("/api/vcf-canon-matches/:jobId/grouped-prototype/cards"', start)
        routes = source[start:end]
        self.assertGreaterEqual(routes.count("hasUploadAccessToken(req, upload)"), 2)
        self.assertNotIn("processGroupedIndividualInterpretation", routes)
        self.assertNotIn("HEAL_MECHANISM_REGISTRY_PATH", routes)


if __name__ == "__main__":
    unittest.main()
