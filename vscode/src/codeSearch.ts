// Code search (Phase 5) in VS Code: the status bar item, its menu, and the
// setup flow. Every step is done by cmcoder over the protocol (rag_candidates,
// rag_setup, index), so the CLI and VS Code write the same settings and use
// the same index.

import * as vscode from "vscode";
import { productName } from "./brand";
import type { AgentEvent, ClientMessage, IndexStatus, RagCandidatesList, RagSetupResult } from "./protocol";

type Send = (message: ClientMessage) => boolean;

export class CodeSearch implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private status?: IndexStatus;
  private waiting = new Map<string, (event: AgentEvent) => void>();
  private progress?: { report: (text: string) => void };

  constructor(
    private readonly send: Send,
    onEvent: vscode.Event<AgentEvent>,
  ) {
    this.item = vscode.window.createStatusBarItem("cmcoder.codeSearch", vscode.StatusBarAlignment.Left, 50);
    this.item.name = `${productName()} code search`;
    this.item.command = "cmcoder.codeSearch";
    onEvent((event) => this.onEvent(event));
  }

  dispose(): void {
    this.item.dispose();
  }

  private onEvent(event: AgentEvent): void {
    switch (event.type) {
      case "system_init":
        this.status = undefined;
        this.send({ type: "index", action: "status" }); // for the status bar
        break;
      case "index_status":
        this.status = event;
        this.render();
        break;
      case "index_progress": {
        const text = `${event.done}/${event.total} files`;
        this.item.text = `$(sync~spin) Indexing ${text}`;
        this.item.show();
        this.progress?.report(`${text}, ${event.chunks} pieces`);
        break;
      }
    }
    const resolve = this.waiting.get(event.type);
    if (resolve) {
      this.waiting.delete(event.type);
      resolve(event);
    }
  }

  private render(): void {
    const s = this.status;
    if (!s) return this.item.hide();
    if (!s.set_up) {
      this.item.text = "$(search) Code search: off";
      this.item.tooltip = "Click to set up code search (an index of this project for the model).";
    } else if (s.error && !s.active) {
      this.item.text = "$(warning) Code search";
      this.item.tooltip = s.error;
    } else if (s.updating) {
      this.item.text = "$(sync~spin) Code search: updating";
      this.item.tooltip = s.lines.join("\n");
    } else if (!s.files && !s.chunks) {
      this.item.text = "$(search) Code search: not indexed";
      this.item.tooltip = "Click to index this project.";
    } else {
      this.item.text = `$(search) Code search: ${s.files.toLocaleString()} files`;
      this.item.tooltip = s.lines.join("\n") + (s.error ? `\nLast update failed: ${s.error}` : "");
    }
    this.item.show();
  }

  /** The next event of a type, after sending a message (or undefined on timeout / not running). */
  private request<T extends AgentEvent>(message: ClientMessage, type: T["type"], timeoutMs = 120_000): Promise<T | undefined> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.waiting.delete(type);
        resolve(undefined);
      }, timeoutMs);
      this.waiting.set(type, (event) => {
        clearTimeout(timer);
        resolve(event as T);
      });
      if (!this.send(message)) {
        clearTimeout(timer);
        this.waiting.delete(type);
        void vscode.window.showWarningMessage(`${productName()} isn't running: open its panel first.`);
        resolve(undefined);
      }
    });
  }

  /** The status bar item's menu. */
  async menu(): Promise<void> {
    if (!this.status?.set_up) return this.setup();
    const pick = await vscode.window.showQuickPick(
      [
        { label: "$(refresh) Update the index", description: "changed files only", action: "update" },
        { label: "$(info) Show the index", action: "status" },
        { label: "$(sync) Rebuild the index", description: "index everything again", action: "rebuild" },
        { label: "$(settings-gear) Set up code search again", action: "setup" },
        { label: "$(trash) Delete this project's index", action: "clear" },
      ],
      { placeHolder: "Code search" },
    );
    if (!pick) return;
    if (pick.action === "setup") return this.setup();
    if (pick.action === "status") {
      const lines = this.status?.lines ?? [];
      void vscode.window.showInformationMessage(lines.join(" · ") || "No index yet.", { modal: false });
      return;
    }
    await this.index(pick.action as "update" | "rebuild" | "clear");
  }

  async index(action: "update" | "rebuild" | "clear" = "update"): Promise<void> {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Code index", cancellable: false },
      async (p) => {
        this.progress = { report: (message) => p.report({ message }) };
        try {
          const status = await this.request<IndexStatus>({ type: "index", action }, "index_status", 30 * 60_000);
          if (status && !status.error) {
            p.report({ message: `${status.files} files, ${status.chunks} pieces` });
          }
        } finally {
          this.progress = undefined;
        }
      },
    );
  }

  /** Pick an embedding model, where the index lives, and whose settings: as `cmcoder rag setup`. */
  async setup(): Promise<void> {
    const candidates = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Asking the gateway for its models…" },
      () => this.request<RagCandidatesList>({ type: "rag_candidates" }, "rag_candidates", 60_000),
    );
    if (!candidates) return;
    for (const [provider, error] of Object.entries(candidates.errors)) {
      void vscode.window.showWarningMessage(`Couldn't list the models of ${provider}: ${String(error)}`);
    }
    const items: (vscode.QuickPickItem & { model?: string })[] = [
      ...candidates.likely.map((m) => ({ label: m, description: "embedding model", model: m })),
      ...(candidates.other.length ? [{ label: "Other models", kind: vscode.QuickPickItemKind.Separator }] : []),
      ...candidates.other.map((m) => ({ label: m, model: m })),
      { label: "$(edit) Type a model name…" },
    ];
    const picked = await vscode.window.showQuickPick(items, {
      title: "Code search (1/4): embedding model",
      placeHolder: "A model on your gateway that turns code into vectors",
    });
    if (!picked) return;
    const model =
      picked.model ??
      (await vscode.window.showInputBox({ title: "Embedding model", prompt: "provider:model, e.g. corp:bge-m3" }));
    if (!model) return;

    const store = await vscode.window.showQuickPick(
      [
        { label: "On this machine", description: "built in, nothing to install", value: "local" as const },
        { label: "Chroma on this machine", description: "needs cmcoder[chroma]", value: "chroma" as const },
        { label: "A Chroma server", description: "can be shared by a team", value: "chroma-server" as const },
      ],
      { title: "Code search (2/4): where the index lives" },
    );
    if (!store) return;
    let url: string | undefined;
    let apiKey: string | undefined;
    let readOnly = false;
    if (store.value === "chroma-server") {
      url = await vscode.window.showInputBox({
        title: "Chroma server",
        prompt: "Its address, e.g. https://chroma.example.com:8000",
        validateInput: (v) => (/^https?:\/\/\S+$/.test(v.trim()) ? undefined : "An http:// or https:// address"),
      });
      if (!url) return;
      apiKey = await vscode.window.showInputBox({
        title: "Chroma server API key",
        prompt: "Kept in your OS keychain, never in a file. Leave empty if it needs none.",
        password: true,
      });
      if (apiKey === undefined) return;
      const mode = await vscode.window.showQuickPick(
        [
          { label: "Search and update it", value: false },
          { label: "Only search it", description: "someone else (e.g. CI) keeps it up to date", value: true },
        ],
        { title: "Chroma server: updates" },
      );
      if (!mode) return;
      readOnly = mode.value;
    }
    const scope = await vscode.window.showQuickPick(
      [
        { label: "Just me", description: "~/.cmcoder/settings.json", value: "user" as const },
        { label: "This project", description: ".cmcoder/settings.json, shared through git", value: "project" as const },
      ],
      { title: "Code search (3/4): whose settings" },
    );
    if (!scope) return;
    const now =
      readOnly ||
      (await vscode.window.showQuickPick(
        [
          { label: "Index this project now", value: true },
          { label: "Later", value: false },
        ],
        { title: "Code search (4/4)" },
      ));
    if (now === undefined) return;
    const indexNow = typeof now === "boolean" ? false : now.value;

    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Setting up code search" },
      async (p) => {
        this.progress = { report: (message) => p.report({ message }) };
        try {
          return await this.request<RagSetupResult>(
            {
              type: "rag_setup",
              embedding_model: model,
              store: store.value,
              url: url ?? null,
              api_key: apiKey || null,
              scope: scope.value,
              read_only: readOnly,
              index_now: indexNow,
            },
            "rag_setup_result",
            30 * 60_000,
          );
        } finally {
          this.progress = undefined;
        }
      },
    );
    if (!result) return;
    if (result.ok) void vscode.window.showInformationMessage(result.message.split("\n").join(" "));
    else void vscode.window.showErrorMessage(`Code search: ${result.message}`);
  }
}
