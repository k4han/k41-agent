# Priority model pools

Open **Settings > Providers > Model Pools** to create a named, ordered list of
models. Members can use different configured providers or accounts, including
manually entered model identifiers. Select a pool in chat or agent settings,
or use **Set Default** on the pool page.

A pool named `small` uses `provider: "@pool"` and `model: "small"` in chat
requests and agent cards. The global default is `llm.default_model: "@pool/small"`.
Pool names are immutable lowercase identifiers of up to 64 characters, using
letters, numbers, underscores and hyphens. Nested pools and duplicate members
are rejected. Pools support automatic reasoning effort only.

## Priority and recovery

Each physical LLM call starts with the first available member. Rate limits,
connection errors, timeouts and server errors allow two retries by default,
waiting one second and then two seconds. Authentication failures, missing models
and exhausted credit or quota switch immediately to the next member. Invalid
requests, content-policy failures, application errors and cancellation stop the
call without switching.

Failed members are skipped for 60 seconds by default, or one hour when credit
or quota is exhausted. Provider retry/reset information takes precedence. A
`Retry-After` longer than 10 seconds skips retries and switches immediately.
After the deadline, the next request can probe the preferred member again;
only one recovery probe for a member is allowed at a time.

If output has started, including reasoning or tool-call output, the call stops
on error without retrying or switching models. Previously completed tool steps
are not replayed. Usage records identify the actual provider and model and
include the logical `pool_name` in usage metadata.

Configuration persists in the existing `runtime_settings` table at
`llm.model_pools.<name>`. Cooldowns are process-local RAM state: they survive
across conversations while the application runs, and reset on restart. No
background health checks, balance polling or cross-process synchronization run.
Use **Reset State** after repairing a connection to bypass its cooldown.

## Management API

All routes require the same admin authentication and CSRF protections as other
dashboard management routes.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/dashboard-api/model-pools` | List configurations and current member availability |
| POST | `/dashboard-api/model-pools` | Create a pool |
| PUT | `/dashboard-api/model-pools/{name}` | Replace members and retry settings |
| DELETE | `/dashboard-api/model-pools/{name}` | Delete an unreferenced pool |
| POST | `/dashboard-api/model-pools/{name}/reset` | Clear temporary availability state |

Example creation body:

```json
{
  "name": "small",
  "members": [
    {"provider": "primary", "model": "fast-model"},
    {"provider": "backup", "model": "another-model"}
  ],
  "retry_count": 2,
  "cooldown_seconds": 60,
  "quota_cooldown_seconds": 3600
}
```

Updates use the same body without `name`. Retry count accepts integers from
zero to five; recovery delays must be positive integers. A pool referenced by
an agent or the global default cannot be deleted; providers referenced by pools
cannot be deleted. Disabling a provider causes it to be skipped. Generic settings
routes cannot edit pool definitions, so they cannot bypass these safeguards.

Custom provider factories must honor `ModelConfig.disable_retries`, disabling
both SDK retries and any adapter-specific retry loop for pool clients. Direct
model clients retain the adapter's existing retry policy. Internal provider
extensions are optional and maintained separately from the core repository.

When no member is available, calls fail with `model_pool_exhausted` and the
earliest known recovery time. Pools use their explicit member list as the whole
fallback chain; the legacy global fallback still applies to direct model
selection.
