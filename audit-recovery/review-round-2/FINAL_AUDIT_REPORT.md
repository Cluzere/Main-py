# Final Audit Report — `revisi_audit_final_main.py`

**Status:** Static/regression gate PASS. Production certification remains CONDITIONAL pending live staging/integration checks.

## Artifact identity
- Branch: `audit-recovery-gpt-2026-11-23`
- Candidate: `audit-recovery/review-round-2/revisi_audit_final_main.py`
- Candidate Git blob SHA-1: `ff560829a17541c0073cbc1788b32b61889a5d04`
- Candidate size: 230,056 characters / 4,730 lines
- Regression suite: `audit-recovery/review-round-2/test_audit_regressions.py`
- Test blob SHA-1: `f30d13ba0c8d6616e1c27dd77b6099fa067e5a78`
- GitHub Actions run: [#59 — PASS](https://github.com/Cluzere/Main-py/actions/runs/45407940932)
- CI head commit: `02626e5a0d33a397d7df9ca126950a3356dd1807`

## Changes applied
1. Provider base URL now requires HTTPS; plaintext HTTP is accepted only for localhost/loopback. The broad `ALLOW_INSECURE_BASE_URL` bypass was removed.
2. Redirect following is disabled on provider POST requests, diagnostic probes, Discord attachment fetches, and Pollinations image requests.
3. Outbound aiohttp session has explicit connection/concurrency/time limits and does not inherit proxy settings from the environment (`trust_env=False`).
4. Global persistent budget file operations are moved off async handlers with `asyncio.to_thread`.
5. Guild and per-user image lock registries use reference counts and remove idle entries; all guild-lock call sites use the cleanup manager.
6. Diagnostic command is ephemeral; function-call name/arguments and response snippets are secret-scrubbed before display.
7. Attachment parser failures now log a redacted diagnostic and return generic failure text rather than echoing raw parser/transport exception details to the model.
8. Existing external-content prompt policy, diagnostic permission policy, and fail-closed persisted-state behavior were preserved; they were not silently reverted.

## CI evidence
- Python 3.12 candidate compilation: PASS.
- Regression suite: **74 tests passed**.
- GitHub Actions job conclusion: SUCCESS.

## Remaining production gate — not covered by this CI
These checks require the actual deployment environment and credentials; passing unit tests cannot substitute for them:
1. Run a staging bot with real Discord permissions and verify message chat, reply context, regenerate/DM buttons, `/cek-tool`, image generation/editing, audio transcription, and each supported attachment type.
2. Verify Xingchi, Cloudflare, Discord CDN, and Pollinations behavior from the actual hosting network. In particular, confirm the host does not require an outbound proxy because this candidate sets `trust_env=False`.
3. Test restart and recovery with valid, missing, oversized, and deliberately corrupted `memory.json`, `view_state.json`, and global budget state. Corrupt persisted state intentionally fails closed and requires manual recovery rather than automatic overwrite.
4. Deploy as a single bot process when using the current file-backed global daily budget. Its in-process thread lock is not a cross-process/distributed lock; multiple replicas sharing one budget file need a separate coordination mechanism.
5. Observe a sustained staging run for memory growth, lock cleanup, request timeouts, Discord rate limits, and graceful shutdown.

## Decision
Do **not** replace production `main.py` solely on the basis of this CI pass. This artifact is a hardened release candidate that passed its current automated gate; promote it only after the staging checks above pass. Production `main.py` was not edited by this audit revision.
