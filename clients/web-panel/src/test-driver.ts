// For tests only (never shipped): lets an IDE's tests read and operate the panel
// through the embedded browser's "run this JavaScript" call (JCEF
// executeJavaScript, SWT Browser.evaluate, WebView2 ExecuteScriptAsync). Every
// function returns JSON text, which all three can hand back.
//
//   __cmcoderTest.run("text", ".msg.assistant")      -> "\"Hi there.\""
//   __cmcoderTest.run("click", ".permission .allow") -> "true"

type Fn = (selector: string, value?: string) => unknown;

const first = (selector: string): HTMLElement | null => document.querySelector<HTMLElement>(selector);

const visible = (e: HTMLElement | null): boolean =>
  !!e && !e.closest("[hidden]") && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";

const actions: Record<string, Fn> = {
  /** The text of the first match, or null. */
  text: (s) => first(s)?.textContent ?? null,
  /** The text of every match. */
  texts: (s) => Array.from(document.querySelectorAll<HTMLElement>(s)).map((e) => e.textContent ?? ""),
  count: (s) => document.querySelectorAll(s).length,
  visible: (s) => visible(first(s)),
  /** Clicks the first match if it's visible and enabled; true if it did. */
  click: (s) => {
    const e = first(s);
    if (!visible(e) || (e as HTMLButtonElement).disabled) return false;
    e!.click();
    return true;
  },
  /** Types into an input or text box (replacing its text). */
  fill: (s, value = "") => {
    const e = first(s) as HTMLInputElement | HTMLTextAreaElement | null;
    if (!e) return false;
    e.focus();
    e.value = value;
    e.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  },
  /** A key press (e.g. "Enter", "Escape") on the first match. */
  press: (s, key = "Enter") => {
    const e = first(s) ?? document.body;
    e.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    return true;
  },
  checked: (s) => (first(s) as HTMLInputElement | null)?.checked ?? null,
  /** A computed style property, e.g. for the theme checks. */
  style: (s, property = "color") => {
    const e = first(s);
    return e ? getComputedStyle(e).getPropertyValue(property) : null;
  },
};

(window as unknown as { __cmcoderTest: unknown }).__cmcoderTest = {
  run(action: string, selector: string, value?: string): string {
    const fn = actions[action];
    if (!fn) return JSON.stringify({ error: `unknown action ${action}` });
    try {
      return JSON.stringify(fn(selector, value));
    } catch (e) {
      return JSON.stringify({ error: String(e) });
    }
  },
};
