// How the panel talks to the IDE around it. The same panel runs in four embedded
// browsers; only sending differs:
//
//   VS Code                acquireVsCodeApi().postMessage(message)
//   Visual Studio          window.chrome.webview.postMessage(message)   (WebView2)
//   Eclipse, NetBeans      window.cmcoderHostPost(JSON text)            (a function the
//                          IDE adds: BrowserFunction in SWT, alert() in JavaFX)
//
// Receiving is the same everywhere: the IDE dispatches a `message` event on the
// window with the message as `data` (VS Code does it itself; the others run
// `window.postMessage(<message>, "*")` in the page).
//
// A function the IDE adds can appear only after the page has loaded, so messages
// sent before that (the first is "ready") wait in a queue instead of being lost.
//
// An IDE that adds the function puts data-host="function" on <body>: Eclipse's
// browser on Windows is WebView2 too, so window.chrome.webview exists there, but
// it belongs to SWT (its own messages), not to the plugin.

declare function acquireVsCodeApi(): { postMessage(message: unknown): void };

interface HostWindow {
  chrome?: { webview?: { postMessage(message: unknown): void } };
  cmcoderHostPost?: (json: string) => void;
}

export interface Bridge<Out> {
  post(message: Out): void;
}

export function bridge<Out>(): Bridge<Out> {
  const viaFunction = document.body?.dataset.host === "function";
  if (!viaFunction && typeof acquireVsCodeApi === "function") {
    const api = acquireVsCodeApi();
    return { post: (m) => api.postMessage(m) };
  }
  const w = window as unknown as HostWindow;
  if (!viaFunction && w.chrome?.webview) {
    const webview = w.chrome.webview;
    return { post: (m) => webview.postMessage(m) };
  }
  const queue: Out[] = [];
  let timer: ReturnType<typeof setInterval> | undefined;
  const flush = (): boolean => {
    const send = w.cmcoderHostPost;
    if (typeof send !== "function") return false;
    for (const m of queue.splice(0)) send(JSON.stringify(m));
    if (timer !== undefined) clearInterval(timer);
    timer = undefined;
    return true;
  };
  return {
    post(m) {
      queue.push(m);
      if (!flush() && timer === undefined) timer = setInterval(flush, 25);
    },
  };
}

/**
 * Whether a "message" event comes from the IDE: Eclipse, NetBeans and Visual
 * Studio post from inside the page (window.postMessage); VS Code posts from
 * one of its webview's own frames: same origin as the page (its own
 * vscode-webview:// address), around it or beside it depending on its
 * version. Never taken: a frame inside the page or a window it opened (a
 * hostile page could make one), or a window of another origin.
 */
export function fromHost(e: MessageEvent): boolean {
  const from = e.source;
  if (from === null || from === window) return true;
  if (typeof (from as Window).postMessage !== "function" || !("parent" in from)) return false; // a port or a worker
  const sender = from as Window;
  for (let w: Window = sender; w.parent !== w; ) {
    w = w.parent;
    if (w === window) return false; // a frame inside this page
  }
  try {
    if (sender.opener === window) return false; // a window this page opened
  } catch {
    // another origin's window: decided below
  }
  for (let w: Window = window; w.parent !== w; ) {
    w = w.parent;
    if (sender === w) return true; // a frame around this page
  }
  const origin = window.origin; // (the document's origin: also right in a frame without its own address)
  return origin !== "null" && e.origin === origin;
}

/** Values the IDE puts on <body> for the panel: data-product, data-icon. */
export function hostSettings(): { product: string; icon: string | null } {
  const data = document.body.dataset;
  const icon = data.icon || null;
  // The icon is a picture the IDE serves (no inline styles under the panel's CSP:
  // a value set from script is allowed).
  if (icon && /^(https?|vscode-webview|vscode-resource|file|data):|^[\w./-]+$/i.test(icon)) {
    document.documentElement.style.setProperty("--cmcoder-icon", `url("${icon.replace(/["\\\n]/g, "")}")`);
  }
  return { product: data.product || "cmcoder", icon };
}
