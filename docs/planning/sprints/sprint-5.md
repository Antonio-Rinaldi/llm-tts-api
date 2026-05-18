# Sprint 5: OpenAI adapter + byte-identity equivalence

> Source: docs/planning/journal.md
> SRS: docs/specs/software-spec.md
> FRS: docs/specs/analyst-frs.md
> NFR: docs/specs/writer-nfr.md
> UAT: docs/specs/analyst-UAT.md
> Author: Sprint Planner (AI-assisted)
> Date: 2026-05-18
> Status: PLANNED
> Version: 1.0

## 1. Sprint Objective

Reduce `POST /v1/audio/speech` to a **thin OpenAI-shaped translator** over `POST /v1/tts/synthesize` (the rich endpoint shipped in Sprint 4), and prove the translation is **byte-faithful** to a paired rich-endpoint call against a warm model. This sprint locks in the OpenAI-compat contract while making the rich endpoint the single source of truth for synthesis — eliminating the dual-code-path risk (BR-9) that has shadowed the cycle since Sprint 2.

## 2. Value Statement

Sprint 4 made the rich endpoint the canonical synthesis surface. Until `/v1/audio/speech` is refactored to delegate to it, the OpenAI-compatible path remains a second implementation that must be maintained in parallel — drift between the two is the highest-likelihood regression source for the rest of the cycle. S-017 collapses that drift to zero by construction; S-018 proves it empirically. Together they unblock Sprint 6 (docs, Dockerfile, perf validation), which assumes one synthesis pipeline.

## 3. Sprint Summary

| Metric | Value |
|--------|-------|
| Stories | 2 |
| User stories | 1 (S-017) |
| Technical stories | 1 (S-018) |
| Total tasks | 9 |
| Parallel tracks | 1 (strictly serial across the two stories — service-boundary) |

## 4. Execution Order

Service-boundary rule applies. S-018 consumes the translation contract S-017 publishes; the two stories MUST run in separate execution steps.

