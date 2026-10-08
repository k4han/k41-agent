# Model-specific reasoning effort

The chat dashboard reads supported effort levels and documented defaults from
the installed LangChain partner packages. Selecting a different model resets
effort to that model's default, or the agent card's effort for its configured
model. Selecting Auto bypasses the agent card default and uses the model's
default. If its default is unknown, Auto leaves the choice to the provider.
If model resolution uses the configured fallback, the fallback model's default
replaces the requested model's effort override. Invalid effort overrides for
the directly requested model are still rejected.

The model catalog API exposes `reasoning_effort_levels` and
`reasoning_effort_default`. A null levels value means metadata is missing; an
empty array means configurable effort is unavailable. The dashboard disables
the control in either case. Reasoning support alone does not establish which
effort levels are accepted. Gemini 2.5 uses a token budget instead of effort
levels and is not offered a categorical effort selector.

## Agent card defaults

Agent Markdown frontmatter accepts a model ID string or structured model settings:

```yaml
provider: anthropic
model:
  id: claude-opus-5-5
  effort: low
```

The parser keeps the model ID in `model` and exposes the optional effort as
`reasoning_effort` in agent configuration and dashboard responses. Saving a card
with an effort writes the structured YAML form; existing string model settings
remain supported. The agent editor preserves and can edit this default.
If both `model.effort` and the flat `reasoning_effort` field are supplied, their
values must match; conflicting values are rejected.

An explicit per-run `reasoning_effort` takes precedence over the card default.
The value `auto` explicitly selects the model default; omitting the field
inherits the applicable agent card default. `auto` is not sent to the provider
as an effort level.
The card default applies only to its configured model and provider. Choosing a
different model or resolving a fallback uses that model's default instead.
Empty or `default` providers and empty, `default`, or legacy `provider default`
models are resolved to their configured identities before matching the card.
The chat effort selector starts with the selected agent's default when available.
Effort validation and missing metadata follow the same provider rules described
below.

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
