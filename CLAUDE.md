# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Our entry for **IndiaMART Voice AI Hackathon 2.0** (9–10 Oct 2026, in office). Chosen problem: **PS02 – Global Context: Memory Across Channels**. The goal is for the Voice Bot to already know the buyer or seller, and their history, before it speaks.

Source docs (read them again if anything here seems off):
- Deck (rules, judging criteria, schedule): https://docs.google.com/presentation/d/1bn_W7NJ77So3cOsuSJhrhmcYjsaQ7JTN
- PS02 brief: https://docs.google.com/document/d/1MONiQTYED3ZXcoKbR1rlYEvkulBOzVgb

**Hackathon rule: all build work happens during the 2 days (9–10 Oct), and pre-built solutions are disqualified.** Code was started on Day 1 (9 Oct) in the `globalctx/` package; see README.md for commands and APPROACH.md for design decisions.

## What we must ship (submission window: 10 Oct, 2:00 PM to 11:59 PM; late entries are not evaluated)

1. **Context layer**: a pipeline that builds `buyer.md` and `seller.md` for at least 3 buyers and 3 sellers, keyed by **GLID**.
2. **Bot demo across 2+ channels**: a voice call on Sarvam, then a WhatsApp or chat interaction for the same GLID. The demo must show a personalised opening and a conversation that resumes without re-asking anything.
3. **Freshness evidence**: the measured delay between a new activity and the updated file.
4. **At least one use outside the bot** that reads the same files (for example executive call prep, a sales dashboard or a WhatsApp campaign).
5. **One-slide presentation**: a sample buyer.md and seller.md, plus a workflow covering data sources, refresh mechanism, lookback windows, the LLM used and integration logic.
6. Submission package: a 5–7 minute demo video, the Sarvam agent ID or live link, a code folder with README, **`skills.md`** (our build journey and tools used, scored under 15%), sample outputs, a note on the problem and approach, and team details.

## How we're judged

**Overall rubric** (each juror scores 1–5): Business Impact 25%, Solution Completeness 20%, Technical Robustness 20%, Voice Experience (natural, low latency, Hinglish) 20%, Demo & skills.md 15%.

**PS02 metrics**: Resumed without re-asking 25%, Freshness 20%, Compactness & structure 20%, Reusability 20%, Personalised-opening lift 15%.

When deciding trade-offs, favour these in order: cross-channel resume working end to end, then measurable freshness, then tight fixed-schema files. Polish comes after all three.

## Hard requirements from the brief

- **Cold start**: a GLID with no history must get a clean, generic opening, and this is explicitly evaluated. Partial or conflicting data must not crash anything or produce made-up claims.
- **Lookback windows**: choose one per data source and write down why.
- **Refresh mechanism**: event-driven, scheduled or hybrid, and the latency has to be measured.
- **File size**: files must stay short and consistently structured so they fit in a prompt. Document the size budget and the reasoning for it.
- **Privacy**: include only what the bot needs, and no sensitive PII. **The bot must never reveal one party's information to the other** (for example, buyer details on a seller call).
- **Synthetic data**: allowed to fill gaps, but it must be clearly labelled in the files and the demo.
- **Customer data**: it must not leave the premises. Send real data only to the provided Sarvam or LLM endpoints, and never to other third-party services.
- The files must be Markdown. Internal storage can be in any format.

## Platform

Sarvam AI (keys and credits on dashboard.sarvam.ai; docs at docs.sarvam.ai; Python and JS SDKs, REST and WebSocket):
- Saaras: speech-to-text, 22 Indian languages plus Hinglish
- Bulbul: text-to-speech
- Sarvam LLMs and Mayura (translation)
- Voice Agents: telephony, WhatsApp, web widget and API

**Building agents (Sarvam Voice Agents, at indus.sarvam.ai → Build → Agents).** An agent is made of:
- **Instruction:** the greeting plus the system prompt.
- **Variables:** *input* variables are filled from telephony metadata, the **on-start hook** or campaign CSV columns, and can be used in the greeting or prompt with `@`. *Output* variables are pulled out after the call using extraction prompts.
- **Speakers & voice:** starting language, whether switching language mid-call is allowed, and the voice.
- **Tools:** HTTPS API tools that can save a reply into variables, plus data validation and code tools.
- **On-start hook:** fetches context before the agent speaks.
- **On-end hook:** pushes the disposition, collected fields and transcript after the call.
- **Knowledge base.**

