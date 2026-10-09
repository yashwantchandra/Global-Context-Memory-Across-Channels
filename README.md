# Yaad: one memory, every channel

IndiaMART Voice AI Hackathon 2.0 · PS02 *Global Context: Memory Across Channels*

**The problem.** IndiaMART's bots start every conversation knowing nothing. 80% of buyer enquiries in our data
never get a seller reply, and 42% of VANI bot calls end "Not Interested", partly because the bot opens with a pitch
instead of what the person actually needs.

**What Yaad does.** For every customer (keyed by GLID) Yaad keeps a short Markdown file, `buyer.md` or `seller.md`,
built from every channel: enquiries, buyer calls, buy-leads, WhatsApp, VANI calls, executive calls and our own
conversations. The file is organised around **unfinished work** ("open threads"): the buyer's requirement nobody
answered, the seller's unread enquiries, the callback someone promised. The bot reads it before it speaks, opens with
the next useful step, never re-asks what it already knows, and writes the conversation back so the next channel can
pick up where this one stopped.

## How it works

```
CSVs / APIs ─► event store (SQLite) ─► thread rules ─► buyer.md / seller.md ─► voice call · chat · exec card · due-today queue
                      ▲                                                                │
                      └──────────── every conversation is written back as an event ◄───┘
```

| Piece | What it does | LLM? |
|---|---|---|
| `yaad/ingest.py` | Turns each data source into events for both buyer and seller | No |
| `yaad/threads.py` | Rules that open / update / close threads and pick guardrails | No |
| `yaad/render.py`, `guard.py` | Fixed-schema file, size cap, privacy firewall, opening line | No |
| `yaad/extract.py` | Turns free text (call summaries, our transcripts) into small fields | Sarvam-105B, once per event |
| `yaad/channels/` | Voice call and chat on the hosted Sarvam agent; write-back on end | Sarvam agent |
| `yaad/web/` | Local demo app: memory viewer, chat, voice, exec card, queue, freshness | No |

**Design rules:** facts come from rules, never from the LLM. A new event rebuilds only that GLID's file, inline,
in a few milliseconds. Every file has the same six sections, so other tools read it by heading without an LLM.

## The file (both roles)

Front-matter (`glid, role, generated_at, last_event_at, freshness_ms, language, cold_start, synthetic_sources`), then:
`Who` · `Known – don't ask` · `Open threads` (max 3) · `Recent timeline` (max 5) · `Guardrails` · `Suggested opening`.
Budget: **≤ 450 tokens buyer, ≤ 500 seller**, enforced in code (lowest-value lines are trimmed first). That keeps the
whole memory under 2 KB, small enough to send as one agent variable on every call with no retrieval step.

## Lookback windows

| Source | Window | Why |
|---|---|---|
| VANI bot calls | 180 days | objections and dispositions stay relevant for months |
| Enquiries, buyer calls (PNS), call notes | 90 days (detail: last 30) | a requirement older than a quarter is usually closed |
| Buy-leads | 45 days | the warehouse only keeps ~45 days |
| WhatsApp chatbot | 30 days | intents such as photo upload or callback go stale fast |
| Executive calls | 60 days | a recent human touch changes what the bot should say |
| Our own conversations | no limit | the freshest truth; the latest one decides the open promise |

## Privacy

- A seller's file only has **aggregates** about buyers (city, product, counts), never a buyer's GLID, name, company,
  phone or message. `guard.py` checks every render and redacts phone numbers, emails and any counterparty id.
- The bot prompt forbids revealing one party's details to the other (tested: it refuses "buyer ka number do").
- Real customer data stays on this laptop and goes only to Sarvam. Synthetic rows are labelled
  (`synthetic_sources` in every file; personas `SYN-*`).

## Run it

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python fastapi "uvicorn[standard]" httpx python-dotenv sarvamai pytest "sarvam-conv-ai-sdk[all]"
cp .env.example .env            # add SARVAM_API_KEY (+ org/workspace ids for the hosted agent)
.venv/bin/python -m yaad ingest # load the organiser CSVs (~2 s)
.venv/bin/python -m yaad synth  # labelled synthetic personas + cold-start GLID
.venv/bin/python -m yaad pick   # suggests demo GLIDs (ids only)
.venv/bin/python -m yaad serve  # open http://127.0.0.1:8000
.venv/bin/python -m pytest -q tests
```

Sarvam agent: **Yaad IndiaMART Memory Assistant** (`Yaad-IndiaM-61a96599-5c50`) on indus.sarvam.ai.
