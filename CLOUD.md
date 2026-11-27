# Cloud control and assisted development

Cloud access starts offline. Each generation, retry budget and fallback destination belongs to an approval. Start with a dedicated profile. No local model, GPU or provider SDK is required.

## Configure an account

In the source checkout, use `python -m raya` wherever the examples use `raya`. Set `RAYA_PROFILE` to a private directory or supply `--profile DIRECTORY` before the command. The HUD's **AI control center** exposes route configuration, masked credentials, discovery, account management, quota history, evaluations and development tasks. Network checks run on demand.

1. Run `raya cloud-init` to lock this profile to zero additional spending. Configure routes with **Routes** or `ai-config`. Saving a routes map replaces the previous map. Choose the actual provider model ID.
2. Run `raya secret-set ai-openrouter` or `raya secret-set ai-gemini`. Enter the secret at the masked prompt. Named credentials use `ai-PROVIDER-ALIAS` and the route field `credential`. Use a distinct account alias for each principal. The profile and vault must be private to the OS user.
3. Run the following commands in a POSIX shell, replacing the example model with a current ID. On Windows the HUD accepts the JSON directly.

```sh
raya ai-config '{"offline":false,"routes":{"free":{"provider":"openrouter","model":"REPLACE_WITH_EXACT_ID:free","account":"personal","pool":"openrouter-personal"}}}'
raya cloud-discover '{"route":"free"}'
raya cloud-quota '{"route":"free"}'
raya cloud-report
```

Use `cloud-catalog` for the observed model IDs, parameter support and token limits where advertised. Discovery reads a bounded catalog page. Missing models and missing account evidence stay ineligible. Check the proposal returned by `generate`, then use `approve` with its ID and digest. A failed health check requires successful rediscovery. Refresh discovery after five minutes or a credential, route or account change.

| Provider | Connection and evidence | Strict generation |
| --- | --- | --- |
| OpenRouter | Authenticated model catalog, per-model price fields, key tier and optional credit balance. | Exact `:free` model, zero in every published price field, free-tier key, zero max prices and disabled provider fallback. |
| Google Gemini | Gemini OpenAI-compatible catalog and generation. Store a separate short-lived Google read token using `secret-set google-billing-read`. Configure the numeric `project` belonging to the Gemini API key. | API Keys lookup binds the key to the project. Cloud Billing must report billing disabled and no attached billing account, including at admission. The model must be listed. |
| Groq, SambaNova, NVIDIA NIM, Mistral | OpenAI-compatible catalog and chat transports on fixed HTTPS origins. Use provider IDs `groq`, `sambanova`, `nim`, `mistral`. | Account-level zero-overage evidence unavailable. |
| Cloudflare Workers AI, Cohere | Workers AI uses the account-scoped search and chat endpoints. Set the 32-character Cloudflare account ID. Cohere uses its v1 catalog and compatibility chat endpoint. | Compute allowance and trial entitlement remain unknown without enforceable account evidence. |
| OmniRoute | Loopback GET /v1/models sends the configured bearer. The OpenAI-compatible list works with an absent build-version header. Management quota has a separate version check. | Upstream price, account and fallback cannot be established from model IDs, gateway balances or reported zero cost. Generation is blocked. |
| Sign in with ChatGPT | Conditional independent public-client OAuth and text Responses transport. PKCE, state, loopback redirect, refresh, account switch and logout are covered by fixtures. | Independent client registration, live interoperability and a verifiable subscription ceiling remain prerequisites. No accounts have been certified. |

For independent ChatGPT registration, use `cloud-register` only with an assignment from OpenAI covering the client, scopes, exact loopback callback and Responses destination. Fields are `client_id`, `scopes`, `port`, `responses_url`, `reference`, `owner_confirms_registration`, `distribution="open-source"` and `paid_product=false`. Run `cloud-login`, `cloud-refresh`, `cloud-switch` or `cloud-logout` with an `account` alias. Revocation is attempted when the issuer advertises it. Use the provider account controls if remote revocation fails. The integration supplies no client registration and imports no browser or Codex credentials.

## Quotas and routing

