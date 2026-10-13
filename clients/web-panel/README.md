# The shared chat panel

cmcoder's chat panel and Agent Navigator, the same files in every IDE (VS Code,
Visual Studio, Eclipse, NetBeans). The IDE side starts `cmcoder --protocol
stdio`, shows these pages in its embedded browser and relays messages between
them; the pages never talk to cmcoder directly.

```
npm ci            # installs marked (Markdown), esbuild, TypeScript, Playwright
npm run build     # -> dist/
npm test          # every test once per way an IDE connects (needs a Chromium:
                  #    npx playwright install chromium)
npm run generate  # src/protocol.ts from cmcoder's protocol (CI checks it's current)
```

(The VS Code extension's `npm ci` installs this package too, and its build
copies `dist/` into the extension.)

## Files an IDE ships (from `dist/`)

| File | What |
|---|---|
| `chat.js`, `chat.css` | the chat panel (a tool window / side panel) |
| `navigator.js`, `navigator.css` | the Agent Navigator (an editor tab) |
| `test-driver.js` | **tests only, never shipped**: see below |

Each page is this HTML, served from the plugin itself (never `file://` from a
folder others can write, never remote):

```html
<!DOCTYPE html>
<html lang="en"><head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src SOURCE; img-src SOURCE data:; script-src 'nonce-NONCE'">
  <link href="SOURCE/chat.css" rel="stylesheet">
</head>
<body data-product="cmcoder" data-icon="SOURCE/icon.png" data-host="function">
  <div id="app"></div>
  <script nonce="NONCE" src="SOURCE/chat.js"></script>
</body></html>
```

`SOURCE` is where the IDE serves the plugin's files; `NONCE` a new random value
per page. `data-product` is the brand name (`branding/brand.json`),
`data-icon` the brand icon's address; `data-host="function"` only where the
IDE adds `cmcoderHostPost` (Eclipse, NetBeans).

## Messages

The types are in `src/messages.ts` (`ToWebview`/`FromWebview` for the chat,
`ToNavigator`/`FromNavigator` for the navigator); protocol events from cmcoder
are passed in unchanged inside `{"kind": "event", "event": ...}`.

- **To the page**: run `window.postMessage(<message>, "*")` in it (VS Code does
  this itself).
- **From the page** (`src/bridge.ts` picks the first that exists):

| IDE | The page calls |
|---|---|
| VS Code | `acquireVsCodeApi().postMessage(message)` |
| Visual Studio (WebView2) | `window.chrome.webview.postMessage(message)`: the IDE gets it in `WebMessageReceived` |
| Eclipse (SWT), NetBeans (JavaFX) | `window.cmcoderHostPost(jsonText)`: a function the IDE adds (`BrowserFunction`; in JavaFX an `alert()` with a per-load secret) |

Such an IDE also puts `data-host="function"` on `<body>`, so the page uses the
function even where `window.chrome.webview` exists (SWT's browser on Windows is
WebView2). The function can be added after the page loads: messages wait in the page
until it exists (the first is `{"kind": "ready"}`, which tells the IDE to send
the state). The IDE must accept messages only from its own page.

## Theme

`theme.json` lists every CSS variable the pages use (VS Code's names, which
VS Code sets itself), what each is for, and a dark and a light default. Other
IDEs set each one on `:root` from their own theme (from script, e.g.
`document.documentElement.style.setProperty(name, value)`), and again when the
theme changes. A test fails if the pages use a variable that isn't listed.

## Security rules (every IDE)

- Only the plugin's own files; no remote content, no `eval`, no inline scripts
  (the policy above). Model output is Markdown rendered after escaping and an
  allowlist of tags; links are never followed by the page: it sends
  `openLink` and the IDE opens only `http(s)` addresses in the system browser.
- Navigation away from the page, new windows, and (in release builds) dev
  tools and the default context menu are blocked by the IDE.

## Testing an IDE's panel (`test-driver.js`)

Test builds load `dist/test-driver.js` after `chat.js`. It adds
`window.__cmcoderTest.run(action, selector, value?)`, which returns JSON text,
so a test can drive the real panel through the embedded browser's "run
JavaScript" call (JavaFX `executeScript`, SWT `Browser.evaluate`, WebView2
`ExecuteScriptAsync`):

| Action | Returns |
|---|---|
| `text`, `texts` | the text of the first / every match |
| `count`, `visible`, `checked` | number, true/false |
| `click` | true if it clicked (visible and enabled) |
| `fill` (value), `press` (key) | true |
| `style` (CSS property) | the computed value (theme checks) |
