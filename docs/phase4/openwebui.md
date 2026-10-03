# Using cmcoder with Open WebUI

cmcoder can use an [Open WebUI](https://openwebui.com) server as its gateway,
like the LiteLLM gateway: Open WebUI has an OpenAI-compatible API under `/api`.
Checked against Open WebUI 0.11.4 (with both kinds of model it can serve:
Ollama and OpenAI-compatible servers such as vLLM).

## 1. Get an API key

In Open WebUI: **Settings → Account → API keys → Create new secret key**.
If there's no such section, an admin turns it on: **Admin Panel → Settings →
General → Enable API Keys** (or `ENABLE_API_KEYS=True` on the server).

## 2. Configure cmcoder

`~/.cmcoder/settings.json`:

```json
{
  "providers": {
    "webui": { "type": "openwebui", "baseUrl": "https://chat.example.com" }
  },
  "model": "webui:qwen3:32b",
  "smallFastModel": "webui:qwen3:8b"
}
```

- `baseUrl` is Open WebUI's address, the one you open in the browser (`.../api`
  works too).
- Model names are Open WebUI's model ids; `cmcoder models` lists them (Open
  WebUI's "arena" entries are left out).
- Store the key, then check everything:

```bash
cmcoder login --provider webui
cmcoder doctor
```

The same works with environment variables: `CMCODER_BASE_URL`,
`CMCODER_PROVIDER_TYPE=openwebui`, `CMCODER_API_KEY`, `CMCODER_MODEL`.
Certificates, proxies and `caCertPath` work as for LiteLLM.

## What cmcoder does differently for Open WebUI

`cmcoder doctor` says which kind of server is behind each model:

| Behind Open WebUI | What cmcoder does |
|---|---|
| **Ollama** | Sends the context window as Ollama's `num_ctx` with every request, because Ollama otherwise uses a small default and **silently drops the start of longer prompts**. The window is cmcoder's usual one for the model (32,768 for Qwen3), never more than the model was trained for (asked from Open WebUI). Switches thinking off with Ollama's `think` option for quick jobs. |
| **OpenAI-compatible** (vLLM, LiteLLM, ...) | Nothing special: the context window is learned from the server's own limit, as with LiteLLM. |

A larger window needs more GPU memory on the Ollama server. To change it, set
it for the model in `modelProfiles` (that value is what's sent):

```json
"modelProfiles": [{ "match": "qwen3:32b", "contextWindow": 65536 }]
```

## If tools don't work

cmcoder sends its tools with each request, and Open WebUI passes them to the
model. If `cmcoder doctor` says no tool call came back:

- In Open WebUI, set the model's **Function Calling** to **Native**: Admin
  Panel → Settings → Models → the model → Advanced Params. (Native is the
  default in recent versions.)
- With Ollama, the model itself must support tools (e.g. Qwen3, Llama 3.1+).
- Otherwise cmcoder can describe the tools in the prompt instead:
  `"modelProfiles": [{ "match": "the-model", "toolCalling": "prompted" }]`.

## Notes

- Open WebUI features meant for its chat page (its own tools, knowledge,
  filters) don't apply to cmcoder's requests, except filters an admin applies
  to all requests of a model.
- A model's system prompt set in Open WebUI is still added by Open WebUI to
  each request; for cmcoder, a model without one works best.
