# Approach note: PS02 Global Context

## Problem
Every call or chat with Vani starts cold, so users repeat themselves. The data to fix this already exists across IndiaMART systems; what's missing is a compact, always-fresh view per user that any channel or team can load instantly.

## Workflow
```
sources ─► ingest (lookback per source, PII dropped) ─► SQLite event store keyed by GLID
                                                         │ new event → rebuild THAT GLID (fast pass ≈10 ms, LLM pass ≈0.5 s)
                                                         ▼ 5-min sweep = backstop
                    facts (plain code) + opening/summary (Sarvam-105B) → select (caps, eviction, budget) → render
                                                         ▼
                              data/profiles/{seller|buyer}/<glid>.md   (+ freshness_log.csv)
       ┌──────────────────────────┬──────────────────────────┬──────────────────────────┐
  Voice: hosted Sarvam agent   Chat: local web chat or     Call prep page (non-bot)
  file passed in as variables  the same agent over text    parses headings, no LLM
  transcript/outputs → events  turns → summary → event
```

## Data sources and lookback windows

| Source | Window | Why |
|---|---|---|
| Enquiries received / buyer activity: enquiries | 90 d / 30 d | Enquiries stay relevant for about a quarter; browse and search intent goes stale within a month |
| Enquiry reply threads | 30 d | Only recent unreplied threads are actionable |
| Buyer calls (PNS) | 90 d | Answer rate is a quarterly behaviour signal |
| Buy-leads bought | 45 d | All the warehouse keeps (16 Aug – 1 Oct) |
| WhatsApp chatbot and delivery | 30 d | High volume and fast-moving; older messages are noise |
| VANI bot calls | 90 d | Outcomes such as callback or not-interested matter for a quarter |
| Executive calls | 60 d | Data covers Aug–Sep only |
| PNS call extractions | 90 d | Products and prices discussed |
| Our own sessions (voice/chat) | 30 d, latest always pinned | This is what makes resume work |
| Profile / KYC snapshot | Latest wins | It's a state, not an event |

Buyer data comes from a log export of the internal `globalcontext/getContext` API. Its broken CSV quoting is repaired at parse time; 29 of 283 records can't be read and are skipped without crashing.

## Refresh mechanism (hybrid) and measured freshness
- **Event-driven.** Every new event (a new enquiry, the end of a call, the end of a chat) rebuilds only that GLID's file.
  - **Fast pass:** facts plus a cached or template opening. Measured median **13 ms**, max 15 ms.
  - **LLM pass:** runs only if the opening's inputs changed. Median **0.5 s**, max 0.7 s.
  - See `samples/freshness_log.csv` and `freshness_summary.json`.
- **Scheduled sweep** every 5 minutes, which rebuilds any file older than its newest event.
- **Freshness definition:** freshness = `generated_at − event ingested_at`. It's written into every file's front-matter and into the log.

## File design and size budget
- **One file per GLID per role**, never a shared file. The bot then only ever sees the person it's talking to.
- **Fixed sections.**
  - Seller (8): Snapshot, Leads & Enquiries, Responses & Calls, Past Conversations, Open Threads, Engagement Signals, Do-Not-Ask, Suggested Opening.
  - Buyer (10): adds Categories Searched, Sellers Contacted and KYC.
- **Row caps:**
  - Seller: 2 / 3 / 3 / 3 / 3 / 2 / 1 / 1 (18 rows).
  - Buyer: 17 rows.
  - At most 120 characters per row.
- **Budget:** at most 2,500 characters (about 700 tokens; typical files are 1,500–2,100).
  - It leaves ample room in the 32K-context conversational model.
  - It keeps time-to-first-word low on voice.
  - It focuses the model on what matters, which makes "don't re-ask" reliable.
  - An executive can read it in 30 seconds.
- **Eviction.** Rows are never edited in place; each rebuild re-selects from events:
  1. Expiry by lookback window.
  2. Dedupe and supersession.
  3. State-based closure of open threads: a later conversation closes a callback; a callback more than 48 h old becomes history; reading an enquiry closes it.
  4. Rank and cap per section. Our latest session is pinned, and Past Conversations take at most 2 rows per channel.
  5. A global trim in a fixed priority order. Snapshot, Open Threads, Do-Not-Ask and the Opening are never trimmed.

  The front-matter records `evicted:` counts.
- **Do-Not-Ask is derived only from facts that survived selection**, so the bot never claims something that isn't in the file.

## Identity, language and role
- **Identity line.** Every file carries the person's name (company name for sellers, first name for buyers) and their preferred language. The language is chosen in this order:
  1. What they spoke in our last conversation.
  2. The script and words of their own messages.
  3. The languages on their phone calls.
  4. Otherwise Hinglish, with the regional language of their state noted.
- **Opening line.** It's written in that language and uses their name. A GLID with no file gets a neutral greeting that doesn't assume whether they buy or sell.
- **Role is resolved at session start, never fixed per channel:**
  - Only one of `buyer/` and `seller/` exists: use it.
  - Both exist: use the one with the most recent activity.
  - Neither exists: create a cold-start file. The conversation summary then works out the role, and the file moves if needed.

## Requests
- **Detection.** At the end of every conversation (voice, phone or chat) the summary lists explicit requests:
  - Sellers: `catalogue_update` and `price_update`.
  - Buyers: `requirement` and `enquiry`.
  - Complaints are not requests, and request types are restricted by role.
- **Storage.** Each request is stored as an event and written to `requests/<folder>/<glid>.md`, newest first with a pending or done status. The pending ones also appear as Open Threads in the profile, so the next call follows up.

## LLM use
- `sarvam-105b` writes the Suggested Opening (JSON schema, Hinglish) and summarises our sessions. The summary extracts requirement, quantity, absolute callback time and open threads.
- `sarvam-105b-conversations` runs the chat channel.
- The hosted agent handles voice: Saaras for speech-to-text, Bulbul for text-to-speech.
- Facts never come from the LLM. If Sarvam fails, templates take over, so a rebuild never fails.

## Integration logic
Everything runs locally; there are no inbound hooks.
- **Voice:** our code starts each hosted-agent session with the SDK and passes `agent_variables={context, role, glid, opening}` plus `initial_bot_message`. Transcript turns stream back and are written as events.
- **Phone:** a test call or instant outbound call carries the same variables. A poller reads the analytics API and turns the agent's output variables into a session event.
- **Chat:** uses the same file and the same event store.
- **Other channels:** inbound phone, WhatsApp and the app would plug in through Sarvam's on-start hook calling a `GET /context?glid=` endpoint, plus an on-end hook posting to `/events`, in a production deployment inside IndiaMART's network.

## Privacy and safety
- Contact details are dropped at ingest. Free text is scrubbed of phone numbers, emails, links, PAN and Aadhaar-like IDs.
- A seller's file never names a buyer; it says "a buyer from <city>". A buyer's file lists seller company names only.
- The bot prompt, both the local chat prompt and the hosted agent's, refuses to share other parties' details. This was tested on both. Session summaries never record such requests.
- Cold start gets a generic greeting with no history claimed. This was tested on the hosted agent with empty context.
- Real data stays in gitignored `data/`. Conversations with real GLIDs are teammates role-playing and are labelled `synthetic: true`. `samples/` is redacted.

## Production path
- Swap the CSV snapshots for streams from the source systems.
- Use a cache for the latest file per GLID.
- Use Sarvam on-start and on-end hooks to an internal context API, which removes polling.
- Run a worker pool with per-GLID debouncing.
- Regenerate the opening only when conversation facts change, to control LLM cost at millions of GLIDs.
