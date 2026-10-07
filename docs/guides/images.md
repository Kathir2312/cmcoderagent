# Images: screenshots, error dialogs, diagrams

You can give cmcoder an image with your message: a screenshot of an error, a
dialog, a stack trace you can't copy, a UI to build, a diagram.

## Attaching an image

| Where | How |
|---|---|
| VS Code, Visual Studio, Eclipse, NetBeans | **Ctrl+V** in the chat's input (Mac: Cmd+V), **drag** an image file onto the chat, or the **Image** button next to **@** |
| Terminal (`cmcoder`, `cmcoder --tui`) | **Alt+V** pastes the clipboard's image (**Ctrl+V** too, where the terminal lets it through: Windows Terminal keeps Ctrl+V for text), or **drag** an image file into the terminal (or paste its path) |

- In the IDEs, the image appears as a thumbnail above the input: **×** removes it.
- In the terminal, the prompt gets a placeholder, `[Image #1]`. Delete the
  placeholder and that image isn't sent. You can refer to it in your text:
  "compare [Image #1] with [Image #2]".
- You can send an image without text.
- **PNG, JPEG, GIF and WebP**; at most **5** per message, up to 20 MB each.
  Large screenshots are made smaller automatically (1568 pixels on the longer
  side), which keeps them quick and cheap for the model.

On Linux, the terminal reads the clipboard with `wl-paste` (Wayland) or
`xclip` (X11): install `wl-clipboard` or `xclip` if Alt+V says there's no image.

## Which model sees it

Not every model can read images. cmcoder knows which can from your gateway
(LiteLLM's model info, Open WebUI's model capabilities), from the model's name
(`-vl`, `vision`, `llava`, ...), or from your settings.

- **Your main model sees images** (a Qwen-VL, for example): it gets the image itself.
- **Your main model is text only** (most coding models, such as Qwen3 and
  Qwen3-Coder): set a **vision model**. It describes the image (every piece of
  text in it exactly: code, errors, logs, names; then what it shows), and your
  main model gets that description. The chat says so: "*qwen2.5-vl-7b described
  the image for the model*".
- **Neither:** the message isn't sent, and cmcoder says how to fix it.

## Setting a vision model

In `~/.cmcoder/settings.json`, next to `model`:

```json
{
  "model": "corp:qwen3-27b",
  "visionModel": "corp:qwen2.5-vl-7b"
}
```

Use a model your gateway serves that can read images (ask your AI team which).
`cmcoder doctor` shows what will happen with images.

If cmcoder gets a model wrong (it thinks a model can't see images, or the other
way round), say so in `modelProfiles`:

```json
{
  "modelProfiles": [{ "match": "corp-multimodal*", "vision": true }]
}
```

## Privacy

Images go to your company's AI gateway, like your messages, and are saved with
the conversation in `~/.cmcoder` (so `/resume` and History bring them back).
Don't paste screenshots with secrets in them.