Channels: telephony (inbound, or outbound via campaigns), web widget embed, WhatsApp, and API/SDK. The SDKs are `sarvam-conv-ai-sdk` for Python (`AsyncSamvaadAgent`) and Web. Keep API keys server-side.

How this maps to PS02. **Decision (8 Oct): everything runs locally.** No API of ours is exposed to the internet, and we only make outbound calls to Sarvam. Every session is started by our code, so we **push** context in at start and **pull** results out. We use no hooks.

**Two voice-call modes, both implemented:**
1. **Browser call (local, primary for dev and the demo).** A teammate playing the buyer or seller talks through the laptop mic in our local web page.
   - Our code reads the GLID's latest .md and starts the hosted agent through the Python SDK (`AsyncSamvaadAgent`, `InteractionConfig(user_identifier=<GLID>, agent_variables={context, role, glid})`).
   - Turns arrive live through `transcript_callback`. On `interaction_end` the file is rebuilt immediately, which gives the best freshness.
2. **Real phone call (Sarvam instant outbound).** Our local code calls `POST …/outbounds` with the phone number and the .md in `app_config.agent_variables`, and Sarvam places the call.
   - Nothing streams back, so a local poller hits the analytics API (`GET …/interactions?start_datetime&end_datetime`, then `…/transcripts/{interaction_id}`), writes the results as events and rebuilds the file.
   - This needs a Sarvam phone number and connection on our workspace (`connection_id`, `agent_phone_number`), so ask the organisers.
   - **Only call teammates' phones.** Never call real IndiaMART customers without written organiser approval (consent and DND risk).

**Shared by both modes:**
- Each event triggers a local rebuild of that GLID's file only.
- The freshness log records "transcript to file", plus "structured output variables to file" when we poll for them.
- **Second channel:** a local web chat (Sarvam LLM) that uses the same .md file and event store and picks up the voice call without re-asking anything.
- **Inbound phone or WhatsApp (started by the user)** would need an on-start hook reaching our endpoint. That's out of scope, and we describe it in the design note as "how other channels plug in".

Docs index: https://docs.sarvam.ai/conversations/llms.txt

### Sarvam API reference notes (read 8 Oct 2026)

