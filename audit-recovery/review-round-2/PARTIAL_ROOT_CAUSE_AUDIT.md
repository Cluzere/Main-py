# Root-cause audit — 32 PARTIAL items

Date: 2026-11-25  
Candidate branch: `audit-recovery-gpt-2026-11-23`  
Candidate source: `audit-recovery/review-round-2/revisi_audit_final_main.py`  
Scope: static code review + existing regression evidence. No production deployment or live Discord traffic was performed.

## Executive finding

The 32 PARTIAL statuses are not 32 confirmed code defects. They consist of:
- implementation/scope limits that need an explicit product decision or additional feature work;
- test-coverage gaps that can be closed in CI;
- environment-dependent gates that cannot honestly be closed by mocked tests;
- several report descriptions that need correction because the implementation has moved ahead of the old wording.

No PARTIAL status is promoted to DONE by this document. A status changes only after its stated closure evidence exists.

## Item-by-item root cause and closure condition

| No. | Root cause | Type | What closes it |
|---:|---|---|---|
| 3 | The 100 MB batch cap is checked against Discord attachment metadata and parser-side byte limits, but the exact 10-large-upload behavior has not been exercised through Discord CDN. | Live validation | Upload 10 real attachments through Discord, including files near the cap; verify skipped-file manifest and memory behavior. |
| 7 | Signature checks exist for PDF, 7Z, ZIP-based Office/EPUB/ODF, RTF, audio, SVG, EPS and AI. Plain-text formats have no universal magic bytes, and a matching header does not prove semantic validity. | Coverage/format limitation | Keep explicit format-specific validation; expand malformed corpus. Do not claim universal signature validation for text. |
| 13 | ZIP member reads call `check_file_signature()` before dispatch. Text, PDF, DOCX, PPTX and XLSX have extraction paths; SVG/EPS/AI are signature-gated but not semantically extracted from archives, and not every member format has a full semantic validator. The older report wording that vector checks only exist on the direct-upload path is stale. | Implementation scope + report correction | Add archive tests for vector signature gates (added in commit 0581981); decide whether vector extraction inside archives is a required feature. |
| 15 | Nested ZIP support is intentionally limited to one nested level; nested 7Z is not expanded. This is a resource/complexity limit, not an accidental parser failure. | Explicit feature limit | Either keep and document the limit as accepted behavior, or design a shared-depth/shared-byte-budget recursive archive parser with dedicated adversarial tests. |
| 16 | Existing ZIP mutation/truncation and archive-bomb tests are deterministic examples, not a general fuzz campaign across ZIP/7Z and all member formats. | Test coverage | Add bounded mutation/property-based fuzz runs with reproducible seeds and crash/time/memory ceilings; run longer corpus outside normal CI. |
| 20 | Image context has helper-level tests for deduplication, cap, user isolation and TTL. The full Discord sequence (reply image → edit → regenerate → expiry/restart) is not proven end-to-end. | Integration validation | Exercise the full sequence in a staging bot and verify the exact source image is used and never leaks between users. |
| 21 | Scope keys include guild, channel and user. The remaining gap is combined media/reply behavior under real Discord message references, threads and DM/channel transitions. | Integration validation | Add tests for reply resolution in threads/DMs and live checks for same-channel multi-user isolation. |
| 30 | Queue is bounded and cancellation/timeout/full behavior is tested, but it is in-memory: a hard process death loses waiting work, and there is no user-facing cancel command. | Explicit product/runtime limit | Decide whether durable queue and cancel UX are required. If yes, persist job metadata and add recovery/idempotent replay tests; otherwise document resend-on-restart behavior. |
| 32 | Retry is deliberately narrow: HTTP 502/503/504, and HTTP 500 only for the known upstream-forwarding phrase, before the first SSE event. Other provider error shapes are not classified. | Provider compatibility | Capture sanitized real Xingchi failure samples and add narrowly-scoped classifiers/tests. Avoid broad retries. |
| 34 | There is no universal idempotency key for provider-side effects. The code avoids retry after stream events, which reduces duplicate tool execution, but cannot guarantee provider-side exactly-once behavior. | Provider/API capability | Confirm provider idempotency support. If supported, send stable request keys; otherwise retain no-replay policy and document at-most-once attempt semantics. |
| 35 | Discord message edit/send delivery failures and duplicate delivery have not been exercised against the real Gateway/API. | Live validation | Inject Discord API failures in staging; verify one final response, no duplicate sends, and fallback behavior. |
| 37 | A partial stream is retained and marked interrupted, but there is no stream cursor/resume protocol. | Provider/API capability | Only implement resume if Xingchi exposes resumable stream IDs/cursors; otherwise keep partial answer + explicit interrupted marker. |
| 39 | Diagnostics record request headers, first event/text, web-search event timings and completion. They do not explain every upstream/network failure or provider-internal delay. | Observability | Add sanitized status/error category, timeout phase, retry decision, and correlation IDs from provider responses when available. |
| 42 | PDF scan pages are rendered and passed as images; there is no dedicated OCR engine for handwriting, poor scans, or text beyond the vision-image cap. | Feature gap | Decide OCR requirement; if required, add a bounded OCR engine/service and test language, rotation, low-resolution and handwriting cases. |
| 44 | XLSX/PPTX extraction handles common chart series and has bar/column and pie fixtures, but not every chart type, cached-value state, pivot chart, combo chart or unusual workbook structure. | Feature/test coverage | Enumerate supported chart types; add fixtures for scatter, line, area, doughnut, combo and missing-cache cases; clearly mark unsupported types. |
| 45 | DOCX/PPTX/XLSX extract common text, tables, headers/footers, notes, charts and core properties. Comments, footnotes/endnotes, tracked changes, embedded objects and some complex metadata are not comprehensively extracted. | Feature gap | Define required OOXML parts and add fixtures for each; parse only supported parts with bounded XML reads. |
| 49 | File-specific signature checks exist, but plain text has no universal signature and valid magic bytes do not establish full semantic validity. | Fundamental format limitation | Keep per-format checks and parser validation; document text formats as heuristic/encoding-validated rather than magic-validated. |
| 50 | Resource caps exist for bytes, entries, pixels, output and concurrency, but parsers run inside the bot process and are not isolated by OS CPU/RAM/time quotas. | Runtime architecture/security | If threat model requires hard isolation, move risky parsers to a restricted worker process/container with OS memory/CPU/time limits. |
| 51 | Some expensive parsing is offloaded to threads, but there is no measured Wispbyte CPU/RAM/latency profile for worst-case uploads. | Runtime validation | Profile representative and adversarial files on the actual hosting plan; record peak RSS, CPU time, event-loop lag and parse duration. |
| 52 | Regression corpus is meaningful but finite; it does not fuzz every parser and file format. | Test coverage | Extend seeded mutation/property-based tests and retain minimized failing samples as permanent regressions. |
| 53 | Memory and view state use bounded files/atomic writes, but graceful shutdown/restart and abrupt termination with active tasks have not been fully validated on Wispbyte. | Runtime validation | Test SIGTERM/restart with active turns and queued work; verify atomic files, persistent buttons, no corrupt state, and explicit resend behavior. |
| 54 | External content is marked untrusted in the system prompt and XML parsing is hardened, but prompt instructions are not a security sandbox. Model prompt injection cannot be mathematically eliminated by prompt text alone. | Security boundary | Enforce tool authorization and data-access policy in code; test malicious instructions in attachments/web results; never treat prompt wording as an execution sandbox. |
| 55 | Ten mixed attachments were tested with mocked Discord objects, not ten actual CDN uploads. | Live validation | Run a real Discord upload batch and verify order, manifest, size accounting and per-file failure isolation. |
| 56 | ZIP/container tests cover important Office/ODF/EPUB structure and mismatch cases, but not every format/structure/corruption combination. | Test coverage | Expand fixture matrix and mutation tests; keep parser-specific rejection assertions. |
| 57 | 7Z tests cover selected text/Office and bomb/signature cases; encrypted and varied real-world archives have not been tested on the hosting runtime. | Test + live validation | Add encrypted/header/solid/multi-volume fixtures where supported, then test representative real archives on Wispbyte. |
| 58 | Reply document/image paths have mock coverage, but Discord CDN, deleted source messages, thread references and real image-edit sequence are not all validated live. | Live validation | Test reply to image/document, deleted source, thread and attachment-plus-reply combinations on staging. |
| 59 | Image generation/edit succeeded in an earlier user-confirmed run, but it has not been repeated against the latest candidate and current provider credentials/quota. | Live provider validation | Re-run one new image and one attached-image edit against the latest candidate when quota is available; inspect output and Discord delivery. |
| 60 | Mock tests cover scopes, FIFO and cancellation; simultaneous real users on Discord and provider concurrency have not been exercised. | Live validation | Run a controlled multi-user test and verify no history/media/status cross-contamination and no unexpected queue starvation. |
| 61 | SSE partial disconnect and retry policy are unit-tested, but upstream fault injection against Xingchi is unavailable in current CI. | Provider validation | Use a controlled proxy/fault-injection environment or provider-supported test endpoint; verify partial text, error status and no replay. |
| 62 | Current corpus spans several formats but not every format and adversarial edge case. | Test coverage | Build a versioned format corpus with valid, malformed, truncated, oversized and mismatched samples for each supported extension. |
| 63 | History retention and partial-response behavior are unit-tested, but no-loss/no-duplicate behavior has not been proven under live Discord/provider failures. | Integration validation | Fault-inject after user-history append, after first delta, during final Discord edit and during tool continuation; verify one coherent history entry and no duplicate side effect. |
| 64 | CI is green, but the latest candidate is not the Wispbyte runtime and has not passed the complete staging matrix. | Release gate | Deploy candidate only after explicit maintenance approval; run staging checklist, verify health and rollback path, then decide production promotion. |

