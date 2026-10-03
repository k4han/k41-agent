# Decision Model Framework & SDK Evaluation

## 1. Executive Summary

This document records the architectural evaluation and technical decisions for integrating fast, low-cost decision models into **kaka-agent-v2**, specifically introducing **Cloudflare Workers AI Clef-flash** (`@cf/cloudflare/clef-flash`) for agent routing and multi-domain evaluation tasks.

A key requirement in Milestone 1 was evaluating whether to adopt the third-party `typesafe-sdk` or implement a resilient, native asynchronous REST client using the existing project-standard `httpx` library. Following detailed protocol analysis and dependency inspection, we adopted a **native `httpx` asynchronous client (`CloudflareClefClient`)** with typed Pydantic v2 schemas and a provider-agnostic port (`DecisionClient`).

---

## 2. Decision Model Overview: Cloudflare Clef-flash

On October 1, 2026, Cloudflare released the **Clef** family of purpose-built decision models on Workers AI:

| Attribute | `@cf/cloudflare/clef-flash` | `@cf/cloudflare/clef` |
|---|---|---|
| **Parameters** | 9B (Qwen3.5-9B base with joint schema head) | 27B (Qwen3.8-27B base) |
| **Median Inference Latency** | ~38.8 ms | ~209 ms |
| **Typical WAN Latency** | ~75 – 160 ms | ~260 – 420 ms |
| **Pricing** | $0.09 / 1M input tokens | $0.24 / 1M input tokens |
| **Context Window** | 65,536 tokens | 65,536 tokens |
| **Input Modalities** | Text, JSON, up to 4 images | Text, JSON, up to 4 images |

### Core Decision Primitives

Clef models operate directly on decision-specific schemas rather than conversational chat completions:

1. **`choice`**: Categorical classification over discrete options described with criteria strings. Returns the selected choice, normalized probability distribution, and confidence score.
2. **`noul`**: Calibrated binary determination ("yes" / "no"). Returns a probability float in `[0.0, 1.0]`, a boolean decision resolved against a threshold, and a calibrated confidence score.
3. **`score`**: Ordered rubric scoring along defined discrete quality or compliance levels. Returns a continuous score, selected level, and level probabilities.

---

## 3. SDK Evaluation: `typesafe-sdk` vs Native `httpx` Client

### 3.1 Analysis of `typesafe-sdk`

`typesafe-sdk` (v0.7.2 on PyPI) was surveyed as a candidate client library for decision models. Our analysis identified fundamental discrepancies:

1. **Target Protocol & Endpoint Mismatch**:
   - `typesafe-sdk` is tailored for TypeSafe AI's hosted Jev model endpoints (`api.typesafe.ai` or OpenRouter) using standard `/v1/` routes.
   - Cloudflare Workers AI strictly requires account-scoped REST endpoints:
     `POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/clef-flash`
   - `typesafe-sdk` does not support Cloudflare account ID URL path parameters.

2. **Authentication Header Incompatibility**:
   - Cloudflare Workers AI requires Cloudflare API Tokens with `Workers AI:Read`/`Edit` permissions passed as:
     `Authorization: Bearer <CLOUDFLARE_API_TOKEN>`
   - `typesafe-sdk` injects proprietary headers (`TYPESAFE_API_KEY`) and lacks native bearer token mapping for Cloudflare.

3. **Response Envelope Incompatibility**:
   - Cloudflare Workers AI wraps model outputs in its standard REST envelope:
     ```json
     {
       "success": true,
       "errors": [],
       "messages": [],
       "result": {
         "model": "clef-flash",
         "answers": {
           "route": {
             "choice": "coder",
             "probabilities": { "coder": 0.92, "researcher": 0.08 },
             "confidence": 0.92
           }
         }
       }
     }
     ```
   - `typesafe-sdk` expects flat root-level answers and fails during deserialization when encountering the Cloudflare envelope.

4. **Dependency Conflict & Bloat**:
   - Installing `typesafe-sdk` introduces four additional dependencies: `httpcore2==2.13.1`, `httpx2==2.13.1`, `truststore==0.10.4`, and `typesafe-sdk==0.7.2`.
   - The repository already standardizes on `httpx>=0.28.0` (with `0.28.1` installed). Introducing hard forks `httpx2` and `httpcore2` adds transport divergence and maintenance complexity.

### 3.2 Comparison Matrix