Sources: [Models](https://docs.sarvam.ai/api/getting-started/models) · [API reference](https://docs.sarvam.ai/api-reference/introduction) · [Voice agent with LiveKit](https://docs.sarvam.ai/api/integration/build-voice-agent-with-live-kit) · full dump: https://docs.sarvam.ai/llms-full.txt

**Models**

| Model | ID used in code | Use it for |
|---|---|---|
| Saaras v4 (STT) | `saaras:v4` | Speech-to-text. 22 Indian languages plus English. Modes: `transcribe`, `translate` (gives English), `verbatim`, `translit`, `codemix`. Handles Hinglish. v3 still works. |
| Bulbul v3 (TTS) | `bulbul:v3` | Text-to-speech. 10 Indian languages plus English. Pitch, pace and speaker can be tuned. Speakers include `shubh` (default), `aditya`, `anand`, `priya`, `simran` and `kavya`. |
| Sarvam-105B | `sarvam-105b` | Chat LLM with 128K context, for reasoning and agentic work. **Use for the profile-builder summary step.** |
| Sarvam-105B Conversations | `sarvam-105b-conversations` | Chat LLM with 32K context, tuned for real-time dialogue. **Use inside the voice/chat agent.** |
| Mayura / Sarvam-Translate | — | Translation (11 and 23 languages respectively). |
| Sarvam Vision | — | Document OCR to HTML, Markdown or JSON. Probably not needed. |
| Voice cloning | — | Covers 12 languages. Not needed. |

- **Deprecated:** Sarvam-M and Sarvam-30B. Don't use them; use `sarvam-105b`.
- **Open-weight models (beta)** are served on a `/v2` endpoint with the same key: GLM-5.3, Gemma 4 31B and DeepSeek V4 Flash. They are rolling out gradually, so don't depend on them.
- Language codes: `en-IN`, `hi-IN`, `bn-IN`, `ta-IN`, `te-IN`, `gu-IN`, `kn-IN`, `ml-IN`, `mr-IN`, `pa-IN`, `od-IN`, and `unknown` for auto-detect.

**REST API**
- The base URL is `https://api.sarvam.ai`. Authenticate with the header `api-subscription-key: <KEY>`; `Authorization: Bearer <KEY>` is also accepted. A bad key returns **403** with `error.code = invalid_api_key_error`.
- Endpoints:
  - `/v1/chat/completions`: chat
  - `/speech-to-text` and `/speech-to-text-translate`: STT
  - `/text-to-speech`: TTS
  - `/translate` and `/translate/document/jobs`: translation
  - `/transliterate`: transliteration
  - `/text-lid`: language ID
  - `/voices/create` and `/voices/clone`: voice cloning
- **`POST /v1/chat/completions` is OpenAI-shaped** (`messages`, `choices[].message`, `tool_calls`, `usage`):
  - `temperature` defaults to 0.2. `max_tokens` defaults to 2048.
  - `reasoning_effort` accepts `low`, `medium` (default), `high` or `max`. Set it to `None` to turn reasoning off, which is better for latency.
  - `stream: true` streams the reply as SSE. `tools` accepts function tools only.
  - `response_format` accepts `json_schema`, which is useful for structured summary output.
  - The reply can include `reasoning_content`; strip it before rendering .md files.
- The docs don't state rate limits. Expect throttling and add retry with backoff.

**Realtime voice agent (LiveKit path, an alternative to the hosted Voice Agents)**
- Install with `pip install "livekit-agents[sarvam,silero]" python-dotenv` (needs Python 3.9+). Put `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and `SARVAM_API_KEY` in a `.env` file in the same folder as the script.
- Wiring:
  - Subclass `Agent(instructions=..., stt=..., llm=..., tts=...)`.
  - STT: `sarvam.STT(language="unknown", model="saaras:v4", mode="transcribe", flush_signal=True)`
  - LLM: `sarvam.LLM(model="sarvam-105b-conversations")`
  - TTS: `sarvam.TTS(language_code="hi-IN", model="bulbul:v3", speaker="shubh")`
  - In `on_enter`, call `self.session.generate_reply()` so the agent speaks first.
- Start it with `AgentSession(turn_detection="stt", min_endpointing_delay=0.07)` and `session.start(agent=..., room=ctx.room)`, then launch with `cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))`. To test, run `python agent.py dev` and, in a second terminal, `python agent.py console`.
- **Context injection for PS02:** before you construct `Agent`, fetch the GLID's `buyer.md`/`seller.md` and put it into `instructions`. This is the equivalent of the on-start hook.
- Gotchas:
  - **Don't pass `vad`** to `AgentSession`; it conflicts with the plugin's own VAD.
  - STT latency is about 70 ms.
  - If transcription is poor, use `language="unknown"` or set the exact language code.
**Local-only APIs we rely on (checked 8 Oct)**
- **Python SDK:**
  - Install with `pip install "sarvam-conv-ai-sdk[all]"` (needs PortAudio).
  - Pass `InteractionConfig(org_id, workspace_id, app_id, user_identifier=<GLID>, user_identifier_type=CUSTOM, interaction_type=CALL, sample_rate=16000, agent_variables={...})` to `AsyncSamvaadAgent(api_key, config, audio_interface=AsyncDefaultAudioInterface(...), transcript_callback=...)`.
  - It connects out over WebSocket from our machine, using the local mic and speaker.
  - The callbacks `transcript_callback(ServerTranscriptMsg role/content)` and `event_callback` (which includes `interaction_end`) give us the write-back events.
- **Instant outbound phone call:**
  - `POST https://apps.sarvam.ai/api/outbounds/v1/orgs/{org_id}/workspaces/{workspace_id}/outbounds`.
  - The body is `app_config{app_id, app_version, connection_config{connection_id, agent_phone_number}, agent_variables}` plus `user_config{user_phone_number}`. It returns `attempt_id`.
  - Leave `webhook_config` out; we can't receive webhooks.
