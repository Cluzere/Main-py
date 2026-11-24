# AI HANDOFF — CURRENT DISCORD BOT AUDIT
Last updated: 2026-11-25 (UTC)
Repository: `Cluzere/Main-py`
Active audit branch: `audit-recovery-gpt-2026-11-23`

> Purpose: durable project memory for future ChatGPT sessions. Read this file before inspecting or changing code. This document concerns the current Discord bot security-audit work on the audit branch. Do not assume it is the same project as any older CLOUZER handoff document in the repository.

## 1. Non-negotiable working rules

1. **Do not modify the production source.** The repository's original source file is `main (4).py` (identical to the default `main` branch); the hosting copy is referred to as `main.py`. All current work is isolated under `audit-recovery/` and tests on the audit branch.
2. Do not rename the project, bot, model, provider, identifiers, or configuration based on guesses. Preserve exact existing names.
3. Do not replace or rewrite the whole bot when a targeted patch is sufficient. Preserve working features and behavior.
4. Do not claim a test passed unless it was actually run and its result inspected. Distinguish source inspection, unit tests, CI, staging, and live production verification.
5. Do not claim the bot is “100% secure”. Explain residual risks and evidence.
6. User prefers Indonesian, casual/direct wording, concise explanations, no unnecessary questions, and autonomous execution of appropriate safe tests.
7. Never request or expose API keys, Discord tokens, Cloudflare tokens, or contents of `.env` in chat, commits, screenshots, logs, or this handoff. Use environment variables securely.
8. Do not run destructive or unauthorized tests against Discord, providers, hosting, or third-party services. Use local unit tests/mocks for adversarial cases; live integration tests only against the user's authorized staging environment.

## 2. Repository and current audit state

- Repository: `Cluzere/Main-py`
- Default branch: `main`
- Audit branch: `audit-recovery-gpt-2026-11-23`
- Current audit candidate: `audit-recovery/review-round-2/revisi_audit_final_main.py`
- Candidate-specific tests: `tests/test_audit_candidate_archives.py`
- Existing regression suite: `audit-recovery/review-round-2/test_audit_regressions.py`
- Current report: `audit-recovery/review-round-2/FINAL_AUDIT_REPORT.md`
- README: `audit-recovery/review-round-2/README.md`

### Current verified file blob SHAs

- Candidate: `ed97417c86ddccc736efdac67c8694bdf80ea941` (282,602 bytes; 5,656 lines)
- Candidate tests: `b3e2fc67db099f2a3a99287851829c682ac45fb5` (50,312 bytes; 1,374 lines)
- Existing regression tests: `5a735dc40bdf4c8504f4d7bf180e819004f40592` (38,349 bytes; 901 lines)
- Audit report: `5d4832099503f0e865f28042002a0cee90fc33ef`
- Original source `main (4).py`: `ffffd0c17169b29504f4a8713dca396cf61ddeff` on both default `main` and audit branch; unchanged.

### Current branch and CI

- Branch head at handoff refresh: `610e115a84b24e467cfb935cd3ccf312e48625a8`
- Candidate source's latest code commit: `20f7d35e93c1e3de9b19fdfa8398d6e2373ced1a`
- Latest verified candidate CI: run `45514190007`, SUCCESS.
- Candidate suite: 56/56 PASS; existing regression suite: 74/74 PASS; total 130 tests.
- Python 3.12 compile, `pip check`, Bandit medium/high severity, and `pip-audit --strict`: PASS; no known dependency vulnerabilities found.
- Changes remain confined to audit candidate/tests/docs. The original source blob above is identical across branches.

## 3. What this bot/audit is trying to protect

Treat the bot as a multi-surface Discord application. The threat model includes:
- hostile or malformed Discord messages and attachments;
- oversized or malformed documents, archives, images, audio, and parser inputs;
- high-volume users attempting to exhaust provider quotas, CPU, RAM, or concurrency;
- malicious instructions embedded in uploaded documents, OCR, transcripts, web pages, or quoted content (prompt injection);
- invalid, oversized, duplicated, or excessive provider SSE events and function calls;
- unauthorized or stale button interactions and command permission mistakes;
- accidental secret exposure through logs, exceptions, state files, or provider error bodies;
- persistence failures, corrupt state, concurrent writes, and restart behavior;
- unsafe URL fetches, redirects, DNS changes, private-network access, or untrusted remote content.

