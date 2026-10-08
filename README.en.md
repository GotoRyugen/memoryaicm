# memoryaicm — local, auditable, provider-neutral memory for LLM agents

Site: https://gotoryugen.github.io/memoryaicm/ · Code: https://github.com/GotoRyugen/memoryaicm · Store: https://elevennightmare.itch.io/memoryaicm-pro

[Version française](README.md) · [Changelog](CHANGELOG.md) · [Licence: AGPL-3.0 or commercial](LICENSE-COMMERCIAL.md)

**memoryaicm** gives long-term memory to any assistant or agent — Claude Desktop, Claude Code, a local model
under Ollama or llama.cpp, the OpenAI API, Gemini, LangChain, or your own agent loop. Everything runs on your
machine, in pure Python, **with zero dependencies** and no vector database: one SQLite file is the source of
truth and everything else is derived from it.

What sets it apart from cloud memories:

- **Nothing leaves the machine** and no provider is imposed: the conversing model *proposes* facts; the
  memory *decides* (write policy, conflicts, sensitivity) and *journals*.
- **Auditable**: an append-only journal, SHA-256 hash-chained, timestamped, attributed. No row is ever
  modified or deleted — not even by the software itself. Forgetting means de-indexing; the journal keeps everything.
- **"Said, not inferred"**: a fact is kept only if it is anchored in what the user actually wrote
  (mechanically checked, ≥ 50 % of its words). A web page, a document or a tool output is *data*: any
  instruction hidden in it is quarantined.
- **An out-of-model validator** blocks secrets, canaries, stale values and followed external instructions.
  Instruction adherence never rests on the model alone.
- **Consolidation ("sleep")**: deduplication, abstraction, ACT-R pruning, compilation of a profile; the
  consolidated state is promoted only if the test suites pass, otherwise it is rolled back.