- **Transcripts:** `GET https://apps.sarvam.ai/api/analytics/v1/{org_id}/{workspace_id}/{app_id}/transcripts/{interaction_id}` (header `X-API-Key`). The response schema isn't documented, so check it on Day 1.
- **Not documented, check on Day 1:**
  - the size limit on `agent_variables` (this sets our .md budget)
  - whether the SDK has a text/chat interaction type
  - how to map `attempt_id` to `interaction_id` when polling

**How the hosted agent could reach our .md files (reference only; this needs a public endpoint, so we are NOT using it)**
- The hosted agent has no direct access to our files. It only reaches them through HTTPS calls to our service:
  - **Read:** the on-start hook calls our API, and fields in the reply are mapped into input variables before the agent speaks.
  - **Mid-call:** an API tool (GET/POST/PUT/PATCH/DELETE, max wait up to 30 s) can fetch more data or post an event. Values can be inserted with `@`, including call context such as User Identifier, Call Transcript and Interaction ID.
  - **Write-back:** the on-end hook pushes output variables, the disposition and the transcript to our endpoint. Our service then rebuilds the .md file. The bot never edits the file itself.
- Our endpoint must be reachable over public HTTPS. If it sits behind a firewall, allowlist Sarvam's IP `4.213.167.70`. Auth options are none, bearer, api_key or basic.
- Not documented, so test on Day 1: the exact hook payload schema, hook timeout, variable size limits, and whether the WhatsApp channel fires the same hooks.