Security goal: defense in depth. One failed control must not automatically expose secrets, authorize a user, permit arbitrary tool execution, or take down the entire bot.

## 4. Existing hardening already present in the audit candidate

The candidate contains earlier security work, including:
- fail-closed memory/state loading and validation; atomic writes, restrictive file permissions, and bounded state;
- secret-redaction filter for logs and tracebacks;
- attachment metadata checks, attachment count/total limits, concurrency limits, signature checks, and bounded streaming reads;
- ZIP archive preflight limits for entry count, per-entry size, total expanded size, and compression ratio;
- XML protections against external entities, parameter entities, and entity expansion;
- image pixel-count protections before/after image decode/reopen;
- bounded diagnostic HTTP response reading;
- global persisted daily operation counters for Responses, image, and audio operations, with fail-closed validation and writes;
- global concurrency semaphores for AI/media operations;
- per-user cooldowns and rolling quotas;
- admin-only diagnostic command defaults and permission checks;
- ownership checks for Regenerate and DM-copy buttons;
- channel permission checks and scoped conversation memory;
- URL fetching protections including HTTPS-only handling, rejecting credentials/IP literals/private DNS, pinning public DNS, and disabling redirects;
- bounded HTTP/SSE handling and failure handling for incomplete/failed Responses streams;
- prompt-injection defense-in-depth marking external content as untrusted;
- preservation of image edit failure behavior (do not silently fall back to new image generation after an edit failure);
- preservation of tool continuation handling via `build_failure_tool_continuation()`.

These are code-level controls; they are not proof of end-to-end production security.

## 5. Latest additional hardening (2026-11-23)

The candidate received further bounds for provider output and function-call metadata:

- `MAX_RESPONSES_OUTPUT_CHARS`: default 65,536 characters, configurable via environment variable with minimum 1,024.
- At most 8 function calls per assistant turn.
- Function-call ID maximum 256 characters.
- Function name maximum 128 characters.
- Function arguments maximum 16 KiB per call.
- Function-call payloads are normalized before being retained.
- Identical duplicate call IDs/payloads are ignored; conflicting duplicates are rejected.
- Valid JSON SSE data that is not an object is ignored safely.
- Added 9 regression tests for output boundaries/types and function-call metadata/count/duplicates.

**Important:** the 65,536-character output limit is a local resource/output bound, not a provider billing cap. Provider charges can occur before the bot receives and rejects an oversized response. Keep global quotas and provider-side billing alerts.

## 6. Test and verification status — do not overstate

Latest verified CI:
- Run `45514190007`: SUCCESS on candidate branch commit `840a479e334f9ee9ea3e023c43221dd80ad8cc9f`.
- Candidate regression suite: **56/56 PASS**.
- Existing audit regression suite: **74/74 PASS**.
- Total: **130/130 tests PASS**.
- CPython 3.12 compile: PASS.
- `pip check`: PASS.
- Bandit medium/high severity scan: PASS.
- `pip-audit --strict`: PASS; no known dependency vulnerabilities found.
- Latest candidate code blob remains `ed97417c86ddccc736efdac67c8694bdf80ea941`; later branch commits refreshed reports/docs and did not change candidate source.

These are CI/unit/regression results. They are not live end-to-end validation.

No live staging has yet proven Discord Gateway, Discord CDN attachment URLs, Xingchi SSE, Cloudflare image/edit/audio, Pollinations fallback, Wispbyte runtime dependencies, restart/shutdown, or real multi-user traffic. Do not call the candidate production-ready until the staging checklist in `FINAL_AUDIT_REPORT.md` is completed.

## 7. Known limits / remaining security work

These are not necessarily exploitable findings; they are areas requiring explicit verification:

