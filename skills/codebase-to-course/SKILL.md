---
name: codebase-to-course
description: >-
  Investigate an indexed codebase through codemap's MCP server and explain it
  inside codemap's explore.html for an engineer who is new to the repo. Use
  when someone wants to "explain this codebase", "help me onboard to this
  repo", "teach this code", "make a walkthrough from this project", "explain
  the functions / the dependencies / the architecture", "simulate what happens
  when I run/click/call this", or asks to fill in the Learn, Packages,
  Simulate or Architecture tabs or the inspector explanations of the Codegraph
  Explorer. First settles what static analysis could not (calls matched by name
  only, web requests with no matching route, misplaced architecture parts,
  hidden databases and services, workflows nobody named), every answer pinned
  to file:line evidence and verified before it is written. Then writes
  .codemap/walkthrough.json (Learn), libraries.json (Packages),
  explanations.json (Graph inspector), scenarios.json (Simulate),
  glossary.json (tooltips), architecture.json (Architecture corrections) and
  findings.json (the facts it settled), all baked into the page by
  `codemap explore`.
---

# codebase-to-course

The flow is **index → investigate → write → bake**:

```
codemap scan  →  codemap MCP: overview / open_questions / read_source / calls …
              →  answer_question + submit (verified, written to .codemap/*.json)
              →  codemap explore --open
```

The page is built from what static analysis can prove **plus** what you
settle with evidence. You never edit code or the renderer, and you never write
a `.codemap/` file by hand while the MCP tools are available: `answer_question`
and `submit` verify each write (keys exist, `file:line` evidence exists and
still reads the same, the entry passes `codemap check`) and refuse anything
that doesn't hold up.

**Prerequisites.**
- The `codemap` CLI is on `PATH`. If `codemap --version` fails, tell the user to
  run `pipx install "codemap[mcp] @ git+https://github.com/devanr29/codebase-to-learn"`.
- The `codemap` MCP tools are in your tool list. The plugin starts the server.
  Without the plugin, run `claude mcp add codemap -- codemap mcp`.
- If neither is available, follow `references/file-mode.md` instead of the
  workflow below.

## Audience

The reader is **an engineer new to this repo**: someone who can program and
needs to be productive in *this* codebase quickly. That could be a new hire, a
reviewer, a contractor, or someone who generated the code with AI and now has
to own it. They want to know:
- what each part is responsible for;
- what goes in and what comes out;
- what it touches (database, network, files, global state);
- who calls it;
- what will surprise them.

Write precisely. Technical terms are fine; tie every claim to real code. See
`references/content-philosophy.md`.

## Workflow

1. **Orient.** If `overview` says there is no index, or the index is stale,
   ask the user to run `codemap scan`, then call `overview` again. Read the
   summary, layers, entry points and `open_questions` counts. Then read
   `references/content-philosophy.md` and `references/gotchas.md`.

2. **Investigate.** This is the step that makes the page correct.
   - **How to work.** Call `open_questions` one `kind` at a time, in this order.
     For each question, read the code first: `read_source` around the call
     site, `calls` / `impact` / `explain_symbol` on the symbols involved, and
     `search_code` for registrations, decorators and config.
   - **How to answer.** Call `answer_question` with the `answer_shape` the
     question gives and `evidence: [{file, line, note}]` pointing at the lines
     that prove it. When the code doesn't settle a question, answer
     `{"unknown": true}` with a note saying what you checked. **Never guess.**
     A wrong "fact" here is drawn on the page as reviewed.
   - **Rejections.** A rejected answer says why (a bad line, a key that isn't a
     candidate). Fix it or move on; nothing was written.

   Work through the kinds in this order:

   | kind | what you settle | becomes |
   |---|---|---|
   | `ambiguous_call` | which same-named function a call really reaches: one, several when it is polymorphic (a base class and its subclasses, a real and a null object), or none (a stdlib/library method) | the edge is confirmed; the wrong guesses disappear |
   | `unmatched_request` | which handler serves a web request, or which outside service receives it | a route link / service on the Architecture tab |
   | `wrong_way`, `weak_placement` | the real layer of a part, or why an upward import is a deliberate shortcut | `architecture.json` corrections |
   | `hidden_dependency` | the database, queue or service a setting points at | a store/service on the Architecture tab |
   | `orphan` | who calls a function the graph shows nobody calling (framework hooks, registries, CLI tables), or that it's unused | reviewed edges / a note |
   | `unnamed_flow` | the workflow an entry point starts | a `scenarios.json` entry |
   | `unexplained_package` | what a dependency is and its job here | a `libraries.json` entry |
   | `unexplained_symbol` | what an important symbol does | an `explanations.json` entry |

   Cover every question of the first five kinds when there are fewer than about
   60. Past that, do the highest-ranked ones and tell the user how many are
   left. For `orphan` questions, settle the top 20 or so. Then answer every
   `unnamed_flow`, every `unexplained_package`, and `unexplained_symbol` until
   the hot spots and entry points are covered.

3. **Write the prose that isn't one question.** Use `submit`, following the
   schema files:
   - `walkthrough` (`references/walkthrough-schema.md`): what the project is,
     its sides and the seam between them, a repo-wide category map, and a page
     for each folder that earns one;
   - `library` entries for the repo's own top-level modules
     (`references/libraries-schema.md`);
   - `glossary` terms for this project's own vocabulary
     (`references/glossary-schema.md`);
   - an `architecture` `summary` and human `title`s for boxes still named after
     a bare folder (`references/architecture-schema.md`);
   - the 3–6 hero scenarios. Give each hand-narrated `steps` that follow
     `calls(<root>, "out", 4, false)`, and mark the single best one
     `"start": true` (`references/scenarios-schema.md`). A step `submit` warns
     is "not a call edge" is narration. Either confirm it as a call first
     (through an `orphan` or `ambiguous_call` answer) or say in its `code` line
     that the framework makes that hop.

4. **Verify claims.** Every "only X does Y" or "nothing else touches this" in
   your prose needs `impact` / `calls(..., "in")` plus `search_code` behind it.
   An exclusivity claim you haven't checked must not ship.

5. **Check.** Run `check` (or `codemap check --json`) until there are zero
   errors. Read each warning: a stale finding means the code moved, so re-answer
   it.

6. **Bake and review.** Tell the user to run `codemap explore --open`, or run
   it yourself. Then:
   - Spot-check that reviewed edges carry the dashed **reviewed** tag in the
     Graph inspector.
   - Check that the Architecture tab has no best-guess box you meant to settle.
   - Check that the Simulate rail reads in workflow order.
   - Report what you settled, what is still unknown, and how many questions remain.

## Grounding

See `references/grounding-tools.md` for the codemap tools, and for using a
`codebase-memory` MCP server as a second opinion when it is installed. Never
install it for the user.

## Non-negotiables

- Write only through `answer_question` / `submit` while the MCP tools are available.
  Never edit code, the renderer, `explore.css` / `explore.js`, or
  `.codemap/traces/`.
- Every answer that changes the graph carries evidence you actually read.
  When the code doesn't settle it, say `unknown`.
- Every symbol key you write is exact (`find_symbol` gives it). No HTML in any string.
- Two or three sentences per `what` / `general` / `here` / folder `purpose`,
  each naming real modules and functions.
- `check` reports zero errors before you say you're done.
