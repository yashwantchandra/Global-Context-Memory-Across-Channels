# skills.md: how we built Yaad

A running log of what we did, which tools we used and what we learned. Newest at the bottom.

## Tools
- **Claude Code** (in VS Code) as a pair programmer for planning, architecture, code, tests and Sarvam agent setup.
- **Sarvam Voice Agents MCP** (`mcp.sarvam.ai/voice-agents`): created and configured the hosted agent
  (variables, voice, prompt) and tested it over chat from inside the editor.
- **Sarvam API MCP** (`sarvam-mcp`) and **Sarvam docs MCP** (`docs.sarvam.ai/_mcp/server`), added for STT/TTS/LLM
  tools and live doc search.
- **Sarvam conv-ai SDK** (`AsyncSamvaadAgent`) for voice and chat sessions; **Sarvam-105B** for extraction.
- Python 3.12, FastAPI, SQLite, `uv`.

## Day 1 · 9 Oct

1. **Understood the data before designing.** We profiled the seller dataset with aggregates only (counts,
   categories, date ranges), never printing customer text. Two numbers shaped the product: 80% of enquiries have no
   seller reply, and 42% of VANI calls end "Not Interested".
2. **Whiteboard to product.** The team's whiteboard insight ("the buyer was searching and the requirement is still
   not fulfilled") became the core idea: memory is a list of *open threads* (unfinished work), not a biography.
3. **Architecture decisions** (recorded in CLAUDE.md): an event store, deterministic thread rules, an LLM only for
   free-text extraction, a fixed six-section file with a token budget, and an inline rebuild per event.
   Considered and rejected: dumping raw data into the prompt (too big, leaks PII), RAG mid-call (Sarvam cannot reach a
   laptop, and it adds latency), and LLM-written files (inconsistent, can invent facts).
4. **SDK discovery.** Inspecting the installed SDK showed `InteractionType.CHAT` and `send_text()`, so one hosted
   agent can serve both voice and chat.
5. **Built the pipeline.** Ingest of 226k events takes ~2 s; a rebuild takes 1–7 ms per GLID; an event reaches the
   file in ~3 ms.
6. **Hosted agent via MCP.** Created "Yaad IndiaMART Memory Assistant" with input variables (`context`, `role`,
   `channel`, `opening`, `language`), post-call output variables (`next_step`, `callback_time`, `product`,
   `outcome`) and a Hindi conversational voice. Learned: the template uses voice config v2 (voice by
   `internal_id`); the prompt follows the platform skeleton; the variable syntax is `{{name}}`.
7. **First test over chat:** the agent opened from memory, recalled "call after Navratri", refused to share a
   buyer's number and redirected to the open thread, then confirmed the next step.
8. **Tests** for cold start, schema and budget, the firewall, redaction, and write-back creating a resumable promise.

9. **Wiring the keys taught us the Sarvam platform layout.** The Voice Agents key (apps.sarvam.ai, `X-API-Key`)
   and the model API key (api.sarvam.ai) are different keys. An uncommitted agent is reachable only with its version
   pinned (otherwise 404 "App not found for the interaction type"). Our workspace only allows voice (`v2v`) agents,
   so chat runs on `sarvam-105b-conversations` with the same prompt and memory.
10. **LLM gotchas found by testing, not by reading docs.** With reasoning on, `sarvam-105b` used the whole token budget
    thinking and returned empty content, so extraction runs with reasoning off (~650–900 ms). About 1 in 3 chat replies
    came back empty; we retry. The model dropped quantities ("500 piece"), so a regex backstop fills `qty` from the
    customer's own words.
11. **Resume quality loop** (synthetic buyer, chat to chat): v1 re-confirmed facts and *claimed* it had already found
    sellers. Fixes: a "Confirmed <date> via <channel>: …" line in Known, and a data-driven guardrail ("the PROMISED
    step is still pending, never claim it is done") written into the file itself. A rule hidden in the prompt was not
    enough; a line in the memory was. Final run: the opening resumes from the last channel, there are zero re-asks,
    and the status is honest. Conversation end to updated file: ~0.8 s (LLM extraction); rebuild alone: ~3 ms.

12. **Phone calls via the Sarvam MCP** (`place_test_call`): the agent dialled a teammate's verified phone. Learned by
    testing: test-call variables are ignored, so the call runs on the agent's defaults, and we set the GLID's memory as the
    default before dialling. One Sarvam caller line passed no user audio. Silence nudges keep the call open. The
    transcript is read from analytics and written back with `ingest_transcript`, with Sarvam's own post-call
    variables as a fallback summary.
13. **Manual testing kept finding bugs one at a time** (typed city dropped, a new enquiry not leading the opening,
    empty replies, masculine verbs, contradicting quantities). So we built `python -m yaad check`: 59 automatic checks
    (real files: structure only; synthetic scenarios: a scripted customer against the real chat bot in a scratch DB).
    Running it 3 times exposed flaky cases (a lost quantity on the fallback path, an over-broad "done" detector) that
    a single run hid. Now 59/59, stable across 4 runs.

## Learnings
- Writing the rules down first (thread statuses, lookbacks, budget) made the code small and the demo explainable.
- Claude must not read raw customer text (it counts as a third party), so we developed on labelled synthetic
  personas and checked real outputs with scripts (structure, size, firewall) instead.
