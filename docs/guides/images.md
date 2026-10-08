# Images: screenshots, error dialogs, diagrams

You can give cmcoder an image with your message: a screenshot of an error, a
dialog, a stack trace you can't copy, a UI to build, a diagram.

## Attaching an image

| Where | How |
|---|---|
| VS Code, Visual Studio, Eclipse, NetBeans | **Ctrl+V** in the chat's input (Mac: Cmd+V), **drag** an image file onto the chat, or the **Image** button next to **@** |
| Terminal (`cmcoder`, `cmcoder --tui`) | **Alt+V** pastes the clipboard's image (**Ctrl+V** too, where the terminal lets it through: Windows Terminal and VS Code's terminal keep Ctrl+V for text), **drag** an image file into the terminal (or paste its path), or type **`/image`** (the clipboard's image) or **`/image C:\path\shot.png`** |

- In the IDEs, the image appears as a thumbnail above the input: **×** removes it.
- In the terminal, the prompt gets a placeholder, `[Image #1]`. Delete the
  placeholder and that image isn't sent. You can refer to it in your text:
  "compare [Image #1] with [Image #2]". **No `[Image #1]` in your prompt
  means no image is attached.** If your message talks about an image ("analyse
  the image") and none is attached, cmcoder holds it back once and says how to
  attach one; press Enter again to send it anyway.
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
`cmcoder doctor` checks it with a test image, and your main model too.

If cmcoder gets a model wrong (it thinks a model can't see images, or the other
way round), say so in `modelProfiles`:

```json
{
  "modelProfiles": [{ "match": "corp-multimodal*", "vision": true }]
}
```

## When an image doesn't get through

1. **Which cmcoder is it?** `cmcoder --version` (or the IDE's **Copy
   Diagnostics**, the **Version:** line) shows the version and the build, e.g.
   `0.1.0 (3f2a9c1 2026-10-08)`; just `0.1.0`, with no build in brackets, is
   an older one: install the current one. In a chat, hover over the model name
   at the top: it shows the same. If the chat's cmcoder is too old for images,
   the chat says so when you send one.
2. **Terminal: is it attached?** The prompt must show `[Image #1]` and the
   bottom bar "1 image attached". If not:
   - In **Windows Terminal** and **VS Code's terminal**, Ctrl+V pastes text
     only: press **Alt+V**, or type `/image`.
   - `/image` says "No image in the clipboard": copy the screenshot again
     (Windows: **Win+Shift+S**, then pick an area), or give the file:
     `/image C:\Users\me\Pictures\shot.png`.
3. **IDE chat: is it cmcoder's chat?** VS Code has its own Chat view too
   (Copilot): open cmcoder's from the cmcoder icon in the activity bar. The
   image shows as a thumbnail above the input before you send.
4. **The message went but nothing came back.** Errors show in the chat (✗).
   In VS Code, **cmcoder: Show Log** also has them (`Error (images): …`), with
   cmcoder's version at the start.
5. **Does your model see images?** Run `cmcoder doctor`. It shows your model a
   small test image and says what happened:
   - "*sees images*": all set.
   - "*sees images, but cmcoder doesn't know it*": add the line it gives you
     to `modelProfiles` (for example
     `{"match": "Qwen3.6-27B", "vision": true}`), so images go straight to it.
   - "*can't see images, and no visionModel is set*": set `visionModel`
     (above).
   - "*visionModel …: didn't see the test image*": that model can't read
     images; choose another.

## Privacy

Images go to your company's AI gateway, like your messages, and are saved with
the conversation in `~/.cmcoder` (so `/resume` and History bring them back).
Don't paste screenshots with secrets in them.
