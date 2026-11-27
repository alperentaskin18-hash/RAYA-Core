# AI providers

A new profile starts with generation disabled. Saving AI settings enables the unified engine. Routes, preferences and usage records live in that profile. No provider SDK is required.

Strict spending admission is the default. Read the [cloud control guide](CLOUD.md) for verified free routes. For the operator-managed local server example below, use a separate profile and explicitly authorize legacy billing.

## Run locally

Start an operator-supplied Ollama or llama.cpp server and choose a model it already serves. Use its actual model ID. The example uses the OpenAI-compatible chat endpoint of a local llama.cpp server on port 8080.

```sh
raya set '{"field":"billing_policy","value":"legacy"}'
raya ai-config '{"offline":true,"routes":{"cpu":{"provider":"llama.cpp","model":"fixture","port":8080,"context_tokens":4096,"features":["stream"]}},"preferred":"cpu"}'
raya providers
raya ai-health '{"route":"cpu"}'
raya generate '{"prompt":"Write one sentence.","stream":true,"max_output_tokens":32}'
raya approve '{"id":"ID_FROM_PROPOSAL","digest":"DIGEST_FROM_PROPOSAL"}'
raya ai-usage
```

Review the proposal before supplying its ID and digest. During streaming, CLI text deltas are JSON lines on stderr. Stdout contains one final receipt with the state. Treat deltas as provisional until the receipt is complete. For interactive plain text, use `raya ask "your prompt"`. A second terminal can run `raya ai-cancel '{"id":"ID_FROM_PROPOSAL"}'` against the same profile to cancel pending or active generation. Remote processing or billing can continue after a disconnect. An interrupted process can leave an in-progress receipt; inspect it before issuing a new request.

## Configure models

Use **Provider settings** in `raya hud` or use `raya ai-config` to read preferences. A JSON patch changes the supplied settings. Supplying `routes` replaces the entire route map. Local defaults are port 11434 for Ollama and 8080 for llama.cpp. Cloud origins are fixed and use HTTPS. Store each cloud provider's key with a masked terminal prompt, for example `raya secret-set ai-openai`. The HUD also supports masked entry. To remove the key, run `raya ai-key-remove '{"provider":"openai"}'`.

For cloud access, configure a model route and set `offline` to false. A route requires a provider ID from the table and a model ID from the service. Declare only features supported by that particular model and backend. Plain text is implicit. Optional features are `stream`, `structured` and `tools`. Health checks query the model list and report whether the configured ID appeared on the returned page. A listed model can still reject a feature or lack quota.

| Provider IDs | Wire protocol | Verification |
| --- | --- | --- |
| openai, gemini, xai, deepseek, groq, openrouter | Chat Completions on each service's own origin | Deterministic loopback fixtures |
| anthropic | Claude Messages, including content-block streaming | Deterministic loopback fixtures |
| ollama | Local OpenAI-compatible chat | Deterministic loopback fixtures |
| llama.cpp | Local OpenAI-compatible chat | Fixtures and real CPU inference with synthetic weights for plain and streamed text |

## Routing, limits and recovery

Set `preferred` or per-request `route` to a route alias or `auto`. Automatic selection considers offline mode, credentials, requested features, configured context and request budget. Among eligible routes it deprioritizes recent failures, then prefers local execution and the smallest fitting context. The proposal names the exact selection. Fallback requires an explicit ordered list such as `"fallback":["second-local","cloud"]`. Check every destination in the proposal before approval. OpenRouter acts as a broker; requests disable broker fallback and require parameter support.

A profile defaults to 256 output tokens, a 65,536-token request budget and zero retries. A request can lower these limits. For each attempt the engine reserves the serialized request byte length plus 32 units of overhead and the output cap, against both the configured context and remaining request budget. This conservative estimate is independent of vendor tokenization and billing. `ai-usage` aggregates provider-reported token fields and counts attempts with missing usage. It does not estimate fees.

Per route, up to two retries are configurable. Only a refused connection or HTTP 429/503 permits retry or authorized fallback. Long Retry-After delays defer the endpoint. An incomplete stream, authentication error, or ambiguous transport failure ends the request. Status and usage survive restarts.

## Structured output and tools

Add a `schema` object to request structured output, or a `tools` array to declare functions. Examples of generation input:

```json
{"prompt":"Return a count.","schema":{"type":"object","properties":{"count":{"type":"integer"}},"required":["count"],"additionalProperties":false}}
```

```json
{"prompt":"Look up the item.","tools":[{"name":"lookup","parameters":{"type":"object","properties":{"id":{"type":"string"}},"required":["id"],"additionalProperties":false}}]}
```

The engine validates completed structured responses and tool arguments locally. Supported schema keywords are type, description, enum, properties, required, additionalProperties (boolean) and items. DeepSeek uses JSON-object mode plus the supplied schema in the prompt. A successful tool response contains call IDs, names and decoded arguments. The caller decides whether to execute a function. To continue, propose a new generation with the same prompt and tool declarations, plus `tool_results` containing objects with `id`, `name`, `arguments` and a text `result`. The follow-up requires its own approval. Hosted model availability, quotas and live feature support depend on the provider and model.
