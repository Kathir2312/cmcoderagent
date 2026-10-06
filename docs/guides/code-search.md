# Code search (optional)

For big projects, cmcoder can find code by meaning ("where do we retry
payments?"), not only by name. It keeps an index of the project, made with an
embedding model on your AI gateway. Small projects don't need it.

## Set it up

| Where | How |
|---|---|
| Terminal | `cmcoder rag setup` in the project folder |
| VS Code | **cmcoder: Set Up Code Search** (or the "Code search" status bar item) |
| Eclipse | the **Code search** item in the chat view's toolbar → set up |

It asks four things:

1. **Embedding model:** picked from your gateway (your AI team names it).
2. **Where the index lives:**
   - **On this machine (built in):** nothing to install. The right choice for
     most people.
   - **Chroma, by its URL:** a Chroma database, either running on this PC
     (`http://localhost:8000`) or a server your team shares
     (e.g. `https://chroma.example.com:8000`). Then its API key (if it has one;
     kept in your OS keychain), and whether you only search it (when CI keeps
     a shared index up to date).
3. **Whose settings:** yours, or the project's (shared through git).
4. **Index now?** The first time takes a few minutes for a big project; after
   that only changed files are updated.

Chroma is always reached through its URL. (Chroma built into cmcoder itself
isn't offered for now.)

## Chroma on this PC, without Python

Run Chroma's official container (Docker Desktop or Podman):

```
docker run -d --name chroma --restart unless-stopped -p 127.0.0.1:8000:8000 -v chroma-data:/data chromadb/chroma
```

then choose **Chroma, by its URL** and enter `http://localhost:8000`.
`127.0.0.1:` keeps it reachable from this PC only; don't open it to the
network without your IT team (see the security notes in
[../phase5/code-search.md](../phase5/code-search.md)).

If Chroma isn't running, cmcoder says "Can't reach the Chroma server
http://localhost:8000 … Is Chroma running on this PC?": start the container
(`docker start chroma`).

## Use it

Nothing to do: cmcoder searches the index when it helps, and keeps it current.
`/index` (terminal) or **Update Code Index** updates it now; `/index status`
or the status item shows its state. Secret files (`.env`, keys) are never
indexed.

Settings by hand (`~/.cmcoder/settings.json`):

```json
{
  "rag": {
    "embeddingModel": "corp:bge-m3",
    "store": { "type": "chroma", "url": "http://localhost:8000" }
  }
}
```

or `"store": { "type": "local" }` for the built-in index.