1. **Live integration:** Discord interactions, gateway reconnects, provider auth/model compatibility, SSE edge cases, media providers, and hosting behavior still need staging tests.
2. **Provider cost exposure:** local operation quotas are not dollar/token spend caps. Confirm provider-side hard limits and billing alerts.
3. **Persistence and multi-process assumptions:** confirm whether hosting can run multiple bot processes/replicas. Process-local locks and counters do not coordinate across replicas unless storage/locking is shared.
4. **Parser behavior:** test supported file formats with malformed, oversized, deeply nested, and resource-intensive samples in isolated staging; confirm optional libraries and OS/container limits.
5. **Button authorization:** verify user ownership, guild/channel scope, expiration, stale message state, concurrent clicks, and behavior after restart.
6. **Command authorization:** review every slash command and every privileged state mutation; do not assume one command's permission check protects another.
7. **Prompt injection:** prompt text is not a security sandbox. Independently validate tool names, arguments, user authorization, and side effects in code. Never let document/web content grant permissions.
8. **Secrets:** verify redaction for all provider error shapes and ensure state files, diagnostics, crash reports, and logs never contain credentials.
9. **Resource limits:** inspect every path that accumulates response text, event data, parser output, attachment bytes, image dimensions, and tool-call metadata. Bound before allocation wherever possible.
10. **Recovery:** test corrupt state, disk-full/write failure, restart during writes, and concurrent saves. User-visible commands must not claim a setting was saved if persistence failed.

## 8. Current configuration/rate-limit values

Rechecked against the latest candidate:
- Chat cooldown: 0 seconds; 10 per 60 seconds.
- File cooldown: 0 seconds; 6 per 300 seconds.
- Image cooldown: 20 seconds; 8 per 600 seconds.
- Regenerate: 8 seconds; 6 per 120 seconds.
- Summarize: 60 seconds; 3 per 600 seconds.
- Diagnostic: 120 seconds; 3 per 3,600 seconds.
- DM copy: 5 seconds; 5 per 120 seconds.
- Daily user quota: 400 operations per rolling 24 hours.
- Global daily operation defaults: Responses 600; images 40; audio 80.
- AI concurrency 4; image 2; audio 2; attachment processing 3.
- Max attachments per message: 10; total default 100 MB.
- Vision images per turn: 10.
- Document text default: 50,000 chars; aggregate attachment context: 100,000 chars.
- Queue per user/channel scope: max 10 waiting tasks; default timeout 300 seconds (hard cap 600 seconds).
- ZIP/7Z expanded bytes, member count, compression ratio, and nesting are bounded.
- 7Z requires `py7zr>=1.0,<2`; safe XML parsing requires `defusedxml`. Both are in `requirements-audit.txt`.
- Prompt history cap: 240,000 characters.
- Responses SSE cap: 20 MiB total; 1 MiB per line.
- Default maximum output image: 12 MiB.
- Image pixel default maximum: 25,000,000 pixels.

Per-user rate limiter is RAM-only and resets on restart. The conversation queue is also in-memory: it is bounded, FIFO, times out, and reports task cancellation, but it is not replayed after a hard process crash/restart. Graceful cancellation status update is best-effort. Global persisted quota is intended for a single active process/host and uses process-local locking; it is not safe as a shared multi-replica counter.

## 9. Current audit score and meaning

The conversational estimate given to the user was **8.7/10 provisional**, up from 8.5, based on source inspection and additional security controls. This is not a formal penetration-test rating, certification, or guarantee. Do not present it as objective assurance. The score must be revisited after CI and staging tests; lower it if new issues appear.

## 10. How the next session should resume

1. Read this handoff and the latest audit report first.
2. Fetch the current branch head and current blob SHAs; do not rely blindly on SHAs recorded here if branch advanced.
3. Confirm production `main.py` and original source remain untouched.
4. Run/check latest Python 3.12 compile and all regression tests; inspect the actual CI job logs.
5. Continue adversarial review with local mocks, prioritizing:
   - authorization checks for every command and state-changing action;
   - bounded response accumulation and SSE parser edge cases;
   - all untrusted file/parser inputs;
   - URL fetch/redirect/DNS protections;
   - persistence failures and multi-process deployment assumptions;
   - button replay/ownership/expiry/restart behavior;
   - resource exhaustion and provider quota behavior.
6. Patch only the audit candidate and tests. Update the report and this handoff with exact commit IDs, test counts, results, and unresolved risks.
7. Do not merge or deploy until the user authorizes it and staging validation is complete.

## 11. Important distinction from older repository notes

The repository's root-level `master_memory_handoff_proyek_clouzer_ai_bot.md` is a separate/older project handoff and may contain historical assumptions or configuration details. Do not blindly merge its project identity, deployment facts, filenames, model/provider values, or instructions into this current audit. If the user explicitly says the projects are the same, reconcile them based on current source and user confirmation; otherwise keep the contexts separate.
