# Global Context: Memory Across Channels (PS02)

IndiaMART Voice AI Hackathon 2.0 entry. The voice bot (Mira) already knows the buyer or seller before it speaks, and a conversation started on a call continues in chat without anything being asked twice.

## What it does, in plain words
1. **Collects history** for each user (GLID) from enquiries, buyer calls, buy-leads, WhatsApp, VANI bot calls, executive calls and our own conversations.
2. **Writes one short file per user**: `seller/<glid>.md` or `buyer/<glid>.md`.
   - Each file has the same 8 to 10 sections and is never longer than 2,500 characters.
   - Facts come from plain code. The LLM only writes the opening line and the summary of each conversation.
3. **Loads the file into the bot** before every call or chat. The bot opens with a personalised line, follows "Do-Not-Ask" and continues the open threads.
4. **Writes every conversation back**, which rebuilds that user's file in about 10 ms. The next channel then starts from where the last one stopped.
5. **The same file is reused outside the bot.** An executive call-prep page reads it by section headings, with no LLM involved.

6. **Knows who it is talking to.**
   - Every file starts with an **Identity** line: name, plus preferred language and how we know it.
   - The opening greets the person by name in that language. A new GLID gets a neutral greeting with no name.
7. **Buyer or seller is never hard-coded.** At the start of every session we check both the `buyer/` and `seller/` files for the GLID:
   - If only one exists, it's used.
   - If both exist, the one with the most recent activity wins.
   - If neither exists, a cold-start file is created. It moves to `seller/` if the conversation shows the person is a seller.
8. **Requests are filed for action.** These are written to separate folders, one `.md` per GLID, and also listed as Open Threads so the bot follows up:
   - a seller's catalogue or price changes go to `requests/catalogue_updation_requests/`
   - a buyer's requirement posts go to `requests/requirements/`
   - a buyer's enquiries go to `requests/enquiry/`

Everything runs on the laptop. The only outbound calls go to Sarvam (the LLM, the voice agent and analytics). Real customer data never leaves the premises: raw data and real files stay in `data/`, which is gitignored, and `samples/` holds only redacted copies.

## Run it
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add SARVAM_API_KEY (+ SARVAM_AGENTS_API_KEY for the hosted agent)
# put the organiser data in the repo root: "Global Context - Seller Dataset/" and the buyer CSV
.venv/bin/python -m globalctx.ingest          # ~7 s: 330k seller events + 251 buyer snapshots -> data/gc.db
.venv/bin/python -m globalctx.pick_demo       # 5 sellers + 5 buyers with rich history, + 1 cold-start each
.venv/bin/python -m globalctx.build --demo    # writes data/profiles/<role>/<glid>.md
.venv/bin/uvicorn globalctx.app:app --host 127.0.0.1 --port 8765
```
Open http://127.0.0.1:8765. It has:
- **`role.md` links** to view each file.
- **Chat** (channel 2), with the live file shown beside the conversation.
- **Call prep**, the non-bot use of the same file.
- **+ new activity**, which posts a synthetic enquiry or buy requirement and shows how many ms the file took to update.
- A live **freshness log**.

Voice (channel 1) uses the hosted Sarvam agent, with the file passed in when the session starts. The easiest way
is the **🎙 Call** button in the web app (or `http://127.0.0.1:8765/call/<glid>`): an internet call from the laptop
mic, no phone line needed. Use earphones. The transcript is written back the moment you hang up. **↺ Reset file**
removes the conversations and requests from our channels, so a demo can be replayed.

From the command line:
```bash
.venv/bin/python -m globalctx.voice.sdk_session --role seller --glid <glid>          # laptop mic (needs PyAudio)
.venv/bin/python -m globalctx.voice.sdk_session --role seller --glid <glid> --chat   # same agent over text
.venv/bin/python -m globalctx.voice.phone payload --role seller --glid <glid>       # variables for a phone test call
.venv/bin/python -m globalctx.voice.phone poll                                      # pull finished calls back
```
Tests: `.venv/bin/python -m pytest -q tests`.

Synthetic case GLIDs (no customer data), with their built files, are in `samples/cases/`:
Dipu (`910000101`, gumboots) and Raju (`SYN-B-2001`, Polo T-shirts, from the team's v2 story). Load them with
`.venv/bin/python -m globalctx.cases samples/cases/*.json`.

Evals (about 2.5 min, about 120 Sarvam LLM calls): `.venv/bin/python -m globalctx.evals`. It scores the PS02 metrics
(re-asks with and without the file, resuming the thread, the opening, cold start, privacy, size, freshness) over 3
runs per GLID, and writes `samples/evals/eval_results.md`. Our read of every flagged item is in `samples/evals/manual_review.md`.

## Presentation
The deck (architecture, opening logic, write-back flowchart, evals, one-slide summary):
https://claude.ai/artifact/BCpB3xhVJqRs7EGhEJE242 (private until shared from its Share menu; downloads as .pptx or PDF).

## Sarvam agent
- Agent ID: `Conversatio-c49cd61c-ee22` (team workspace).
- Input variables are `context`, `role`, `glid` and `opening`.
- Output variables are `call_summary`, `requirement`, `quantity`, `callback_time`, `next_step` and `disposition`.
- The prompt is in `globalctx/agent_prompt.md`.

## Code map
| Path | What it is |
|---|---|
| `globalctx/ingest/` | Loaders for the seller CSVs and the buyer getContext API log |
| `globalctx/store.py` | SQLite event store keyed by GLID, plus the latest file per GLID |
| `globalctx/build/facts.py` | Turns events into candidate rows per section (plain code, no LLM) |
| `globalctx/build/select.py` | Section caps, ranking, eviction and the 2,500-char budget |
| `globalctx/build/narrative.py` | LLM opening line and session summary, with template fallbacks |
| `globalctx/build/privacy.py` | Removes phone numbers, emails, links and IDs |
| `globalctx/refresh.py` | Event → rebuild of that GLID (fast pass, then LLM pass) + 5-minute sweep |
| `globalctx/sessions.py`, `app.py`, `web/` | Chat channel, call-prep page, simulate activity, freshness |
| `globalctx/voice/` | Hosted-agent SDK session, simulated caller for automated voice tests, and the phone-call poller |
| `globalctx/resolve.py` | Picks the buyer or seller file for a GLID (one, most recent, or a new cold-start file) |
| `globalctx/build/identity.py` | Name and preferred-language detection |
| `globalctx/build/requests.py` | Request folders: catalogue updates, requirements, enquiries |
| `globalctx/phonemap.py` | Maps a demo phone number to a GLID (also matches Sarvam's hashed caller ID) |

See `APPROACH.md` for design decisions and `skills.md` for how we built it.