`cloud-report` shows timestamped provider headers, credit or gateway observations, manually entered ceilings, local attempts, reported token categories, known cost and missing cost counts. `cloud-quota` reads the selected provider where supported. OpenRouter credits may require a management credential with appropriate access. OmniRoute accepts `{"route":"owner-omni","key_id":"MANAGEMENT_KEY_ID"}` against its token-limits endpoint when the selected bearer has access. A gateway or provider may return UNKNOWN. Manual subscription, grant, compute and remaining-credit observations are informational.

```sh
raya cloud-limits '{"pool":"openrouter-personal","limits":{"requests_minute":3,"tokens_day":18000,"requests_month":100}}'
raya cloud-observation '{"pool":"openrouter-personal","classification":"grant","reference":"owner account dashboard","counters":{"credits_remaining":10}}'
```

Minute and day ceilings use rolling windows. Month ceilings use UTC calendar months. To cap one dimension use `scope` (provider, account, project or model) and give its identity in `pool`. Map a direct route and a gateway route for the same upstream to the same pool, account and project, with the gateway's `upstream` set. The ledger reserves requests and a conservative token estimate transactionally before sending. Complete usage replaces estimated tokens. Ambiguous attempts retain the reservation. Other clients can consume quota after the last provider observation. Unreported cost stays unknown. A positive reported charge quarantines the route.

Manual funding declarations never enable dispatch. Hosted tools, unknown billing, paid fallback and automatic purchases are excluded from strict admission. The one tool evaluation declares a local function signature and checks the returned arguments without executing any external tool. Legacy DAY_05 and DAY_06 transports can be explicitly selected in a **separate, unlocked** profile. Only enable legacy billing for a service you have independently authorized.

Use `cloud-eval` with a route and kind `reasoning` or `tools`, approve the proposal, then call `cloud-score` with the request ID. Development runs also score feature, repair and optimization outcomes. Rankings use measured pass rates, local quota headroom, token efficiency and latency. No vendor benchmarks or scores ship with the source.

## Verify OmniRoute on the owner's Windows host

From the checkout, run a catalog-only check. Omit `--store-key` if the profile already holds the gateway key. Exit code 0 means the catalog was readable. No generation request or development task is created.

```powershell
$env:RAYA_PROFILE = Join-Path $env:LOCALAPPDATA 'RAYA-Day07'
py -3.12 tools/day07_acceptance.py --profile $env:RAYA_PROFILE --omniroute --store-key --catalog-only
```

On the reported v3.8.48 installation, look for `state=ready`, `route_class=CLIENT_API`, `catalog_version=model-metadata-v1:static` and the advertised IDs. The build version can be null. A 200 records that the request carrying the bearer succeeded. To verify the key itself, the documented read-only `POST /api/cloud/auth` validates it. In PowerShell 7 the following keeps the key out of command history and discards the returned connection metadata:

```powershell
$key = Read-Host 'OmniRoute client key' -AsSecureString
try {
    $check = Invoke-WebRequest -Method Post -Uri 'http://127.0.0.1:20128/api/cloud/auth' -Authentication Bearer -Token $key -AllowUnencryptedAuthentication -MaximumRedirection 0
    if ($check.StatusCode -eq 200 -and ($check.Content | ConvertFrom-Json).connections -is [array]) { 'Gateway key validated' } else { 'Key validation unconfirmed' }
} finally { Remove-Variable key,check -ErrorAction SilentlyContinue }
```

**Verify the selected upstream and fallback before relying on a route.**

1. In the local gateway management UI, inspect the exact client key's allowed models, provider connections and account. For an explicit alias, `POST /api/cloud/model/resolve` with `{"alias":"YOUR_ALIAS"}` can identify its configured provider/model. A 404 leaves resolution unknown. For combos and automatic IDs inspect all enabled model-to-combo mappings, nested steps, eligible accounts, retries, alternate providers and fallback or parallel branches. The documented management reads include `GET /api/model-combo-mappings`, `GET /api/models/alias` and `GET /api/combos`. Use management credentials only in the owner's gateway.
2. Check the **upstream provider** for the exact account, model, current rates and overage policy. A gateway price of zero can mean unpriced. Verify that the effective gateway policy pins the authorized target and prevents substitution on failure. Save the account reference, model, configuration revision and observation time locally, without keys. An alias resolution or successful historical request cannot establish future fallback behavior. R.A.Y.A keeps gateway generation blocked while effective upstream and spend enforcement remain unverified.
3. For a path R.A.Y.A can verify directly, configure an authorized OpenRouter route named `free` as above. Use the exact upstream model ID and its own account key. These PowerShell commands read provider evidence and show the routing controls R.A.Y.A would send, without inference:

```powershell
$d = '{"route":"free"}' | py -3.12 -m raya cloud-discover - | ConvertFrom-Json
$r = py -3.12 -m raya cloud-report | ConvertFrom-Json
$r.routes.free | Select-Object model,price_class
$d.key | Select-Object is_free_tier
$d.direct_request_policy | ConvertTo-Json -Depth 3
```

For this direct route require `verified_free`, a free-tier key, `allow_fallbacks=false`, `require_parameters=true` and zero price ceilings. Use the route by name with an empty R.A.Y.A fallback list. This evidence applies only to the direct route. [OmniRoute catalog implementation](https://github.com/diegosouzapw/OmniRoute/blob/v3.8.48/src/app/api/v1/models/catalog.ts) · [OmniRoute management API](https://github.com/diegosouzapw/OmniRoute/blob/v3.8.48/docs/reference/API_REFERENCE.md)

## Local Windows acceptance

Run from the Git checkout. Set up Python 3.11+ and Git. To execute candidate code on Windows, install a WSL Linux distribution with /usr/bin/python3 and libseccomp.so.2. Linux hosts use the same seccomp runner natively. Missing confinement stops candidate execution. No packages are installed by the development engine.

```powershell
$env:RAYA_PROFILE = Join-Path $env:LOCALAPPDATA 'RAYA-Day07'
py -3.12 tools/day07_acceptance.py --profile $env:RAYA_PROFILE --omniroute --store-key
```

The first command-line run requests a masked gateway key and reads the actual catalog from this machine's 127.0.0.1:20128. It also checks all three development targets and persists paused tasks when no eligible route exists. Exit code 2 means that all three tasks have yet to reach proposed commits. For a live run, first configure a qualifying Gemini or OpenRouter route in this same profile via the HUD or CLI, then run:

```powershell
py -3.12 tools/day07_acceptance.py --profile $env:RAYA_PROFILE --live --route free
```

Read every source disclosure, destination and proposed diff before approving. Each run starts three fresh task records. To resume a previous task, use `dev-status` and `dev-step` with its ID. `dev-review` returns the approval for creating its isolated proposal commit. `dev-cancel` and `dev-rollback` cancel pending work and remove engine-owned proposals. A remote provider may finish a request already accepted.

The three tasks cover a feature, a seeded boundary defect and an optimization. Retrieval reads one allowlisted target from a fixed Git commit. Planning and patching require separate approvals. The engine permits three patch attempts and compares returned values to host-controlled cases. The optimization must reduce median CPU time by at least 20 percent across three samples. Candidate code runs in a separate, resource-limited process after file and socket denial self-checks. Proposals modify only the target in a detached worktree and create a separate proposal ref on approval. Merging, pushing, dependency installation and arbitrary repository test commands are outside the runner. No source solutions for the three acceptance tasks are supplied.

## Protocol references

- Google documents project binding and billing state. [API key lookup](https://cloud.google.com/api-keys/docs/reference/rest/v2/keys/lookupKey) · [Project billing](https://cloud.google.com/billing/docs/reference/rest/v1/projects/getBillingInfo) · [Gemini billing](https://ai.google.dev/gemini-api/docs/billing)
- OpenRouter documents catalog prices, routing controls and account limits. [API reference](https://openrouter.ai/docs/api-reference/overview) · [Provider routing](https://openrouter.ai/docs/features/provider-routing) · [Limits](https://openrouter.ai/docs/api-reference/limits)
- OmniRoute documents its version header and gateway telemetry. [v3.8.48 API reference](https://github.com/diegosouzapw/OmniRoute/blob/v3.8.48/docs/reference/API_REFERENCE.md)
- Provider compatibility guides. [Groq](https://console.groq.com/docs/openai) · [SambaNova](https://docs.sambanova.ai/) · [NVIDIA](https://build.nvidia.com/) · [Cloudflare](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/) · [Mistral](https://docs.mistral.ai/) · [Cohere](https://docs.cohere.com/docs/compatibility-api)
