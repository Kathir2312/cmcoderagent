// The bridge (src/bridge.ts) and the test driver (src/test-driver.ts) on their own:
// what every IDE relies on to talk to the panel and to test it.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { chromium, type Browser, type Page } from "playwright";

const root = join(__dirname, "..");
let browser: Browser;

before(async () => {
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
});

async function page(body: string, head = ""): Promise<Page> {
  const p = await browser.newPage();
  const errors: string[] = [];
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("close", () => assert.deepEqual(errors, []));
  await p.setContent(`<!DOCTYPE html><html><head><style>${readFileSync(join(root, "dist", "chat.css"), "utf8")}</style>
    <script>window.sent=[];${head}</script></head><body ${body}><div id="app"></div></body></html>`);
  return p;
}

const sent = (p: Page) => p.evaluate(() => (window as unknown as { sent: unknown[] }).sent);

test("Eclipse/NetBeans: messages wait until the IDE adds its function, then go in order", async () => {
  const p = await page("");
  await p.addScriptTag({ content: readFileSync(join(root, "dist", "chat.js"), "utf8") });
  // The panel already said "ready" and asked for nothing else yet: nothing lost.
  await p.waitForTimeout(100);
  await p.fill("textarea", "a");
  await p.evaluate(() => {
    (window as unknown as { cmcoderHostPost: (j: string) => void }).cmcoderHostPost = (j: string) =>
      (window as unknown as { sent: unknown[] }).sent.push(JSON.parse(j));
  });
  await p.waitForFunction(() => (window as unknown as { sent: unknown[] }).sent.length > 0);
  assert.deepEqual((await sent(p))[0], { kind: "ready" });
  await p.close();
});

test('data-host="function": the IDE\'s function is used even where window.chrome.webview exists (SWT on Windows)', async () => {
  const p = await page('data-host="function"', "window.webviewSent=[];window.chrome={webview:{postMessage(m){window.webviewSent.push(m)}}}");
  await p.addScriptTag({ content: readFileSync(join(root, "dist", "chat.js"), "utf8") });
  await p.evaluate(() => {
    (window as unknown as { cmcoderHostPost: (j: string) => void }).cmcoderHostPost = (j: string) =>
      (window as unknown as { sent: unknown[] }).sent.push(JSON.parse(j));
  });
  await p.waitForFunction(() => (window as unknown as { sent: unknown[] }).sent.length > 0);
  assert.deepEqual((await sent(p))[0], { kind: "ready" });
  assert.deepEqual(await p.evaluate(() => (window as unknown as { webviewSent: unknown[] }).webviewSent), []);
  await p.close();
});

test("the IDE's icon is used; an address that could break out of the CSS is not", async () => {
  const good = await page('data-icon="https://panel.test/icon.png"', "window.chrome={webview:{postMessage(m){window.sent.push(m)}}}");
  await good.addScriptTag({ content: readFileSync(join(root, "dist", "chat.js"), "utf8") });
  assert.equal(await good.evaluate(() => document.documentElement.style.getPropertyValue("--cmcoder-icon")), 'url("https://panel.test/icon.png")');
  await good.close();
  const bad = await page(`data-icon='x") ; background: red; ("'`, "window.chrome={webview:{postMessage(m){window.sent.push(m)}}}");
  await bad.addScriptTag({ content: readFileSync(join(root, "dist", "chat.js"), "utf8") });
  assert.equal(await bad.evaluate(() => document.documentElement.style.getPropertyValue("--cmcoder-icon")), "");
  await bad.close();
});

test("the test driver reads and operates the panel, answering in JSON", async () => {
  const p = await page("", "window.chrome={webview:{postMessage(m){window.sent.push(m)}}}");
  await p.addScriptTag({ content: readFileSync(join(root, "dist", "chat.js"), "utf8") });
  await p.addScriptTag({ content: readFileSync(join(root, "dist", "test-driver.js"), "utf8") });
  const run = (action: string, selector: string, value?: string) =>
    p.evaluate(([a, s, v]) => (window as unknown as { __cmcoderTest: { run(a: string, s: string, v?: string): string } }).__cmcoderTest.run(a, s, v), [action, selector, value] as const);
  await p.evaluate(() => window.postMessage({ kind: "state", state: "ready" }, "*"));
  assert.equal(await run("count", "textarea"), "1");
  assert.equal(await run("fill", "textarea", "hello"), "true");
  assert.equal(await run("press", "textarea", "Enter"), "true");
  await p.waitForFunction(() => (window as unknown as { sent: Array<{ kind: string }> }).sent.some((m) => m.kind === "send"));
  assert.deepEqual((await sent(p)).at(-1), { kind: "send", text: "hello", includeContext: false });
  assert.equal(await run("text", ".msg.user"), JSON.stringify("hello"));
  assert.equal(await run("click", ".nothing-here"), "false");
  assert.equal(await run("visible", "header"), "true");
  assert.match(await run("style", "body", "background-color"), /rgb/);
  assert.match(await run("dance", "body"), /unknown action/);
  await p.close();
});

