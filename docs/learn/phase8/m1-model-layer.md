# M1: the model layer on LangChain

The first step of the Phase 8 engine ([plan](../../project/phases/phase8/PLAN.md)):
cmcoder talks to its gateway through LangChain's chat model, extended where
LangChain gets cmcoder's gateways wrong. The code is in
`src/cmcoder/llm/`; the tests in `tests/test_llm_chat_openai.py` and
`tests/test_llm_extras.py`.

**You learn:** LangChain's messages and chunks; how a `ChatOpenAI` call runs
inside; where to extend a chat model by subclassing it; the Runnable methods
`bind_tools`, `with_fallbacks` and `with_structured_output`; embeddings.

**Lab:** [labs/m1_model.py](labs/m1_model.py) runs plain `ChatOpenAI` and
cmcoder's `CmcoderChatOpenAI` side by side against the mock gateway:

```
uv run python docs/learn/phase8/labs/m1_model.py
```

## 1. Messages

LangChain's conversation is a list of messages:

| Message | cmcoder's (classic) `Message` | Notes |
|---|---|---|
| `SystemMessage` | role `system` | the system prompt |
| `HumanMessage` | role `user` | text, or content blocks with images |
| `AIMessage` | role `assistant` | `content`, `tool_calls` (parsed), `invalid_tool_calls` (arguments that weren't JSON), `usage_metadata`, `additional_kwargs` (cmcoder keeps the reasoning there) |
| `ToolMessage` | role `tool` | a tool's result, linked by `tool_call_id` |

While streaming, the model yields `AIMessageChunk`s; adding chunks (`a + b`)
joins their text and their tool-call pieces into the final message.

## 2. Inside a call

```
model.invoke(messages)                      Runnable entry point (also ainvoke, stream, astream)
  └ generate → _generate_with_cache         every invoke passes here       ← cmcoder: _finish (whole reply)
      └ _stream (streaming=True)
          ├ _get_request_payload            builds the HTTP body           ← cmcoder: the history, the options
          ├ POST {base_url}/chat/completions, read the SSE lines
          ├ _convert_chunk_to_generation_chunk   one per "data:" line      ← cmcoder: the reasoning field
          └ run_manager.on_llm_new_token    callbacks: what streams to the user  ← cmcoder: after splitting <think>
```

Callbacks are LangChain's way to watch a run: tracing (LangSmith,
Langfuse) and streaming to a UI are both callbacks. That is also why
cmcoder calls `on_llm_new_token` itself: the user must see the chunks
*after* the `<think>` text was taken out.

## 3. What cmcoder changes, and where

The Phase 8 spike ([SPIKE.md](../../project/phases/phase8/SPIKE.md), E1)
ran plain `ChatOpenAI` against the mock gateway; the lab shows the same:

| On cmcoder's gateways, `ChatOpenAI`… | `CmcoderChatOpenAI` | Where |
|---|---|---|
| drops vLLM's `reasoning_content` / `reasoning` | keeps it in `additional_kwargs["reasoning_content"]` | `_convert_chunk_to_generation_chunk`, `_create_chat_result` |
| shows `<think>…</think>` as the answer (no reasoning parser on the server) | splits it as it streams, also when a tag is cut across chunks; learns a template that opens `<think>` itself | `_stream`/`_astream` with cmcoder's `ThinkSplitter`; `_finish` |
| resends a broken tool call as written: vLLM answers 400 to every later message | sends past arguments as strict JSON (`wire_arguments`) | `_get_request_payload` → `wire_history` |
| keeps empty replies, which the model then copies | leaves them out, and never sends reasoning back | `wire_history` |
| knows nothing of Qwen's thinking switch, Ollama's options or `max_tokens` naming | `invoke(..., thinking=False)`; options from cmcoder's model profile | `_get_request_payload` |
| doesn't parse Qwen's `<tool_call>` text | turns it into tool calls; with `toolCalling: "prompted"`, describes the tools in the prompt instead of sending `tools` | `_finish` → `text_tool_calls`; `prompted_history` |

## 4. Building it: `llm/factory.py`

`chat_model_for(settings, provider, model, profile)` builds the model as
cmcoder built its own provider:

- **TLS:** cmcoder's `httpx` clients (the OS certificate store or
  `caCertPath`; never unverified), passed as `http_client` and
  `http_async_client`, with `http_socket_options=()`: otherwise
  `langchain-openai` puts in its own transport, which turns off `HTTPS_PROXY`.
- **The key:** from `CMCODER_API_KEY` or the keychain, as a `SecretStr`
  (never in `repr`, logs or `to_json()`).
- **The profile:** LangChain's `profile` gets the context window
  (`max_input_tokens`): its summarising middleware needs it, and it doesn't
  know Qwen (the spike's E4).

## 5. Runnables

Every LangChain model is a *Runnable*: something with `invoke`, `stream`,
`batch` and their async forms, that you can wrap:

| Method | What it gives | In cmcoder |
|---|---|---|
| `model.bind_tools([...])` | the same model, always sent these tools | the engine's tools (M2, M4) |
| `fast.with_fallbacks([main])` | tries `fast`, then `main` if it raises | `with_fallback`: side jobs on the small model |
| `model.with_structured_output(Schema, method="function_calling")` | a reply parsed into a pydantic object, through a tool call | `structured`: titles, the critic's verdict, team plans (M6) |

## 6. Embeddings

`embeddings_for(settings, provider, model)` gives LangChain's
`OpenAIEmbeddings` on the same clients, with
`check_embedding_ctx_length=False`: otherwise it counts tokens with
tiktoken, which knows only OpenAI's models and downloads its tables from the
internet on first use. Code search moves onto it in M7.

## 7. The tracing guard

`src/cmcoder/tracing_guard.py`: with `LANGSMITH_TRACING` set in a
developer's shell, LangChain sends every run, with the code it read, to
LangSmith's cloud by itself, and it reads the variable only once (the spike's
E3). So cmcoder moves the `LANGSMITH_`, `LANGCHAIN_` and `LANGFUSE_`
variables aside first thing; the commands it runs for the developer get them
back. Tracing that settings allow comes in M8.

## Exercises

1. In `labs/m1_model.py`, add `reasoning_field="reasoning"` to the first
   `MockState` (newer vLLM's name): what changes for each model?
2. Put a `print(payload)` at the end of
   `CmcoderChatOpenAI._get_request_payload` and run the lab: find the strict
   JSON that replaced the broken call.
3. Call `chat_model(...).bind_tools([...])` with a profile whose
   `tool_calling` is `"prompted"`, against a mock reply that writes a
   `<tool_call>` block. Where does the tool description go in the request?
4. Wrap a `RunnableLambda(lambda x: 1 / 0)` with `.with_fallbacks([...])`:
   which exceptions does a fallback catch?
