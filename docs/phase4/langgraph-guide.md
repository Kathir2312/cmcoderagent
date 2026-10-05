# cmcoder Phase 4, through LangChain / LangGraph eyes

Phase 4 is about running an agent safely for other people: another gateway,
a sandbox for its shell, usage metrics, and a build anyone can install. Here
is how you'd do each with LangChain / LangGraph, and what cmcoder does.

## The mapping

| Phase 4 item | LangChain / LangGraph | cmcoder |
|---|---|---|
| A second gateway (Open WebUI) | `ChatOpenAI(base_url=".../api", api_key=...)`, or `ChatOllama(model=..., num_ctx=...)` | `OpenWebUIProvider(OpenAICompatProvider)` (`providers/openwebui.py`) |
| Shell commands that can't do harm | Nothing built in: `ShellTool` runs on your machine; you add a container or a remote sandbox yourself | `sandbox/`: bubblewrap (Linux, WSL2), `sandbox-exec` (macOS), a filtering proxy for the network |
| Fewer approval prompts | `interrupt()` before a tool node, on every call or none | Sandboxed commands auto-allowed; leaving the sandbox asks every time |
| Usage metrics | LangSmith tracing (whole runs: prompts, outputs) or OpenTelemetry instrumentation | `telemetry.py`: counts and timings only, OTLP to the company's collector, off by default |
| Shipping it | A Python package or a Docker image; LangGraph Platform for servers | A PyInstaller program folder, and a `.vsix` per platform with it inside |
| The company's name and icon | n/a | `branding/`, checked at build time |

## 1. Open WebUI

### The LangChain way

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(base_url="https://webui.example/api", api_key=key, model="qwen3:32b")
```

That works for chat. Two things you'd hit later: an Ollama model behind Open
WebUI uses Ollama's default window (often 2–8k tokens) unless the request
sets `num_ctx`, and silently drops the start of a longer prompt; and its tool
calls arrive numbered 0, which a parser that merges by index will glue
together.

### The cmcoder way

The same OpenAI-compatible client, subclassed: it sends `num_ctx` (cmcoder's
window, capped at the model's trained length), starts a new tool call on a new
id, and explains Open WebUI's errors. `cmcoder doctor` says which backend a
model has and whether tool calls get through.

### Why they differ

A library gives you the client and leaves the server's quirks to you; an
agent that teams install needs to handle them once, for everybody.

## 2–3. Shell commands and approvals

### The LangGraph way

```python
from langgraph.types import interrupt

def run_shell(state):
    ok = interrupt({"command": state["command"]})   # a person approves
    if ok:
        return {"output": subprocess.run(...).stdout}
```

Either every command waits for a person (people stop reading), or none does
(the agent can delete your home folder). Isolation is yours to add: a Docker
container, a remote sandbox service.

### The cmcoder way

The agent's persistent shell runs *inside* an operating-system sandbox: the
project writable, everything else read-only, credentials hidden, `.git` and
cmcoder's own settings read-only, no network except allowlisted hosts through
cmcoder's proxy. Inside it, commands don't need approval. When something truly
needs the outside (a `git commit`, a blocked host), the model asks with
`dangerously_disable_sandbox` and the user approves that one command.

### Why they differ

A container per agent is the server answer; on a developer's laptop, a
sandbox around the shell keeps the agent in the developer's real project
without copying it anywhere.

## 4. Usage metrics

### The LangChain way

LangSmith traces each run: inputs, outputs, tool calls, latency. Excellent for
debugging an application you own; it records prompts and code, so for a
company-wide coding agent it would collect everyone's work.

### The cmcoder way

Eight counters with a few labels (model, tool, result), exported as OTLP JSON
to the company's own collector, and only if the company turns it on. Never a
prompt, a reply, a path or a command.

## 5–6. Shipping

A LangGraph app ships as a package or a container. cmcoder ships as a program
folder built with PyInstaller (no Python needed), and inside the VS Code
extension for each platform, with the company's name and icon from
`branding/`.

## 7. Security

The sandbox got its own escape review ([SECURITY-REVIEW.md](SECURITY-REVIEW.md)):
6 issues fixed (local services' sockets, `.git`, missing protected folders,
macOS local ports, the proxy's request text, the API key in the shell's
environment), each with a test.