test("messages are taken only from the page itself or its host frame, never from another window", async () => {
  const p = await page("", "window.chrome={webview:{postMessage(m){window.sent.push(m)}}}");
  await p.addScriptTag({ content: readFileSync(join(root, "dist", "chat.js"), "utf8") });
  // A frame inside the page (as a hostile page could be) posts to it: ignored.
  await p.evaluate(() => {
    const f = document.createElement("iframe");
    f.srcdoc = `<script>parent.postMessage({ kind: "state", state: "stopped", message: "FROM-A-FRAME" }, "*")</script>`;
    document.body.append(f);
  });
  await p.waitForTimeout(300);
  assert.equal(await p.evaluate(() => document.body.innerText.includes("FROM-A-FRAME")), false);
  // ... and said so, to the IDE's log and in the chat (a chat that hears nothing looks stuck).
  assert.match(JSON.stringify(await sent(p)), /panelError.*ignored a message from a window that isn't the IDE/);
  // The IDE's own way (Eclipse, NetBeans, Visual Studio): taken.
  await p.evaluate(() => window.postMessage({ kind: "state", state: "stopped", message: "FROM-THE-IDE" }, "*"));
  await p.waitForFunction(() => document.body.innerText.includes("FROM-THE-IDE"));
  await p.close();
});

test("VS Code: messages from the frames it wraps the page in, however deep, are taken", async () => {
  // VS Code puts the webview's page in frames of its own; the one that posts the
  // extension's messages isn't always the page's direct parent.
  const p = await browser.newPage();
  const chat = readFileSync(join(root, "dist", "chat.js"), "utf8");
  await p.setContent(`<!DOCTYPE html><html><body><iframe id="outer"></iframe></body></html>`);
  await p.evaluate((chat) => {
    const outer = (document.getElementById("outer") as HTMLIFrameElement).contentDocument!;
    outer.body.innerHTML = '<iframe id="inner"></iframe>';
    const inner = (outer.getElementById("inner") as HTMLIFrameElement).contentWindow!;
    const w = inner as unknown as Window & { sent: unknown[]; acquireVsCodeApi: () => unknown };
    w.sent = [];
    w.acquireVsCodeApi = () => ({ postMessage: (m: unknown) => w.sent.push(m) });
    inner.document.body.innerHTML = '<div id="app"></div>';
    const script = inner.document.createElement("script");
    script.textContent = chat;
    inner.document.body.append(script);
    // The top window (two frames up) posts, as VS Code's frames do.
    window.postMessage; // (the page itself)
    (window as unknown as { inner: Window }).inner = inner;
  }, chat);
  await p.evaluate(() => {
    const inner = (window as unknown as { inner: Window }).inner;
    inner.postMessage({ kind: "state", state: "exited", message: "FROM-VS-CODE" }, "*");
  });
  await p.waitForFunction(() => {
    const inner = (window as unknown as { inner: Window & { sent: { kind: string }[] } }).inner;
    return inner.document.body.innerText.includes("FROM-VS-CODE") && inner.sent.some((m) => m.kind === "panelState");
  });
  await p.close();
});

test("VS Code: a message from another frame of the webview (same origin, beside the page) is taken; another origin's is not", async () => {
  // In VS Code the extension's messages come from a frame of the webview that is
  // neither the page nor around it, at the webview's own address.
  const p = await browser.newPage();
  const errors: string[] = [];
  p.on("pageerror", (e) => errors.push(e.message));
  await p.route("https://webview.test/**", (route) =>
    route.fulfill({ contentType: "text/html", body: '<!DOCTYPE html><html><body><iframe id="host"></iframe><iframe id="chat"></iframe></body></html>' }),
  );
  await p.route("https://elsewhere.test/**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<!DOCTYPE html><html><body></body></html>" }),
  );
  await p.goto("https://webview.test/");
  const chat = readFileSync(join(root, "dist", "chat.js"), "utf8");
  await p.evaluate((chat) => {
    const inner = (document.getElementById("chat") as HTMLIFrameElement).contentWindow!;
    const w = inner as unknown as Window & { sent: unknown[]; acquireVsCodeApi: () => unknown };
    w.sent = [];
    w.acquireVsCodeApi = () => ({ postMessage: (m: unknown) => w.sent.push(m) });
    inner.document.body.innerHTML = '<div id="app"></div>';
    const script = inner.document.createElement("script");
    script.textContent = chat;
    inner.document.body.append(script);
  }, chat);
  // The other frame of the webview (same origin, beside the chat) posts: taken.
  await p.evaluate(() => {
    const host = (document.getElementById("host") as HTMLIFrameElement).contentWindow as Window & { eval(code: string): unknown };
    host.eval(`parent.frames[1].postMessage({ kind: "state", state: "exited", message: "FROM-VS-CODE" }, "*")`);
  });
  const chatText = () => (document.getElementById("chat") as HTMLIFrameElement).contentDocument!.body.innerText;
  await p.waitForFunction(`(${chatText})().includes("FROM-VS-CODE")`);
  // A frame of another origin posts: ignored (and reported once).
  await p.evaluate(() => {
    const f = document.createElement("iframe");
    f.src = "https://elsewhere.test/";
    f.onload = () => f.contentWindow!.postMessage("", "*"); // (just to have it loaded)
    document.body.append(f);
  });
  const other = await p.waitForEvent("framenavigated", (f) => f.url().startsWith("https://elsewhere.test"));
  await other.evaluate(() => parent.frames[1].postMessage({ kind: "state", state: "exited", message: "FROM-ELSEWHERE" }, "*"));
  await p.waitForFunction(`(${chatText})().includes("ignored a message from a window that isn't the IDE")`);
  assert.equal(await p.evaluate(`(${chatText})().includes("FROM-ELSEWHERE")`), false);
  assert.deepEqual(errors, []);
  await p.close();
});