**Sarvam Voice Agents MCP server (checked 9 Oct)**: [docs](https://docs.sarvam.ai/conversations/mcp)

This is a **dev-time tool, not part of the runtime.** It lets Claude Code configure and test the hosted agent, but our app doesn't use it at runtime. It has **no per-user memory or context tool**, so it doesn't replace our context layer.

- **Setup:**
  - Add it with `claude mcp add --transport http --scope project sarvam-voice-agents https://mcp.sarvam.ai/voice-agents`.
  - Then `/mcp` → Authenticate (Sarvam SSO). The token lasts 24 h with no refresh, so sign in daily.
- **Useful tools:**
  - `configure_agent`: create or update the prompt, input/output variables and voice. Changes go to a draft until `commit`.
  - `place_test_call` (needs `verify_phone_number` first): places a test call.
  - `send_chat`: tests the agent in chat.
  - `analytics`: transcripts and interaction traces.
  - `evals` with `configure_scenario` and `run_eval`: automated "resumed without re-asking" tests.
- **Limits:**
  - 120 calls a minute.
  - Lists are replaced whole on write.
  - `commit`, `place_test_call` and `delete_*` can't be undone or cost money.
  - `upload_agent_code` (server-side hooks and state) is enterprise-only.
- **Confirms our local-only design:** Sarvam **refuses tool URLs that resolve to internal or private addresses**, so a hosted hook could never reach a laptop on the office network.
- **Data caution:** transcripts read through this MCP go into Claude's context. Only use it on role-play or synthetic sessions, never real customer conversations.

- **Which path to use:** the hosted Voice Agents (indus.sarvam.ai) give us telephony, WhatsApp and hooks with no extra work, so they are the default. Fall back to LiveKit only if we need full control of the pipeline or have to measure latency ourselves.

These organiser resources were still "to be shared" as of 8 Oct: the starter API list, sample GLIDs, sample voice and WhatsApp conversations, and the current bot prompt. Add their locations here once they arrive.

## Code (built 9 Oct)

- Package `globalctx/` (not `gc`: that name clashes with Python's built-in garbage-collector module).
- Commands:
  - `python -m globalctx.ingest`
  - `python -m globalctx.pick_demo`
  - `python -m globalctx.build --demo`
  - `uvicorn globalctx.app:app --port 8765` (port 8000 is taken on this machine)
  - `python -m globalctx.voice.sdk_session`
  - `python -m globalctx.voice.phone`
  - `python -m globalctx.samples`
  - `pytest tests`
- Data, the SQLite DB and real profile files live in gitignored `data/`. Only redacted `samples/` are committed.
- The hosted agent is `Conversatio-c49cd61c-ee22` in the **team workspace** (org `01a1109e-edab-…`, workspace `01a1109e-edb1-…`), configured through the project Sarvam MCP. `Conversatio-f58d078f-8b4b` in the personal workspace is an identically configured backup. Its prompt source is `globalctx/agent_prompt.md`.
  - Input variables: context, role, glid, opening.
  - Output variables: call_summary, requirement, quantity, callback_time, next_step, disposition.
  - It is still uncommitted (draft v1). Commit only with the user's OK.
- The SDK and analytics need `SARVAM_AGENTS_API_KEY` (a Voice Agents key from indus.sarvam.ai). This is separate from `SARVAM_API_KEY`, which is used for the LLM.
- Role-play conversations are labelled synthetic (`GC_ROLEPLAY=1`, the default).
- **Openings are rule-based (approach C, 10 Oct) and NOT in the .md:** `narrative.template_opening`, stored in `profiles.opening`, read with `sessions.opening_for(glid, role)` and sent as the agent's `opening` variable; with 0 LLM calls per event; the agent prompt says to phrase it naturally. `GC_OPENING=llm` restores LLM openings. The LLM runs only for end-of-conversation summaries.
- **Role is never hard-coded:** use `sessions.load(glid)` or `resolve.resolve(glid)` (only one / most recent / new cold start). Every file has an `## Identity` section with name and preferred language, and the opening uses both.
- **Requests** go to `data/requests/{catalogue_updation_requests,requirements,enquiry}/<glid>.md`, with types restricted by role (`config.REQUEST_TYPES_BY_ROLE`).
- **Phone test calls:** MCP `place_test_call` drops custom `app_variables`. To test a phone call with context, set the agent draft's *default* variables to the GLID's file, call, then reset the defaults to cold start.
- **Sarvam caller ID** is `sha256("+91XXXXXXXXXX")`; `phonemap.lookup` matches it.
- **Analytics API:** `/interactions` returns 500 when given `sort_by`, so we sort locally. `/transcripts/{id}` returns `messages[{role, content, language_name}]`.
- **SDK voice:** an uncommitted agent needs `version=1`. The agent speaks only after the caller's audio starts. Use the audio chunk `status: completed` to detect the end of a turn, and merge streaming partial transcripts (`sessions.merge_partials`).

## Architecture

```
sources (starter APIs + labelled synthetic)  ──►  connectors/  (one per source; applies its lookback window)
        │
        ▼
event store keyed by GLID  ◄── channel transcripts written back (browser call / phone call / chat)
        │  (each new event triggers a rebuild of that GLID only; a scheduled sweep acts as backstop)
        ▼
profile builder  ── deterministic aggregation for facts (counts, statuses, categories)
                 └─ LLM only for "last conversation / open threads" summary + suggested opening
        │
        ▼
renderer → buyer.md / seller.md  (fixed section order, token budget, front-matter with
                                  glid, role, generated_at, last_event_at, freshness_ms, synthetic flag)
        │
        ├──► session runner (local) → hosted Sarvam agent, file passed as agent_variables at start
        │       ├─ browser call via SDK: transcript streamed back live → event → rebuild
        │       └─ phone call via instant outbound: analytics API polled → event → rebuild
        ├──► local web chat (Sarvam LLM): same file → resumes the thread, writes events back
        └──► non-bot consumer (e.g. exec call-prep view) reading the same file
```

Design principles:
- **Facts are deterministic; the LLM does narrative only.** This keeps the files consistent, quick to rebuild and free of invented details.
- Every channel writes its conversation back as an event. That write-back is what lets a WhatsApp chat today continue yesterday's voice call.
- Freshness is measured as `generated_at − last_event_at` and reported by the system itself, so we have the evidence ready for the demo.
- Use one fixed schema for each role. Other consumers should parse sections by heading and never need to call an LLM.

## Working conventions

- **Keep `skills.md` updated as we build**, with what we did, which tools we used and what we learned. It is a scored deliverable, and it's very hard to reconstruct at 11 PM on Day 2.
- Label synthetic data at the source (for example `synthetic: true` in front-matter or per record).
- Keep sample outputs for the submission folder in a `samples/` directory: generated .md files, freshness logs and switch or resume transcripts.
- Teams are 3 people and at least one is non-technical. Keep the README and the slide readable for that teammate.