## Findings that are already implemented but were under-described

1. ZIP member processing calls `check_file_signature(ext, member_bytes)` for every member before type dispatch. This includes SVG/EPS/AI signatures; valid vector members are still reported unsupported because archive vector extraction is not implemented.
2. Queue tests already cover guild-lock full/timeout/cancellation and ingress FIFO/full/cancellation. The remaining queue issue is durability/cancel UX, not basic lock correctness.
3. PDF parser already renders pages with little/no text as visual input and explicitly reports pages omitted by scan caps. The remaining issue is OCR coverage/quality and pages beyond caps.
4. Core metadata, PPTX notes/chart series, XLSX sheet/chart values and DOCX header/footer are already extracted. The remaining issue is less-common OOXML parts and broader chart types.

## Current CI addendum

- Test commit: `05819815ea152b12560518b356a067a5ca5d9863`
- New focused candidate tests: **87/87 PASS**
- Existing regression tests: **76/76 PASS**
- Total: **163/163 PASS**
- `py_compile`, `pip check`, Bandit medium/high, and `pip-audit --strict`: PASS
- Run: https://github.com/Cluzere/Main-py/actions/runs/45567432060

## Decision

The PARTIAL list is primarily a mixture of feature boundaries and missing external evidence, not a list of 32 broken code paths. CI can close the testable gaps, but Discord CDN/Gateway, Xingchi behavior, Wispbyte resource profile, OCR quality and production restart behavior require the corresponding environment. Keep production status blocked until those gates are honestly satisfied.
