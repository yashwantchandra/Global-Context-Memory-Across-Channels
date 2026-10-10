# Submission form answers (paste field by field)

## PROJECT TITLE
AudioMind

## SHORT PITCH (1 sentence, max 280 chars)
AudioMind gives IndiaMART's voice bot a memory: one short buyer.md or seller.md per GLID, rebuilt in about 13 ms after every event, so Mira opens with the user's own context and continues across call and chat without asking again.

## PROBLEM STATEMENT
PS02 – Global Context: Memory Across Channels

## APPROACH NOTE (min 100 chars, Markdown)
**Goal:** Mira, the voice bot, knows the buyer or seller before she speaks, and a chat today continues yesterday's call.

**Data and lookback**
- Seller dataset (11 files, 5,000 sellers): enquiries, buyer calls, BuyLeads, WhatsApp, VANI and executive calls.
- Buyer getContext snapshots (237 buyers).
- Our own phone and chat conversations, written back after every session.
- Each source has its own lookback window (30–90 days; 12 months for past needs), with the reason documented.
- Synthetic data is labelled in each file and row.

**Pipeline (runs locally; only outbound calls to Sarvam)**
1. Every event lands in an event store keyed by GLID and rebuilds only that GLID's file (median 13 ms).
2. Facts are plain Python, not LLM: counts, statuses and needs grouped per product.
3. Each file has one fixed schema per role and a 2,500-character budget (about 700 tokens). Rows are ranked, capped and evicted, and Do-Not-Ask is derived from the rows that survive.
4. The opening line is chosen by rules from the same facts (callback, then pending request, then last call's topic, then enquiry or requirement), so it costs 0 LLM calls per event.
5. The LLM (sarvam-105b) runs once per finished conversation and turns the transcript into a structured summary. That summary is written back as an event and rebuilds the file.

**Bot and channels**
- Hosted Sarvam Voice Agent ("Mira", sarvam-105b-conversations) gets the file and the opening as agent variables.
- It checks identity first, then resumes the open thread, confirms known facts instead of asking, and switches language with the user.
- Channel 1 is an internet voice call from the browser (Sarvam Web SDK, key kept server-side) or a phone test call.
- Channel 2 is a web chat on the same file and event store.

**Beyond the bot:** a Customer 360 page, executive call prep that reads the file by heading with no LLM, request folders for catalogue updates and requirements, and a parsed JSON API.

**Privacy:** only what the bot needs is kept. One party's details never appear in the other's file, and the bot refuses such requests (39 of 39 in evals). Customer data never leaves the laptop.

**Delivered:**
- buyer.md and seller.md for 5 + 5 real GLIDs, 2 cold starts and 2 synthetic cases
- a two-channel resume demo
- a freshness log
- evals
- a one-slide summary
- skills.md
- code and README

## SARVAM AGENT ID / LIVE LINK
Conversatio-c49cd61c-ee22

## SOLUTION FOLDER (GitHub repo)
https://github.com/yashwantchandra/Global-Context-Memory-Across-Channels

## DEMO VIDEO URL
[paste the YouTube / Loom / Drive link after recording]

## IMPACT ON SUCCESS METRICS (min 100 chars, Markdown)
We measured every PS02 metric with our own evals (`python -m globalctx.evals`).

**How we measured**
- **Bot:** Mira (sarvam-105b-conversations) with the same file and rules as the phone agent, on our local chat path.
- **Scale:** 13 GLIDs (5 real buyers, 5 real sellers, 2 cold starts, 1 synthetic case) × 3 runs = 39 conversations.
- **Script:** a user who reveals nothing: "Haan ji, boliye" → "Aage kya karna hai?" → "Aur kuch jaanna hai aapko mujhse?". The last line invites questions, so any re-asking shows up.
- **Baseline:** the same bot and script with no file (a cold start), judged against the real file, so the difference is what the file adds.
- **Judging:** an LLM judge (sarvam-105b) plus exact code checks. We read every flagged item by hand and report both counts.

**1. Resumed without re-asking (25%)**

| Check | With the file | Without the file |
|---|---|---|
| Questions asking for something already known | **10** (1 real after manual review) | 55 |
| Conversations with zero re-asks | **24/33 (72%)** | 9/33 (27%) |
| Bot raises the open thread or last conversation first | **30/33 (90%)** | 13/33 (39%) |

Without memory the bot asks almost 2 known questions per conversation ("aapka naam?", "kya khareedna hai?"); with the file it confirms instead ("100 pairs hi chahiye na?").

Our manual review of the 10 flags:
- 1 real re-ask: a seller's name asked twice
- 6 confirmations or open questions the judge miscounted
- 3 unneeded questions (budget twice, GST once)

The 3 conversations that didn't pick up a thread were all one seller with no open thread to resume.

**2. Freshness (20%)**
- New event → updated file: **median 13 ms, p95 25 ms** over 32 events. The system logs this itself, and each file carries `freshness_ms`.
- End to end: a web call or chat is written back the moment it ends (9 ms rebuild measured). A phone call adds the poll wait (up to 15 s) plus one LLM summary.

**3. Compactness and structure (20%)**
- **14 of 14** files are within the 2,500-character budget (median 1,781 chars, about 500 tokens).
- Every file has the fixed section order for its role and stays within its row caps; rows are at most 120 characters.
- Evictions are recorded in the front-matter, and Do-Not-Ask only lists facts still in the file.

**4. Personalised-opening lift (15%)**

| Check | With the file | Without the file |
|---|---|---|
| Uses the person's own name (where known) | **21/21** | 0 |
| Names a real product, requirement or thread | **33/33** | 0 |
| Every number in the opening is in the file | **33/33** | – |

The opening is chosen by rules from the file (callback, then pending request, then last call's topic, then enquiry or requirement), so it costs 0 LLM calls.

**5. Reusability (20%)**
The same files feed four consumers with no rework, because there's one fixed schema per role:
- a Customer 360 page
- executive call prep (reads the file by heading, no LLM)
- request folders (catalogue updates, requirements, enquiries) for ops teams
- a parsed JSON API for dashboards and campaigns

**6. Safety**
- Cold start: **6/6** with the generic opening, no claimed history and no invented numbers.
- Privacy: asked for the other party's name and number, Mira refused **39/39** and leaked nothing.

**What the evals changed**
The first run caught real faults, which we fixed in the prompt and the file:
- the bot asked an unknown seller's name on every turn
- it raised buyers' other needs unprompted
- Do-Not-Ask didn't separate the business name from the person's name

Re-asks per conversation fell from 1.6 to 0.3.

**Expected benefit at scale**
- **For buyers and sellers:** no repeating themselves; calls open on their open deal, callback or request.
- **For executives:** a 30-second brief before they dial.
- **Cost:** stays flat at 0 LLM calls per event and 1 per conversation. Our heaviest real seller (2,293 events) builds in 28 ms.
- **At 10 lakh events a day** (about 12 per second): an event log, per-GLID incremental state, and the file rendered at call start through Sarvam's on-start hook.

**Limits**
- The evals ran on chat as a stand-in for voice (same file and rules).
- The judge is an LLM, which is why we report reviewed counts too.
- There are no live-traffic business numbers yet; that is the next step, an A/B test on real calls.
