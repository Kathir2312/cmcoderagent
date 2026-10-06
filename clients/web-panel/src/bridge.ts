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
