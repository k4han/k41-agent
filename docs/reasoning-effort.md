# Model-specific reasoning effort

The chat dashboard reads supported effort levels and documented defaults from
the installed LangChain partner packages. Selecting a different model resets
effort to that model's default. If its default is unknown, Auto leaves the
choice to the provider. Auto can also be selected manually.
If model resolution uses the configured fallback, the fallback model's default
replaces the requested model's effort override. Invalid effort overrides for
the directly requested model are still rejected.

The model catalog API exposes `reasoning_effort_levels` and
`reasoning_effort_default`. A null levels value means metadata is missing; an
empty array means configurable effort is unavailable. The dashboard disables
the control in either case. Reasoning support alone does not establish which
effort levels are accepted. Gemini 2.5 uses a token budget instead of effort
levels and is not offered a categorical effort selector.

## Supplementing missing metadata

Open Settings > Providers, edit the provider, and set **Model Reasoning
Profiles (JSON)**. Use exact model IDs as keys:

```json
{
  "custom-model": {
    "reasoning_effort_levels": ["low", "medium", "high"],
    "reasoning_effort_default": "high"
  },
  "fixed-model": {
    "reasoning_effort_levels": []
  }
}
```

This setting is stored as `llm.providers.<provider>.model_profiles`. It can
also be updated through the settings API as a JSON object or JSON string.
Overrides merge with installed metadata and take precedence. An empty levels
array disables effort selection. Levels must be unique lowercase identifiers;
when supplied together, the default must belong to the levels array. Clearing
the setting restores installed metadata.
Models named in the overrides are also included in the selectable model list.

Overrides affect both discovery and runtime requests. OpenAI, Anthropic, and
Gemini 3+ receive LangChain's standard `reasoning_effort` parameter. GPT-6
uses the Responses API even without effort metadata, including namespaced model
IDs such as `openai/gpt-6.1-sol`. Its effort overrides use `reasoning.effort`. Custom
OpenAI-compatible endpoints must support the corresponding OpenAI request
format; declaring metadata does not change an endpoint's API capabilities.

Metadata comes from installed package registries, not a live provider
capabilities query. Update the partner packages when model metadata changes,
or supplement the missing information through the provider setting.
