# First-time setup (once, for every way you use cmcoder)

cmcoder needs to know your company's AI gateway and your API key. You do this
once; the terminal and every IDE (VS Code, Visual Studio, Eclipse, NetBeans)
use it.

Your team may already have done this for you: if your company's settings came
with the build, cmcoder creates `settings.json` from them the first time it
starts, and you only need your API key (step 2). If `cmcoder doctor` passes,
or the chat answers, skip this page.

## The quick way: `cmcoder setup`

In a terminal:

| | With the terminal package | Without it |
|---|---|---|
| Windows, Command Prompt | `cmcoder setup` | `"%USERPROFILE%\.cmcoder\bin\cmcoder.exe" setup` |
| Windows, PowerShell | `cmcoder setup` | `& "$env:USERPROFILE\.cmcoder\bin\cmcoder.exe" setup` |
| macOS, Linux | `cmcoder setup` | `~/.cmcoder/bin/cmcoder setup` |

(Every IDE puts cmcoder in `.cmcoder\bin` the first time you open its chat;
the terminal package installs it there.)

It asks for the gateway's address, your API key (into the keychain, not
shown), then lists the gateway's models for you to pick the main one, a
small fast one and one that reads images. It writes your `settings.json`,
keeping anything else in it. Run it again to change something: Enter keeps
each current answer. Then `cmcoder doctor` checks everything.

The rest of this page is what it does, for doing it by hand.

## 1. Your gateway

Edit the file **`%USERPROFILE%\.cmcoder\settings.json`** (Windows) or
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
- `visionModel` (optional): a model on the gateway that can read images, for
  when your main model can't ([images.md](images.md)).
- **Open WebUI** instead of LiteLLM: add `"type": "openwebui"` to the provider
  and use the Open WebUI address; the key is an Open WebUI API key
  (Settings → Account → API keys).

`settings.example.json` next to it lists every setting.

## 2. Your API key

The key goes into your operating system's keychain (Windows Credential
Manager, macOS Keychain, the Linux secret service), never into a file.

- `cmcoder setup` asks for it; or `cmcoder login` (only the key), and paste
  it (it isn't shown).
- Without the terminal package, run the program by its path, as in the
  table above, with `login`. The key is shared: one `login` is enough for
  the terminal and every IDE.

Never put the key in `settings.json`, a script, or a chat message.

## 3. Check

```
cmcoder doctor
```

It checks, and tells you what to fix: the gateway's name (VPN), proxies, the
certificate chain, the key, the model list, streaming, and tool calling. In
Visual Studio, Eclipse and NetBeans, **Copy Diagnostics** includes the same
report.

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
| The program (one for the terminal and every IDE) | `~/.cmcoder/bin` |
| Your settings | `~/.cmcoder/settings.json` |
| Every setting, as an example | `~/.cmcoder/settings.example.json` |
| A project's shared settings (commit them) | `<project>/.cmcoder/settings.json` |
| Your "always allow" answers for a project | `<project>/.cmcoder/settings.local.json` |
| Saved conversations | `~/.cmcoder/` (per project) |
| The API key | the OS keychain |

A project's own settings can't change the gateway (so a repository can never
receive your key), and its riskier settings apply only once you trust the
project (`cmcoder trust` in its folder; VS Code's own workspace trust).
