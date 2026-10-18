# Context window cache usage

Verified against official API documentation and the installed SDK adapters on 2026-10-18.

## Endpoint fields

| Endpoint | Cache reads | Total input used for the hit rate |
| --- | --- | --- |
| OpenAI `/v1/chat/completions` | `usage.prompt_tokens_details.cached_tokens` | `usage.prompt_tokens` |
| OpenAI `/v1/responses` | `usage.input_tokens_details.cached_tokens` | `usage.input_tokens` |
| OpenRouter `/api/v1/chat/completions` | `usage.prompt_tokens_details.cached_tokens` | `usage.prompt_tokens` |
| DeepSeek `/chat/completions` | `usage.prompt_tokens_details.cached_tokens`, falling back to `usage.prompt_cache_hit_tokens` when the standard field is absent/null | `usage.prompt_tokens` |
| Anthropic `/v1/messages` | `usage.cache_read_input_tokens` | `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` |
| Gemini `generateContent` / `streamGenerateContent` | `usageMetadata.cachedContentTokenCount` | `usageMetadata.promptTokenCount`, plus server-side tool prompt tokens when reported |

Cache writes (`cache_write_tokens` or `cache_creation_input_tokens`) are not hits.
For OpenAI, OpenRouter, DeepSeek, and Gemini, the total input already includes cached tokens.
Anthropic's raw `input_tokens` excludes cache reads and cache creation; its LangChain adapter adds those fields to produce the normalized input total. Do not add them again.

## Normalization and streaming

- Prefer SDK-normalized `usage_metadata`. Its `input_token_details` contains `cache_read`; OpenAI priority/flex tiers use `priority_cache_read` / `flex_cache_read`. Sum these disjoint partitions, excluding write and TTL counters.
- Normalize DeepSeek's legacy alias before the OpenAI adapter discards nonstandard fields. Preserve an explicit standard zero if both fields are present.
- OpenAI-compatible Chat Completions streams may repeat or correct cumulative usage. Forward text immediately, but attach only the last usage snapshot once. A terminal snapshot without cache data must not reuse an earlier hit count.
- Responses streams retain their SDK event handling; completed response usage is normalized by the OpenAI adapter.
- Anthropic can report input/cache usage in `message_start` and only output usage in later `message_delta` events. Merge non-null fields into a request-local snapshot, accepting explicit zero and later corrections. Count the final cumulative snapshot once. Preserve SDK handling for content, tools, thinking, and stop metadata.
- Gemini's adapter converts raw cumulative snapshots into deltas. Adding those deltas produces the completed usage; do not apply the Chat Completions snapshot rule to Gemini.
- The callback record and `context_usage` event must describe the same completed call. The thread usage API reads the latest eligible call's stored input details, including tier-prefixed cache fields.

## Display

`Cache hit = cached input tokens / total input tokens * 100`.

The cache count is already part of context usage. Never add it again or subtract it from the context window. The percentage excludes response/output tokens.

Show a reported zero as `0 tokens (0.0%)`. If no usable cache-read count is available, show `—`. Reject negative, non-integer, boolean, or out-of-range normalized cache counts. A compaction estimate does not predict cache hits for the next request.

## Sources

- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [OpenAI Chat Completions caching example](https://developers.openai.com/cookbook/examples/prompt_caching101)
- [DeepSeek Chat Completions response schema](https://api-docs.deepseek.com/api/create-chat-completion/)
- [OpenRouter prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
- [Anthropic prompt caching and total input accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic streaming events](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Gemini UsageMetadata](https://ai.google.dev/api/generate-content#UsageMetadata)

Regression tests replay endpoint payloads through installed SDKs without live model calls. They cover synchronous/asynchronous streams, nonstreaming OpenAI responses, cache aliases, service tiers, corrected snapshots, concurrent Anthropic calls, Gemini cumulative usage, and raw usage fallback.
