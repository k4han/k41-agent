---
description: Connect model providers, choose default models, and configure priority model pools.
---

# Model providers

The K41 runtime coordinates your work; a configured LLM provider supplies the
model. You choose the provider, credentials, and enabled models.

## Add a provider

1. Open **Settings > Providers > Provider catalog** and filter by **Models**.
2. Select a supported provider, or choose **Custom endpoint** for an OpenAI-compatible connection.
3. Enter the credentials and, when applicable, the provider's base URL.
4. Set the default model and enable the models you want to use.
5. Save the configuration. Open **Defaults** to choose the global provider and model or a model pool.

**Connections** lists saved Models, Decision, and Search & Web configurations
together. Search by connection, provider, or model, and filter by type or status.
Choose **Configure** to open the full connection editor. Model connections have
**Models**, **Decision**, **Connection**, and **Advanced** tabs; switching tabs retains unsaved
changes. Each configuration keeps its own credentials, including configurations
with the same name in different categories.

A connection's default model and the global model selection are separate
settings. Model discovery preserves manually configured model IDs, and saving
a connection does not require a successful connection test.

The repository includes adapters for OpenAI-compatible, Anthropic, and Google
model services. The dashboard's provider catalog is the place to see the
options supported by your installed release.

## Choose a model for a task

Use the model selector in chat to choose an enabled model. An
[agent](./agents) can also define its own provider and model settings.

A provider's model identifier must match what that service accepts. A display
name is not always the API identifier. If a request fails, check the model name,
credentials, endpoint, and account access before changing other settings.

## Define fallback priorities

A [model pool](./model-pools) is an ordered list of models from your configured
providers or accounts. Select a pool in chat or agent settings to use its
priority and recovery behavior.

Pools switch on certain provider failures, not on every error. They do not
restart a response that has already started producing output. Read the
[pool recovery rules](./model-pools#priority-and-recovery) before depending on
fallback behavior.

## Local runtime and model requests

Your runtime and runtime data are local. Using a remote model still sends
conversation content and relevant tool results to that provider. Model usage and
any service charges remain subject to the provider account you configured.

An OpenAI-compatible endpoint can point to another compatible service. Model
capabilities, tool support, and endpoint behavior depend on that service.

## Other provider settings

Use the shared catalog and connection list for **Search & Web** and **Decision**
configurations. **Defaults** manages the default Decision connection and a default
Web connection for each service. **Execution Environments** remains a separate tab.
These configurations are separate from the LLM provider needed for your first conversation.

## Google Decisions

Google Decisions evaluates text or JSON using `gemini-decision-1`. It supports
yes/no probabilities (`noul`), categorical choices (`choice`), and ordered rubric
scores (`score`). See the [Google Decisions API documentation](https://aistudio.google.com/docs/decisions-api).

1. Open **Settings > Providers > Provider catalog**, filter by **Decision**, and select **Google Gemini Decisions**.
2. Enter a configuration name and a Gemini API key. If the key is blank, the
   runtime checks `GEMINI_API_KEY`, then `GOOGLE_API_KEY`.
3. Keep the default model `gemini-decision-1` and base URL
   `https://generativelanguage.googleapis.com/v1beta`, or supply a compatible endpoint.
4. Use **Test Connection** to run a small inference request, then save.
5. Choose the saved configuration as the default decision provider.

Creating a configuration does not change the current default. Google decision
credentials are managed separately from chat model credentials. Stored keys are
encrypted and are not returned by the dashboard APIs.

The **Decision** tab in a Google or Cloudflare model connection also lets you
select, create, edit, and test a Decision connection for that provider. Its
model, endpoint, timeout, retry policy, and credentials are independent of the
Models settings. Choose **Use as Decision default** after saving, or select it
under **Defaults > Default Decision connection**. Defaults shows the selected
Decision model and endpoint and links to its configuration. Select
**Legacy / environment fallback** to clear the default after confirmation.

Configure routing mode and confidence thresholds under **Settings > Decisions &
Routing**. The existing `clef_only` mode uses whichever decision provider is
selected; `cascade` falls back to the chat model on low confidence or provider
errors. Changing the default applies to subsequent decisions without interrupting
requests already in flight. Existing Cloudflare configurations remain available.

The adapter uses the existing `DecisionClient` interface. Dictionary states are
serialized to JSON text in Google's `input` field. `noul` thresholds are applied
locally; token usage and score legends remain available in `DecisionResult.raw_response`.
Google-specific limits are validated before sending requests, including 2–255
choice options or score levels. Images, PDFs, audio, and video are not supported
by this integration yet.