| Evaluation Dimension | `typesafe-sdk` | Native `httpx.AsyncClient` |
|---|---|---|
| **Zero New Dependencies** | ❌ (Adds 4 packages including `httpx2`) | ✅ (Uses existing `httpx>=0.28.0`) |
| **Cloudflare Account URL Support** | ❌ (Hardcoded to `/v1/` paths) | ✅ (Native `/accounts/{account_id}/...`) |
| **Cloudflare Bearer Auth** | ❌ (Non-standard headers) | ✅ (Native `Bearer <token>`) |
| **Cloudflare Envelope Parsing** | ❌ (Fails on `result.answers`) | ✅ (Unpacks `result.answers` cleanly) |
| **Exponential Backoff & Jitter** | Limited / fixed retry | ✅ (Full backoff with `Retry-After` on 429 & 5xx) |
| **Custom Transport / Offline Testing** | Difficult to mock | ✅ (Supports `httpx.MockTransport`) |
| **Pydantic v2 Schema Alignment** | Proprietary internal types | ✅ (Direct Pydantic v2 typed models) |

### 3.3 Architectural Decision

**Decision**: Adopt the native `httpx.AsyncClient` implementation (`CloudflareClefClient`) and avoid `typesafe-sdk`.

**Rationale**: Adopting `typesafe-sdk` would necessitate writing custom adapters, monkey-patching URL generators, unwrapping envelopes manually, and resolving dependency conflicts between `httpx` and `httpx2`. In contrast, a native client using `httpx` requires approximately 200 lines of clean, resilient, fully typed Python code with zero additional dependencies, full connection pooling, and complete test isolation.

---

## 4. Module Architecture & Components

The decision framework is encapsulated under `agent/modules/decisions/`:

```text
agent/modules/decisions/
├── __init__.py           # Public exports adhering to test_module_public_import_boundaries.py
├── ports.py              # DecisionClient abstract base class
├── models.py             # Pydantic v2 question/answer schemas, result, and exceptions
├── cloudflare.py         # CloudflareClefClient native asynchronous REST client
└── mock.py               # MockDecisionClient for deterministic test isolation
```

### 4.1 Schema Layer (`models.py`)

- **Questions**:
  - `ChoiceQuestion(instructions, criteria)`
  - `NoulQuestion(instructions, threshold=0.5)`
  - `ScoreQuestion(instructions, criteria)`
  - `QuestionUnion` (discriminated union by `type`)
- **Answers**:
  - `ChoiceAnswer(choice, probabilities, confidence)`: Automatically infers confidence from `probabilities[choice]` if not supplied.
  - `NoulAnswer(noul, decision, confidence)`: Automatically resolves `decision = (noul >= threshold)` and `confidence = max(noul, 1.0 - noul)` if not supplied.
  - `ScoreAnswer(score, selected_level, probabilities, confidence)`: Automatically infers confidence and `selected_level` from probability distribution if not supplied.
  - `AnswerUnion` (discriminated union by `type`)
- **Container**:
  - `DecisionResult(model, answers, latency_ms, raw_response)`

### 4.2 Port Layer (`ports.py`)

- `DecisionClient(ABC)`:
  - `evaluate(state, questions, model=None, **kwargs) -> DecisionResult` (abstract)
  - `evaluate_choice(state, instructions, criteria, model=None) -> ChoiceAnswer`
  - `evaluate_noul(state, instructions, threshold=0.5, model=None) -> NoulAnswer`
  - `evaluate_score(state, instructions, criteria, model=None) -> ScoreAnswer`
  - `close() -> None` and `__aenter__` / `__aexit__` context manager protocol

### 4.3 Cloudflare Client (`cloudflare.py`)

- **Connection Management**: Async HTTP client with connection pooling (`max_keepalive_connections=20`, `max_connections=50`).
- **Resilience**:
  - HTTP 429: Respects `Retry-After` header with randomized jitter.
  - HTTP 500, 502, 503, 504: Exponential backoff `(0.5 * 2^attempt) + jitter`.
  - HTTP 401, 403: Fast-fail `DecisionAuthenticationError`.
  - Timeouts: Configurable timeout with `DecisionTimeoutError`.
- **Envelope Unpacking**: Extracts `response["result"]["answers"]` and parses into typed answer models according to the question definitions.

### 4.4 Mock Client (`mock.py`)

- `MockDecisionClient`:
  - Deterministic evaluation for testing workflows and router nodes without API credentials.
  - Supports canned answers (by question ID), custom simulated latency, and sensible default responses.
  - Records all invocations in `recorded_calls` for assertions in test suites.

---

## 5. Verification Strategy

1. **Boundary Compliance**:
   All public types and clients are exported at `agent.modules.decisions.__init__.py` to satisfy `tests/test_module_public_import_boundaries.py`.
2. **Deterministic Offline Testing**:
   Unit tests utilize `MockDecisionClient` or `httpx.MockTransport`, ensuring CI environments run reliably without requiring Cloudflare API tokens or incurring network latency.
