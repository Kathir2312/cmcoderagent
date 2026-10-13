# Gateway and models

cmcoder runs on your PC; the model runs on your company's AI gateway. Any
**OpenAI-compatible** server works: a LiteLLM gateway (for example in front of
Qwen3 models), vLLM, Ollama's `/v1`, or an **Open WebUI** server.

First time on a PC: `cmcoder setup` does all of this with questions
([../guides/setup.md](../guides/setup.md)). This page is the detail, and the
setup for a team.

## Connect to the gateway

`~/.cmcoder/settings.json`:

```json
{
  "providers": {
    "corp": { "baseUrl": "https://ai-gateway.example.com/v1" }
  },
  "model": "corp:qwen3-27b",
  "smallFastModel": "corp:qwen3-7b"
}
```

- A **provider** is a gateway with a name (`corp`); a model is
  `provider:model`. You can list several providers (LiteLLM and Open WebUI
  side by side).
- Model names are whatever the gateway serves: `cmcoder models` lists them
  (`--provider <name>` for one gateway).
- Then store the API key in the OS keychain (never in a file) and check:

```bash
cmcoder login                  # or: cmcoder login --provider corp
cmcoder doctor
```

`doctor` checks DNS (VPN), proxy settings, the TLS certificate chain, the API
key, the model list, streaming, the Qwen3 thinking switch and tool calling,
and says what to fix.

**Open WebUI:** give the provider `"type": "openwebui"` and the Open WebUI
address; the key is an Open WebUI API key. What cmcoder does differently for
it (Ollama's context window, thinking): [openwebui.md](openwebui.md).

```json
{ "providers": { "webui": { "type": "openwebui", "baseUrl": "https://chat.example.com" } },
  "model": "webui:qwen3:32b" }
```

## Which model does what

| Setting | Used for | Typical choice |
|---|---|---|
| `model` | the conversation: reading, editing, running, answering | your best coding model (e.g. a Qwen3 27B–32B) |
| `smallFastModel` | side jobs: conversation titles, compaction summaries, the `explore` subagent | a small model of the same family (e.g. Qwen3 7B–8B) |
| `subagentModel` | the `explore` subagent (else `smallFastModel`) | optional |
| `visionModel` | describes images for a text-only main model | a vision model (e.g. Qwen2.5-VL) ([images.md](images.md)) |
| `rag.embeddingModel` | code search's index | an embedding model ([code-search.md](code-search.md)) |

`/model` (in the chat) or `--model` switches the main model for a session.

## Company networks

- **Certificates.** cmcoder trusts the OS certificate store (Windows, macOS
  Keychain, the Linux CA bundle), so a company root CA that IT installed just
  works. Otherwise set `"caCertPath": "/path/to/company-root-ca.pem"` on the
  provider (or `CMCODER_CA_CERT`). Certificate verification is never switched
  off.
- **Proxies.** `HTTPS_PROXY` and `NO_PROXY` are respected. If the gateway is
  internal, add its domain to `NO_PROXY`.
- **Extra headers** for the gateway: `"headers": {"Name": "value"}` on the
  provider, or `CMCODER_CUSTOM_HEADERS`. Timeouts: `connectTimeout` (15 s),
  `readTimeout` (300 s), `maxRetries` (2).

## Model profiles

cmcoder knows what common models can do from their names (Qwen3's thinking
switch, parallel tool calls, context window, vision). For anything else, or to
override, add a profile:

```json
"modelProfiles": [
  { "match": "qwen3*", "contextWindow": 40960, "parallelToolCalls": true },
  { "match": "corp-multimodal*", "vision": true }
]
```

The first matching profile wins (`*` is a wildcard). Other keys include
`maxOutput`, `toolCalling`, `thinkingSwitch`, `topP`, `extraBody` (sent with
every request) and `steerBashFileWork`
([permissions.md](permissions.md#file-work-goes-through-the-file-tools));
every key, with its default, is in `src/cmcoder/providers/profiles.py`.

## The context window

Qwen3 is often served with a 32K-token window, which an agent fills quickly.
cmcoder finds the real window by asking the server once (and learns it from
the server's error if a request is ever too long); `cmcoder doctor` shows the
value and where it came from. Long conversations are summarised
automatically: [sessions.md](sessions.md#long-conversations-the-context-window).
If you can, have the gateway serve a longer context.

## For the team

- **One gateway for everyone:** an administrator ships the provider and models
  in `branding/settings.template.json`, so developers only run `cmcoder login`
  ([../admin/README.md](../admin/README.md#the-settings-template)).
- **Locked to the company gateway:** managed settings (`lockProviders`) stop
  anyone pointing cmcoder elsewhere
  ([../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change)).
- A repository's settings can never change the gateway, so a repository you
  clone can't receive your key ([settings.md](settings.md#a-projects-settings-and-trust)).

**Privacy:** each turn sends your prompt, the file contents the agent reads and
command output to the gateway. Gateway admins may be able to see that traffic
in their logs.
