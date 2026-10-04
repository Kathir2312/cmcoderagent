# cmcoder Phase 5, through LangChain / LangGraph eyes

Code search is retrieval-augmented generation (RAG) over the project's code.
LangChain has a building block for each step; this maps them to cmcoder's.

## The mapping

| RAG step | LangChain / LangGraph | cmcoder |
|---|---|---|
| Load documents | `DirectoryLoader`, `GenericLoader` | `rag/files.py: FileSelector` (gitignore, secrets refused) |
| Split | `RecursiveCharacterTextSplitter.from_language(Language.PYTHON, ...)` | `rag/chunker.py: chunk_file` (Python's `ast`; definitions; headings) |
| Embed | `OpenAIEmbeddings(base_url=..., model=...)`, `OllamaEmbeddings` | `rag/embed.py: Embedder` over `provider.embed` (LiteLLM and Open WebUI alike) |
| Store | `Chroma(...)`, `InMemoryVectorStore`, `FAISS` | `rag/stores.py`: `LocalStore`, `ChromaServerStore`, `ChromaLocalStore` |
| Keep current | `RecordManager` + `index(docs, record_manager, vectorstore, cleanup="incremental")` | the manifest in `rag/index.py` (time/size, then SHA-1; batches) |
| Retrieve | `vectorstore.as_retriever(search_kwargs={"k": 5})` | `CodeIndex.search(query, k, path)` |
| Retriever as a tool | `create_retriever_tool(retriever, "search_code", "...")` | `tools/code_search.py: CodeSearchTool` |
| "Stuff" context into the prompt | a chain: `{"context": retriever | format_docs, "question": ...} | prompt | llm` | automatic context: `Agent._auto_context` |

## The LangChain way

```python
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from langchain.indexes import SQLRecordManager, index
from langchain.tools.retriever import create_retriever_tool

emb = OpenAIEmbeddings(base_url="https://litellm.corp/v1", model="bge-m3")
store = Chroma(collection_name="pay-api", embedding_function=emb,
               client=chromadb.HttpClient(host="chroma.corp", port=8000))
splitter = RecursiveCharacterTextSplitter.from_language(Language.PYTHON, chunk_size=2000)
docs = splitter.split_documents(loader.load())
rm = SQLRecordManager("chroma/pay-api", db_url="sqlite:///records.sql"); rm.create_schema()
index(docs, rm, store, cleanup="incremental", source_id_key="source")

tool = create_retriever_tool(store.as_retriever(search_kwargs={"k": 6}), "search_code",
                             "Find code by what it does.")
agent = create_react_agent(model, [tool, *other_tools])
```

## The cmcoder way

```
cmcoder rag setup -m corp:bge-m3 --store chroma-server --url https://chroma.corp:8000 --index --yes
```

Then every session (CLI or VS Code) has the `CodeSearch` tool and automatic
context; the index is kept current by itself.

## Differences worth knowing

- **Splitting by structure, not characters.** LangChain's language splitter
  splits on separators (`\nclass `, `\ndef `) and then by size; cmcoder uses
  Python's parser for Python (exact function boundaries, decorators kept,
  big classes per method) and keeps each chunk's **names**, which go into the
  embedded text.
- **Two ways to use retrieval at once.** LangChain apps usually pick one: a
  retriever *tool* (the agent decides) or a *chain* that always stuffs context.
  cmcoder does both: the tool, and a small automatic context with a token
  budget, de-duplicated within the conversation.
- **Freshness is the agent's problem too.** A RecordManager handles
  re-indexing between runs; cmcoder also re-indexes the agent's own edits
  before the next search, and checks the files of each result.
- **No `chromadb` client for a server.** `ChromaServerStore` speaks Chroma's
  REST API with the same HTTP client as the gateway (company CA, proxy).
- **Secrets.** Loaders load what they're given; cmcoder's selector refuses
  secret files with the same rules as its Read tool.