| Step | Stories | Can start after |
|------|---------|----------------|
| 1 | S-017 | Immediately (S-013 is DONE; no intra-sprint deps) |
| 2 | S-018 | Step 1 complete (S-017's Service Interface section is the contract S-018 tests against) |

## 5. Stories

### S-017: OpenAI adapter as thin translator
- **Status:** READY-FOR-REVIEW
- **Type:** User
- **Parallel with:** None within this sprint
- **Depends on (intra-sprint):** None (depends on S-013 — DONE)
- **Refs:** FR-OA-01..04, NFR-PT-03, BR-9
- **Architecture:** SRS §4.3 (OpenAI adapter), SRS §5 G-1 (parity resolution), SRS §6 (handler topology)

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Define the OpenAI→rich request mapping table | Document each OpenAI field (`model`, `input`, `voice`, `response_format`, `speed`, `stream`) → rich-endpoint field, including defaults applied for fields the OpenAI schema does not expose. Lives in the implementation notes so S-018 can pair against it. | No (foundation) | READY-FOR-REVIEW | FR-OA-01, SRS §5 G-1 |
| 2 | Refactor `POST /v1/audio/speech` handler to translate + delegate | Replace direct `SpeechSynthesizer` calls with: (a) translate OpenAI request → rich-endpoint internal call signature, (b) await the rich endpoint's service-layer function (not via HTTP), (c) translate the response back to OpenAI shape. Handler stays ≤30 LOC of translation per UAT-OA-03. | No (depends on T1) | READY-FOR-REVIEW | FR-OA-02, NFR-PT-03 |
| 3 | Preserve OpenAI streaming end-to-end | Ensure `with_streaming_response.create(...)` still works: stream raw bytes through with OpenAI-expected `Content-Type`. Strip rich-endpoint-only headers (`X-Voice-Source`, `X-Chunks`, etc.) from the OpenAI response to keep the OpenAI contract intact per user constraint. | No (depends on T2) | READY-FOR-REVIEW | FR-OA-03, UAT-OA-02 |
| 4 | Sync `GET /v1/models` to the rich-endpoint catalog | Make `/v1/models` enumerate the same `(provider, model)` pairs the rich endpoint accepts (driven from the provider registry + allow-lists, no duplicated lists). | Yes (with T3 — independent surface) | READY-FOR-REVIEW | FR-OA-04, UAT-OA-04 |
| 5 | Tests: OpenAI request shape unchanged + adapter LOC + no-bypass | UAT-OA-01 (OpenAI request returns 200), UAT-OA-02 (SDK streaming), UAT-OA-03 (grep/AST check that the handler does not import or call `SpeechSynthesizer` directly — only the rich service-layer entry point), UAT-OA-04 (`/v1/models` matches catalog). | No (verifies T1–T4) | READY-FOR-REVIEW | FR-OA-01..04 |

#### Acceptance Criteria
- OpenAI-shaped request works unchanged (UAT-OA-01).
- OpenAI SDK streaming works against the local service (UAT-OA-02).
- Code-review/AST check finds no bypass calls into the synthesizer; handler is <~30 LOC of translation (UAT-OA-03).
- `/v1/models` and rich-endpoint catalog match (UAT-OA-04).
- OpenAI response shape is byte-identical to upstream OpenAI's `/v1/audio/speech` contract (user constraint — no extra headers leak, no `X-Voice-Source` etc. on this path).

#### Testing & Verification
Pytest suite extended with: (a) OpenAI-shaped happy path against `TestClient`; (b) streaming path using `httpx.AsyncClient` chunked iteration; (c) a static check (AST or grep) asserting `routers/audio.py` (or the adapter module) does not import `SpeechSynthesizer` or the rich endpoint's router directly — only the shared service-layer function; (d) `/v1/models` enumeration cross-checked against the provider registry. mypy --strict + ruff stays clean.

---

### S-018: Byte-identity paired UAT (rich vs OpenAI)
- **Status:** READY-FOR-REVIEW
- **Type:** Technical
- **Parallel with:** None within this sprint
- **Depends on (intra-sprint):** S-017
- **Refs:** NFR-PT-03b (SRS §5 G-1), RISK-8, UAT-OA-05
- **Architecture:** SRS §5 G-1 (parity resolution + RISK-8 relaxation contract)

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Build paired-request fixture | Construct an OpenAI-shaped request and the **equivalent** rich-endpoint request from the S-017 mapping table (Step 1, T1). Both go through the same warm-model code path. Same seed where the provider exposes one. | No (foundation) | READY-FOR-REVIEW | UAT-OA-05 |
| 2 | Implement byte-identity assertion (strict path) | `sha256` of the audio body from each endpoint must match for at least one provider/model combo on warm load. Test marked deterministic — gate of the strict-equivalence claim. | No (depends on T1) | READY-FOR-REVIEW | NFR-PT-03b |
| 3 | Implement relaxation path (RISK-8 fallback) | If a provider proves non-deterministic in CI, the test falls back to `±1 sample length + perceptual-hash threshold` per SRS §5 G-1. Relaxation threshold + rationale recorded in `docs/perf/baseline.md` (or sibling) and referenced from SRS §5. The strict path stays in CI for the deterministic provider/model. | No (depends on T2) | READY-FOR-REVIEW | RISK-8, SRS §5 G-1 |
| 4 | Wire into CI as a deselected-by-default integration test that runs nightly OR a regular unit test if cheap | Decision driven by warm-model cost. Default: paired test runs in the standard unit suite if model load is already amortized by other tests; otherwise marked `@pytest.mark.integration` and run on a dedicated CI job. Document the decision in the implementation notes. | No (depends on T2/T3) | READY-FOR-REVIEW | UAT-OA-05 |

#### Acceptance Criteria
- Paired test exists and runs in CI.
- Byte-identity holds for at least one provider/model combo on warm load.
- If relaxation is applied, the relaxation threshold + rationale is recorded in `docs/perf/baseline.md` (or a sibling doc) and referenced from SRS §5.

#### Testing & Verification
A single new test file under `tests/` (most likely `tests/test_openai_adapter_parity.py`) executes the paired requests through the in-process app (no network), asserts sha256 equality on the audio bodies, and skips/relaxes per RISK-8 documented relaxation. mypy --strict + ruff stays clean. The test must be deterministic enough to not flake in CI — if it flakes, escalate to the relaxation path rather than retry-looping.

---

## 6. References

- [SRS](../../specs/software-spec.md) — §4.3 OpenAI adapter, §5 G-1 parity resolution, §6 handler topology
- [FRS](../../specs/analyst-frs.md) — FR-OA-01..04
- [NFR](../../specs/writer-nfr.md) — NFR-PT-03, NFR-PT-03b
- [UAT](../../specs/analyst-UAT.md) — UAT-OA-01..05
- [Journal](../journal.md) — Stories: S-017, S-018

## 7. Risks & Dependencies

| Risk/Dependency | Affected Stories | Mitigation |
|----------------|-----------------|------------|
| RISK-8 — provider non-determinism could break byte-identity | S-018 | Documented relaxation path (T3): perceptual-hash + ±1 sample tolerance; record threshold + rationale in `docs/perf/baseline.md`. Strict path stays for at least one deterministic provider/model. |
| OpenAI contract drift if adapter accidentally leaks rich-endpoint-only headers | S-017 | User-decided constraint enforced in T3; test asserts the OpenAI response has only OpenAI-expected headers + `X-Request-ID`. |
| Adapter handler size creep — could pull synthesis logic in by accident | S-017 | UAT-OA-03 LOC + AST/grep check (T5) gates merge; code-reviewer skill repeats the check at Phase 3. |
| Service-boundary discipline — S-018 must not start before S-017's Service Interface section is assembled | S-018 | Coordinator enforces (Phase 2 Step 2.6 assembly before Step 2.2 of next step). |
