# `codemap mcp`: let an AI investigate the repo, with evidence

`explore.html` is built from what static analysis can prove. Static analysis
has gaps, and codemap is honest about them:
- a call matched by name alone (`AMBIGUOUS`);
- a web request no route in the repo serves;
- a function only a framework or a registry calls;
- a folder whose name doesn't say which layer it is;
- a database reached through a URL in an environment variable.

`codemap mcp` serves the index to an AI assistant over the
[Model Context Protocol](https://modelcontextprotocol.io). The assistant reads
the code, settles those gaps one question at a time, and writes its answers
back. Every answer must cite `file:line` evidence, and each one is verified
before anything is written. `codemap explore` then draws the settled facts,
marked as **reviewed** so readers can tell them from what codemap proved on its
own.

```
codemap scan  →  codemap mcp  ⇄  AI assistant (open_questions → read_source / calls → answer_question)
                     │
                     ▼  verified writes only
           .codemap/findings.json + walkthrough / libraries / explanations / scenarios / glossary / architecture.json
                     │
                     ▼
           codemap explore  →  explore.html
```

The server never scans, never edits code and never calls an LLM itself. It only
writes under `.codemap/`.

## Setup

The server ships with codemap: it speaks MCP's JSON-RPC 2.0 over stdio itself
(`codemap/mcp_server.py`), so there is no SDK to install.

```
pipx install git+https://github.com/devanr29/codebase-to-learn
```

**Claude Code, with the plugin.** `/plugin install codemap@codemap` starts the
server for you. Then run `/codemap:course`, or ask Claude to explain the repo.

**Claude Code, without the plugin:**

```
claude mcp add codemap -- codemap mcp
```

**Cursor, Claude Desktop and other clients:** add a stdio server that runs
`codemap mcp`. Pass `--path <repo>` if the client doesn't start it inside the
repo:

```json
{ "mcpServers": { "codemap": { "command": "codemap", "args": ["mcp", "--path", "/path/to/repo"] } } }
```

Run `codemap scan` first. A tool call on an index that's behind the code says
so (`"stale": true`) and the assistant should ask you to rescan.

## Tools

| Tool | What it returns |
|---|---|
| `overview` | summary, stack, architecture layers and their parts, entry points, folders, which authored files exist, open-question counts, freshness |
| `find_symbol(query)` | symbol keys (`path::Name`) by key, qualified name, path suffix, bare name or substring |
| `explain_symbol(key)` | signature, docstring, entry-point role, explanation, direct callers/callees with confidence, source |
| `calls(key, direction, depth, include_guesses)` | the call tree, each edge with `EXTRACTED` / `INFERRED` / `AMBIGUOUS` and its call line |
| `impact(key, depth)` | callers over confident edges, the tests among them, whether an entry point reaches it |
| `read_source(path, start, end)` | numbered lines, exactly as indexed (these are the line numbers evidence cites) |
| `search_code(pattern, path_prefix)` | regex hits over indexed files |
| `get_authored(kind, id)` | the current content of an authored file or one entry |
| `open_questions(kind, limit)` | ranked questions the graph can't settle, each with context and the answer shape it expects |
| `answer_question(question_id, answer, evidence, note)` | settles one question; verified, then written |
| `submit(kind, item, evidence)` | adds or replaces one authored entry; checked like `codemap check`, then written |
| `check` | `codemap check` over every authored file and finding |

### Open questions

| kind | the question | an answer becomes |
|---|---|---|
| `ambiguous_call` | `f` calls `helper()`, which matches N symbols by name. Which one runs (several, for polymorphism), if any? | the chosen edges are confirmed (`INFERRED`, tagged reviewed); the others are removed |
| `unmatched_request` | the code sends `GET /api/x` but no route matched. Which handler serves it, or is it an outside service? | a route link (Simulate, Architecture) |
| `orphan` | nothing calls `f`. Is it called dynamically, or is it unused? | reviewed call edges, or a note |
| `weak_placement` | this Architecture box was placed by best guess. Which layer is it? | an `architecture.json` correction |
| `wrong_way` | a lower layer imports a higher one. Is a part misplaced, or is this a real shortcut? | a correction, or a note |
| `hidden_dependency` | the code reads `DATABASE_URL`. Is that a store or service? | a store/service on the Architecture tab |
| `unnamed_flow` | this entry point has no scenario. What workflow does it start? | a `scenarios.json` entry |
| `unexplained_symbol` | what does this hot spot or entry point do? | an `explanations.json` entry |
| `unexplained_package` | what is this dependency, and what is it for here? | a `libraries.json` entry |

`{"unknown": true}` plus a note records that the code doesn't settle a question,
so it isn't asked again. Nothing in the graph changes.

## What "verified" means

A write is refused, and nothing is written, when:

- an evidence `file` isn't an indexed file, or a `line` is past its end;
- a symbol key doesn't resolve, or an `ambiguous_call` answer isn't one of the
  candidates;
- the entry would raise a new **error** in `codemap check` (a scenario whose
  steps don't resolve, an architecture path that matches no folder, and so on).

New warnings are returned with the successful write. A scenario step that
isn't a call edge is one of them: the step will play as narration.

## `.codemap/findings.json`

The settled facts that have no other home live here. Each one has an `id`, a
`kind` (`call`, `not_call`, `request`, `unknown`, `note`), a `note`, an
`evidence` list (`file`, `line`, and that line's `text` when it was written),
the index commit and a date. `model.build` applies them after codemap's own
analysis (name matching, then type resolution), so a reviewed answer has the last word
on a guess. It never removes a confident edge.

**Staleness.** When an evidence line no longer reads the same, or a symbol in
the finding is gone, the finding is **stale**:
- it is not applied, so the graph shows codemap's own guess again;
- `codemap check` warns and names it;
- the question reopens.

Like every authored file, `findings.json` is optional, git-ignorable and safe
to delete.

## On the page

- **Graph inspector.** A caller or callee settled by review carries a dashed
  **reviewed** tag. Hover it for the note and the evidence.
- **Architecture tab.** A request link settled by review is labelled
  "(confirmed by review)".
- **`codemap explore --json`.** The payload carries `edges[].review` and
  `routes[].review`, plus a top-level `review` summary
  (`findings`, `applied`, `stale`).
