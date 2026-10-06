# First-time setup (once, for every way you use cmcoder)

cmcoder needs to know your company's AI gateway and your API key. You do this
once; the terminal, VS Code and Eclipse all use it.

Your team may already have done this for you (a company settings file). If
`cmcoder doctor` passes, or the chat answers, skip this page.

## 1. Your gateway

Create the file **`%USERPROFILE%\.cmcoder\settings.json`** (Windows) or
**`~/.cmcoder/settings.json`** (macOS, Linux) with what your AI team gives you,
for example:

```json
{
  "providers": {
    "corp": { "baseUrl": "https://ai-gateway.example.com/v1" }
  },
  "model": "corp:qwen3-27b",
  "smallFastModel": "corp:qwen3-7b"
}
```

- `baseUrl`: the gateway's address (LiteLLM: ending in `/v1`).
- `model`: `provider:model`; the model names are whatever the gateway serves
  (`cmcoder models` lists them once your key works).
- **Open WebUI** instead of LiteLLM: add `"type": "openwebui"` to the provider
  and use the Open WebUI address; the key is an Open WebUI API key
  (Settings → Account → API keys).

## 2. Your API key

The key goes into your operating system's keychain (Windows Credential
Manager, macOS Keychain, the Linux secret service), never into a file.

- **Terminal:** run `cmcoder login` and paste the key (it isn't shown).
- **VS Code / Eclipse without the terminal package:** run the cmcoder that
  is inside the extension once, with `login` (or install the terminal package;
  the key is shared, so one `login` is enough for all):
  - VS Code (Windows):
    `%USERPROFILE%\.vscode\extensions\cmcoder.cmcoder-<version>-win32-x64\bin\cmcoder\cmcoder.exe login`
    (macOS, Linux: `~/.vscode/extensions/cmcoder.cmcoder-<version>-<platform>/bin/cmcoder/cmcoder login`)
  - Eclipse: see [eclipse.md](eclipse.md), step 4.

Never put the key in `settings.json`, a script, or a chat message.

## 3. Check

```
cmcoder doctor
```

It checks, and tells you what to fix: the gateway's name (VPN), proxies, the
certificate chain, the key, the model list, streaming, and tool calling. In
Eclipse, **cmcoder: Copy Diagnostics** (Ctrl+3) gives the same report.

## Company networks

- **Certificates:** cmcoder trusts your operating system's certificate store,
  so an internal root certificate that IT has installed just works. Otherwise
  add `"caCertPath": "C:\\path\\to\\company-root-ca.pem"` to the provider.
  Certificate checks are never switched off.
- **Proxy:** `HTTPS_PROXY` and `NO_PROXY` are respected. If the gateway is
  internal, put its domain in `NO_PROXY`.

## Where cmcoder keeps things

| What | Where |
|---|---|
| Your settings | `~/.cmcoder/settings.json` |
| A project's shared settings (commit them) | `<project>/.cmcoder/settings.json` |
| Your "always allow" answers for a project | `<project>/.cmcoder/settings.local.json` |
| Saved conversations | `~/.cmcoder/` (per project) |
| The API key | the OS keychain |

A project's own settings can't change the gateway (so a repository can never
receive your key), and its riskier settings apply only once you trust the
project (`cmcoder trust` in its folder; VS Code's own workspace trust).
