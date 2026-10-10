# skills.md: build journey and tools

## Tools used
| Tool | What for |
|---|---|
| **Claude Code** (Claude Opus) | Planning, reading the brief and Sarvam docs, writing and testing the code, configuring the agent |
| **Sarvam Voice Agents MCP** (`mcp.sarvam.ai/voice-agents`) | Configuring the hosted agent from the terminal (`configure_agent` with dry-run first), testing with `send_chat`, test calls, listing voices |
| **Sarvam-105B / 105B-Conversations** (`sarvamai` SDK) | Opening line (JSON-schema output), session summaries, chat channel |
| **Sarvam Conversational SDK** (`sarvam-conv-ai-sdk`) | Starting hosted-agent sessions with `agent_variables` and streaming transcripts back |
| Python, SQLite, FastAPI, pytest | Event store, local web app, tests |
| GitHub (private repo) | Code only; data stays local |

## Journey
**Day 0 (8 Oct): planning only, no solution code.**
- Read the deck, the PS02 brief and the Sarvam docs, and summarised them in `CLAUDE.md`.
- Chose an **all-local** design: our API isn't exposed and we only make outbound calls to Sarvam. We send context in when a session starts and pull results back afterwards.

**Day 1 (9 Oct).**
1. **Data.**
   - The buyer CSV turned out to be a log of the internal `getContext` API with broken quoting. We parse it by locating each `API_RESPONSE_JSON` value and decoding it as a JSON string.
   - The 11 seller CSVs load into one event table, about 330k events in 7 s.
   - We drop contact details at ingest.
2. **Design decisions, made with the team:**
   - One file per GLID per role.
   - Fixed sections with row caps.
   - A 2,500-character budget.
   - Eviction by re-selection, never by editing rows in place.
3. **Builder.**
   - Plain code for facts, with the LLM only for the opening and summaries.
   - First run: the LLM returned a bare string and spoke *as* the seller. Fixed with a JSON schema and a "speak TO the {role}" prompt.
4. **Freshness.** New activity reaches the file in about 10 ms (fast pass); the LLM opening follows about 0.5 s later. Both are logged.
5. **Cross-channel resume test.**
   - A voice session captured "20 tons/month of eucalyptus firewood, WhatsApp tomorrow 11 AM".
   - The chat the next day opened on that topic, answered "how much did I say?" without re-asking, and refused to give out a buyer's phone number.
6. **Bugs found by testing:**
   - The summariser left callback times relative ("tomorrow"). We now give it today's date.
   - It recorded a refused request for a buyer's phone number as an "open thread". We added a policy line and a filter.
   - With empty context, the hosted agent claimed to "have your profile". We set a default value that marks cold start, plus a rule; it now says it has no earlier details.
   - The SDK needs a separate Voice Agents API key (`X-API-Key`), not the dashboard key.
7. **Live voice through the team agent** (`Conversatio-c49cd61c-ee22`), with the file passed in when the session starts. We tested it with an automated caller (`globalctx/voice/sim_caller.py`): Sarvam TTS generates the caller's voice, and Saaras transcribes the agent's audio.
   - **Seller:** the agent opened with the right Horse Gram Dal enquiry and read back "500 kg at Rs 95/kg". The file updated in 19 ms.
   - **Buyer:** the second call opened with "Pichhli baar humari baat… hui thi" and confirmed "50 pieces at Rs 300" without asking again. This is resume across sessions.
   - **What we learned:**
     - The Voice Agents key is different from the dashboard key. A dashboard key returns "Invalid API key format".
     - An uncommitted agent needs `version=1`.
     - The agent only starts speaking once the caller's audio stream begins.
     - A silent caller is ended after the inactivity nudges.
     - Use the server's `status: completed` marker to detect the end of a turn.
     - Streaming STT sends growing partial transcripts, which we merge before saving.
8. **Cost decision (Day 2): approach C.**
   - We compared three ways of writing the opening line:
     - an LLM after every change
     - an LLM just before each call
     - rules on every change, with the agent phrasing the line
   - We chose rules. The voice agent is already an LLM, so writing its first line with a second LLM pays twice.
   - The result: 0 LLM calls per event, openings in 5–15 ms, and only about 1 LLM call per conversation (the summary).
   - Rule-based openings also exposed two data bugs, which we fixed:
     - free-text "pending" threads were being taken as real requests
     - a callback saved as "tomorrow" before the date-conversion fix
9. **Grouped by need, not by data source (Day 2).**
   - Buyers had three sections for one need (enquiries, categories searched, sellers contacted). The bot had to join
     them itself. Now "Buying Needs" has one row per product: requirement posted, searches, enquiries, sellers called.
   - Sellers get "Buyer Demand by Product": enquiries, buyer calls and BuyLeads per product, then a totals row.
     We avoided the name "Leads" because on IndiaMART a lead means a BuyLead specifically.
   - **What we learned:**
     - Buyer calls only carry an MCAT id, so we name them after the product most enquired under that MCAT.
     - Matching on one shared word merged "Plastic Containers" with "Plastic Bottle Making Machine". Two shared
       words (or one for a one-word label) fixed that.
     - A chat requirement was being shown as the "details" of an unrelated posted requirement (ERP software vs
       safety helmets). Details now attach only when they're about the same product.
10. **Evals (Day 2).** `python -m globalctx.evals` runs a scripted 3-turn chat per GLID, once with the file and
    once without (the cold baseline), 3 times each, and an LLM judge (sarvam-105b) marks every question that asks
    for something already known. Exact checks cover invented numbers, size and schema.
    - **Results:** re-asks dropped from 55 without the file to 10 with it (1 real after manual review). The bot picked
      up the open thread 90% of the time vs 39% without. Privacy was refused 39/39, cold start was clean 6/6, and
      freshness median was 13 ms.
    - **What the evals caught:** the bot asked an unknown seller's name on every turn, and raised the buyer's other
      needs unprompted. We fixed both in the prompt. Do-Not-Ask said "name" when only the business name was known.
    - **What we learned:** the judge needs the known-facts list and examples of indirect asks ("aap kya khareedna
      chahte hain?"), otherwise it misses re-asks in the baseline. It also flagged the user's own name as a privacy
      leak, and "9 Oct" vs "09 Oct" as an invented number. Always read the flagged items before you publish a number.
11. **Privacy:** role-play conversations on real GLIDs are labelled synthetic, and `samples/` is generated with GLIDs, people and companies redacted.

## What we learned
- Keeping facts deterministic made the files trustworthy and fast. The LLM is the slowest part and the easiest one to get wrong.
- "Do-Not-Ask" has to be derived from what survived selection. Otherwise the bot claims facts the file no longer holds.
- Testing the unhappy paths (cold start, someone asking for another party's details) found more bugs than testing the happy path.