- **GDPR**: complete, re-verifiable export (right of access, portability), AES-256-GCM encrypted backups,
  erasure by key destruction (right to erasure). See [GDPR](#gdpr--the-persons-rights).
- **Measured**: on LoCoMo (1,536 questions) retrieval beats a BM25 baseline up to k = 20, in 97 ms per
  recall, without a GPU. See [Benchmark](#benchmark-locomo).

```
Log ⊇ Index ⊇ Adapter          truth → projection → compiled cache
forget = de-index ≠ destroy    append-only journal, SHA-256 chained, full text
∀ write ∈ Log                  dated · attributed · replayable
100 % adherence ∈ guards       never in the model alone
consolidated state promoted ⇔ tests green
```

## Installation

```bash
pip install memoryaicm                 # core, zero dependencies (Python ≥ 3.10)
pip install "memoryaicm[secure]"       # + encrypted backups (cryptography)
pip install "memoryaicm[claude]"       # + Anthropic SDK (optional: the direct HTTP API works without it)
```

From source: `pip install -e ".[dev]"` then `python -m pytest` (128 tests, Linux and Windows). On Windows the
scripts `setup.ps1`, `install-claude.ps1`, `chat.ps1` and `serve.ps1` do the same from the repository folder.
Without installing anything: `python scripts/build_neutral.py` produces `dist/memoryaicm_neutral.zip`, an
archive that runs as is (see `NEUTRAL.md`).

The `auto` extraction backend picks for itself: **Claude** if `ANTHROPIC_API_KEY` is set, else **Ollama** if
it answers on `localhost:11434`, else the **deterministic stub** (FR/EN rules, no inference) — everything works
offline. Settings live in `.env` (template: `.env.example`) or `MEMORYAICM_*` variables. Data lives in
`./data` or in `--home` / `MEMORYAICM_HOME`.

## Claude Desktop, Cowork and Claude Code add-on (MCP server)

```bash
python -m memoryaicm install --write --hook     # any OS; on Windows: .\install-claude.ps1
```

The command writes the `mcpServers.memoryaicm` entry into Claude Desktop's configuration (a `.bak-…` copy
next to it), registers the server in Claude Code if installed, and sets the `UserPromptSubmit` hook that
memorises every message and injects the relevant notes **without any tool call**. Restart Claude: the
`memory_*` tools appear. **Claude generates, the memory remembers.**

| Tool | Role |
|---|---|
| `memory_recall` | relevant labelled notes + preferences — call before answering about the user |
| `memory_remember` | the verbatim message + extracted facts; each fact must be **anchored** in the message or it is rejected |
| `memory_ingest_external` | page, document, tool output = data; a memory instruction ⇒ quarantine |
| `memory_forget` / `memory_reactivate` | de-index (journal intact) / reactivate |
| `memory_search` | full text over the whole journal, de-indexed content included |
| `memory_read` | reads ONE journal event in full, untruncated (by seq or id) |
| `memory_transcript` | archives an exchange verbatim into the chained journal, or re-reads the last turns |
| `memory_status` / `memory_sleep` / `memory_review` | state, immediate consolidation, review queue for sensitive facts |
| `memory_history` / `memory_timeline` | dated values of a subject / summaries of recent sessions |

MCP resources to attach with one click: `memory://profile`, `memory://index`, `memory://timeline`,
`memory://status`; prompt `memory_brief` (ready-to-paste context). The server has no dependency (JSON-RPC 2.0
over stdio); `python -m memoryaicm mcp --selftest` launches it as a subprocess and talks MCP to it.

## Any LLM

The thirteen `memory_*` tools have a single source (`memoryaicm/tools.py`) and render in every provider's format:

```bash
python -m memoryaicm tools --format openai|anthropic|gemini|mcp|ollama|markdown   # schemas
python -m memoryaicm protocol [--text]                                            # system instruction (+ text protocol)
python -m memoryaicm agent --provider openai --base-url http://localhost:11434 --model llama3.1   # Ollama, llama-server, LM Studio, vLLM, OpenAI…
python -m memoryaicm agent --provider anthropic                                   # Claude through the direct API (no SDK)
python -m memoryaicm --backend ollama agent --provider text                       # a model WITHOUT tool calling (```memory``` blocks)
```

- `memoryaicm.tools`: `TOOL_SPECS`, `as_openai() as_anthropic() as_gemini() as_mcp() as_markdown()`, `PROTOCOL`,
  `ToolRouter(agent).call(name, args)` — same text rendering whatever the model; text protocol for models
  without tool calling.
- `memoryaicm.toolloop`: `OpenAIToolLoop`, `AnthropicToolLoop`, `TextToolLoop` — complete loops, stdlib only.
- Extraction backends: Claude (SDK or direct HTTP), `openai`, `llamacpp`, `ollama`, `callable:module:fn`,
  `callable:https://…`, `stub`. `auto` chooses by itself, never errors.
- `adapters/`: OpenAI-compatible, direct Anthropic, native Ollama, Gemini, plain text, LangChain,
  `llama-cpp-python`, Python and JavaScript HTTP clients. Full protocol in `docs/PROTOCOL.md` (French).

**Local service** (`python -m memoryaicm serve`): JSON API bound to `127.0.0.1:8765` — `POST /turn` ·
`POST /ingest` · `POST /forget` · `POST /sleep` · `POST /review` · `GET /status` · `GET /index` · `GET /search` ·
`GET /health`. Serialised access (single SQLite writer), background sleep after inactivity.

**Standalone chat** (`python -m memoryaicm chat`): talk normally; `oublie X` (forget); `sommeil` (sleep);
`/externe file.txt`; `/index`; `/journal words`; `/revue`; `/status`; `/quit`.

## Plugging into an existing agent

`memoryaicm/integrations/aca.py` shows a complete integration with an existing local agent (ACA, *Architecture
Cognitive Adaptative*): a subclass of its long-term memory, every memory also journaled and indexed, queries
merging both sources; the agent's epistemic contract (observations, not truths) is preserved. The same journal
can serve the local agent and Claude: point both at the same folder — SQLite in WAL mode, one writer at a time
(`BEGIN IMMEDIATE`), each process resynchronises its index before reading or writing.

Rules inherited from safe local agents:

- **approved local model** — `python -m memoryaicm model approve path.gguf` records the SHA-256; `model check`
  refuses a model without a fingerprint or whose fingerprint changed; every check is journaled;
- **`llamacpp` backend** — any OpenAI-compatible server via `MEMORYAICM_OPENAI_BASE_URL`; with
  `MEMORYAICM_LLAMA_SERVER`, memoryaicm launches `llama-server` itself on a loopback port with an ephemeral key;
- **autonomy levels** (`MEMORYAICM_AUTONOMY`, default 3) — 0 observe (read-only, writes refused and journaled);
  1 propose (every fact waits for `review`); 2 execute with confirmation (only sensitive facts wait);
  3 routines (automatic sleep). Refusals surface in the chat, the API (`403`), MCP tools (`isError`) and the hook.

## Autonomy

The system maintains itself: at start-up the chain is verified and the index rebuilt if missing or ahead of the
journal; after 25 turns since the last sleep it sleeps; when a session opens, if it has not slept for 6 h and
there is something new, it sleeps first; in `serve` mode a background thread sleeps after 15 min of inactivity.
Every sleep compiles an adapter, runs the suites and **promotes or rolls back**. The only point that waits for a
human, by design: the review queue for sensitive facts. Settings: `auto_sleep_every_turns`, `auto_sleep_idle_s`,
`serve_idle_sleep_s`, `selfcheck_on_start` (`config.py`).

## One turn, end to end

```
user turn ──► Journal (append) ──► WRITE: gate · durability · conflict⇒merge(ver++) · salience · sensitive⇒review
                                      │
                                      ▼
                       Ctx = [ sys+profile · top-k labelled notes · history (redacted view) · external=DATA · prefs@end · user ]
                                      │
                                      ▼
                       CALIBRATE: question about self? → relevant note ⇒ level "note"
                                                        else n samples, agreement < τ ⇒ abstain
                                      │
                                      ▼
                       VALIDATOR (out of model): secrets · canaries · followed external instruction ⇒ block;
                                                 note fact without label ⇒ label added
                                      │
                                      ▼
                       Journal (answer, used facts ⇒ ACT-R activation) ──► Index.sync()
```

`forget x`: a `fact.forget` event + cascade over everything derived from x (lineage); the originating turns
**and the command itself** leave the context view; the adapter is recompiled without x. The journal keeps
everything; `search` still finds it; `recall` reactivates.

`sleep`: dedup (lineage kept) → one active fact per exclusive subject → abstraction of groups
(`tool:*` ⇒ `abs:tool`) → pruning `act_eff < θ` and TTL ⇒ `on=0` → compile adapter vN → tests (recall,
injection, lineage, journal) → **promote** or **rollback**, all journaled.

**Hybrid retrieval**: `rel = lexical if lexical > 0 else semantic`. Lexical (stems, FR/EN synonyms, idf weighting
over the active index) decides when it finds; semantic catches rephrasings and typos — stdlib n-gram hashing
vectors, or a real local embedding model if Ollama answers (`nomic-embed-text`). Vectors are stored with the
model name and regenerated by `rebuild`: the index stays fully derived from the journal. **Temporality**:
`memory_history(subject)` lists every dated value; the validator blocks an answer quoting a superseded value.
**Episodic**: sleep summarises each session into a dated `EPI` fact with TTL and lineage; `memory_timeline(days)`
answers "where were we?".

## Spec ↔ code

| Block | File | What is mechanical here |
|---|---|---|
| TYPES | `model.py` | `Fact{kind, src, t₀, t_upd, ver, act, imp, ttl, deps, sens, on}`, event vocabulary |
| Journal · cold | `log.py` | SQLite, triggers refusing `UPDATE`/`DELETE`, SHA-256 chain, FTS5, `verify()` |
| Index · active | `index.py` | replayable projection (`rebuild()`), ACT-R `ln Σ tᵢ^(−d)`, cascade, idf lexical |
| WRITE | `policy.py` | `actor == user` gate, quarantine, INFER/ephemeral ignored, merge `ver++`, repetition/surprise/utility, review |
| READ | `index.retrieve`, `embed.py` | `rel`, `score = w_r·rel + w_a·act + w_i·imp`, top-k, `rel ≥ min_rel` (never a dump), label |
| Ctx | `context.py` | prefs at the end, redacted view, compaction keeping facts produced by old turns, delimited external |
| FORGET | `agent.forget` | event, cascade, view redaction, `Adapter ← compile(Index)` |
| SLEEP | `sleep.py` | dedup · conflicts · abstraction · pruning · compile · tests · promote/rollback |
| Adapter | `adapter.py` | `profile.md` (system prefix), `train.jsonl` (input of an external LoRA), manifest, `CURRENT` |
| ADHERE | `guard.py` + `context.py` | out-of-model validator; prefs re-injected at the end; `PROC` compiled into the profile |
| CALIBRATE | `calibrate.py` | Index route before weights, agreement between samples, abstention |
| Promotion tests | `bench.py` | canonical recall, injection, lineage, chain |
| Backends | `llm/` | `stub`, `anthropic`, `ollama`, `openai`/`llamacpp`, `callable`; contract `extract / complete / abstract / sample` |
| Autonomy | `agent.selfcheck / maybe_sleep`, `serve.py` | self-check, sleep by turns / idleness, local service |
| Claude add-on | `mcp_server.py`, `cli.install` | stdio MCP server, `memory_*` tools, anchoring of client-supplied facts |
| Agent integration | `integrations/aca.py`, `models.py` | LongTermMemory port, SHA-256 approval, autonomy 0–3 |
| GDPR | `privacy.py` | re-verifiable JSONL export, AES-256-GCM backup/restore, erasure by key destruction |

## GDPR — the person's rights

One memory folder = one person. Since the journal never modifies or deletes a row, rights are exercised on the
whole folder:

```bash
python -m memoryaicm export --out export.jsonl     # right of access and portability (art. 15, 20): the whole journal,
                                                   #   one event per line, hashes included, re-verifiable without the tool
python -m memoryaicm backup --out mem.maicm        # AES-256-GCM encrypted backup (folder-specific key: data/vault.key,
                                                   #   never copied into the backup — keep it apart)
python -m memoryaicm --home new restore mem.maicm [--key HEX]   # restore into an empty folder, verify the chain, rebuild the index
python -m memoryaicm erase --yes                   # right to erasure (art. 17): the key is destroyed — every encrypted backup
                                                   #   becomes unreadable —, every file is overwritten then deleted;
                                                   #   only a dated tombstone remains, with no content
```

What this guarantees: erasure is total for the folder and for its encrypted backups (*crypto-shredding*),
without ever editing a journal row. What it does not cover: plaintext copies made outside the tool, and physical
overwriting on SSDs (the key destruction is the guarantee, not the overwrite). Encryption: the `[secure]` extra
(`cryptography`); `export` and `erase` work without it.

## Benchmark (LoCoMo)

A measure of the **memory alone**, with no LLM judge: every conversation of the public LoCoMo set
(10 conversations, 5,882 turns, 1,536 questions, real session dates) is replayed into a fresh store, then we
check whether the expected evidence turn is in the top-k. Baseline: BM25 over the same turns.

| k | memoryaicm | BM25 |
|---:|---:|---:|
| 1 | **0.340** | 0.299 |
| 5 | **0.528** | 0.517 |
| 10 | **0.607** | 0.602 |
| 20 | **0.674** | 0.674 |
| 30 | 0.708 | 0.720 |

Median recall 97 ms over 500–690 facts, pure Python, no GPU, no network. Scores published by other systems
(≈ 90 % on LoCoMo) are *GPT-4o-judged* scores: a different quantity, not comparable with this hit@k. Measured
weak spot: multi-hop questions (0.447 at k = 10). Details, raw results and reproduction:
`bench/LISEZMOI-BANC-MEMOIRE.md` (French), `bench/locomo/`.

## Data

```
data/
  journal.sqlite      source of truth — never rewritten
  index.sqlite        projection — disposable, `rebuild` recreates it from the journal
  adapter/
    v1/ v2/ …         profile.md · train.jsonl · manifest.json (index_hash, tests, promoted/rejected)
    CURRENT           promoted version
  models.json         SHA-256 fingerprints of approved local models
  vault.key           backup encryption key (created on first `backup`)
```

## Commands

`init` · `chat [--session S]` · `say "text"` · `ingest FILE --source S` · `forget "x"` · `recall FACT_ID` ·
`sleep` · `review [FACT_ID --approve|--reject]` · `show index|log|adapter|status [--all]` · `search "words"` ·
`rebuild` · `verify` · `bench` · `serve [--port N]` · `mcp [--selftest]` · `hook prompt` ·
`model approve|check|list|revoke [PATH]` · `install [--write] [--hook]` · `tools [--format F]` · `protocol [--text]` ·
`agent [--provider openai|anthropic|text] [--base-url U] [--model M] [--say "…"]` ·
`export [--out F]` · `backup --out F` · `restore F [--key HEX]` · `erase --yes`.
Global options: `--home`, `--backend` (`auto|stub|anthropic|openai|llamacpp|ollama|callable:module:fn|callable:http://…`).

Interface strings, prompts and documentation are in French; the code, tool names and schemas are language-neutral.

## What is guaranteed, what is not

Mechanically guaranteed (tests): no journal row is ever modified or deleted; the chain detects any external
alteration; the index rebuilds identically; no non-user content produces a fact; a forgotten fact survives
neither in the index, nor in the view, nor in the promoted adapter; a consolidated state is promoted only if the
suites pass; an altered or keyless encrypted backup is refused.

Backend-dependent: extraction quality (the stub only knows simple phrasings), abstraction, answer fidelity.
The validator does not judge meaning: it applies hard rules.

Out of scope, by design: actual adapter training (`train.jsonl` is its input); unlearning in the base model.
Semantic retrieval without an embedding model remains n-gram hashing: it catches typos and inflections, not
distant paraphrases — `ollama pull nomic-embed-text` is enough, nothing else to change.

## Licence

Dual licence: **AGPL-3.0-or-later** (`LICENSE`) for any use that honours the network copyleft, or a
**commercial licence** to embed memoryaicm in a product or service without publishing your code, with support.
Details and contact: `LICENSE-COMMERCIAL.md`.

Copyright © 2026 Eugène Baumela.
