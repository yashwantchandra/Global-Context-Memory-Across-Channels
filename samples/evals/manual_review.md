# Manual review of the flagged items (run 2026-10-10 13:31, 3 runs × 13 GLIDs)

The headline numbers in `eval_results.md` are the LLM judge's counts, left uncorrected. We read every flagged
item in the transcripts (`data/evals/results.json`, kept local) to see which are real.

## 10 questions flagged as "asks a known fact" (with the file)

| # | GLID | Question (short) | Verdict |
|---|---|---|---|
| 1 | BUYER_2 | "koi specific budget ya quantity?" | Not a re-ask (budget isn't in the file), but asking budget unprompted breaks rule 10 |
| 2 | BUYER_3 | "requirement abhi bhi Bollards ki hi hai?" | Confirmation, not a re-ask (judge error) |
| 3 | BUYER_3 | "aapka budget kya hai?" | Same as #1 |
| 4 | BUYER_4 | "GST number available hai?" | File says "GST not available", so not a re-ask, but an unneeded question |
| 5 | BUYER_CASE_DIPU | "yahi quantity chahiye ya change hua?" | Confirmation (judge error) |
| 6 | SELLER_2 | "koi aur product ya nayi enquiry?" | Open question about something new (judge error) |
| 7 | SELLER_3 | "koi nayi requirement ya update?" | Open question about something new (judge error) |
| 8 | SELLER_5 | "apna naam bata dein" (first time) | Allowed: the contact name isn't known (ask once) |
| 9 | SELLER_5 | "quantity badli hai?" | Confirmation (judge error) |
| 10 | SELLER_5 | "shubh naam jaan sakti hoon?" (second time) | **Real re-ask** (name asked twice) |

**After review:** 1 real re-ask of a known fact in 33 conversations. There were 3 more unneeded questions
(budget ×2, GST ×1) that don't re-ask anything but go against the prompt rules.

## 1 conversation flagged as "claims history not in the file"
BUYER_4: "pichhli baar humne decide kiya tha ki quotation WhatsApp pe bhej doongi". The file has
"Next step agreed: Bot will check suppliers and send the quotation via WhatsApp (voice call, 09 Oct)", so this is
a judge error. After review: **39/39 with no invented facts.**

## What we changed because of the evals (first run → this run)
- The bot asked an unknown seller's name on every turn, holding up the conversation. It is now asked at most once
  and never as a condition (chat rules and hosted agent prompt).
- The bot raised the buyer's other needs on its own ("QR Code Stand bhi chahiye?"). It no longer does.
- Do-Not-Ask said "name" on seller files where only the business name was known. It now says "business name" /
  "contact name".
- First run: 18 re-asks, 2/11 clean conversations. After the fixes: 10 flagged (1 real), 24/33 clean by the judge's count.
