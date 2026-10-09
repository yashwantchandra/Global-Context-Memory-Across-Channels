# Global Context: Memory Across Channels – Seller Dataset

**What it's for:** building a `seller.md` for each seller that VANI loads before a call or chat.
**Sample:** 5,000 sellers, randomly chosen from sellers the VANI bot called. Each also has activity on at least one other channel (enquiries, buyer calls, buy-leads or WhatsApp).
**Window:** 1 Apr – 2 Oct 2026, one row per event, so teams can pick their own lookback window.
**Join key:** seller GLID: `fk_glusr_usr_id`, `seller_glid` or `glid`, depending on the file.
**Cleaning:** every column was checked; columns empty for all records were dropped. Not masked: messages and summaries contain real names; internal use only. Phone numbers and emails are excluded.

## Files

| File | One row per | Rows | Sellers covered |
|---|---|---|---|
| `gc_seller_profile.csv` | seller | 5,000 | 5,000 |
| `gc_enquiries_received.csv` | enquiry a buyer sent the seller | 33,006 | 2,749 |
| `gc_enquiry_messages.csv` | message in an enquiry thread (buyer or seller) | 17,633 | 1,468 |
| `gc_buyer_calls_received.csv` | buyer phone call to the seller | 19,238 | 2,063 |
| `gc_buyleads_bought.csv` | buy-lead the seller bought | 14,852 | 254 |
| `gc_whatsapp_messages.csv` | WhatsApp message to / from the seller (delivery and read status) | 130,897 | 3,681 |
| `gc_whatsapp_chatbot_conversations.csv` | WhatsApp chatbot turn (seller's message + bot reply) | 83,280 | 3,002 |
| `gc_buyer_call_extractions.csv` | buyer–seller call the seller was on, with AI-extracted details | 5,417 | 1,388 |
| `gc_bot_calls.csv` | answered VANI bot call | 9,657 | 4,395 |
| `gc_bot_call_turns.csv` | turn of a bot call (who said what, when) | 4,920 | 420 |
| `gc_executive_calls.csv` | IndiaMART executive call to the seller (Aug–Sep) | 2,580 | 271 |

Most sellers appear in 3 or more files.

---

## gc_seller_profile.csv
- **Location:** `seller_city`, `seller_district`, `seller_state`, `seller_pincode`, `seller_locality`
- **Business:** `company_name`, `business_type` (proprietorship / partnership / company), `annual_turnover`, `nature_of_business` (+ `_secondary`), `gst_registration_year`
- **Categories:** `top_category_1/2/3`, `top_parent_category`, `top_category_group`, `num_categories`
- **Account & KYC:** `customer_type`, `is_paid`, `listing_status`, `mobile_verified_flag`, `email_verified_flag`, `gst_verified_flag`, `gst_flag`, `is_pan_number_available`, `business_type_flag`, `visiting_card_flag` (1 = present / verified)
- **Engagement** (`eng_` prefix; 90-day and 1-year): activity in the last 30 days, call attempts, pickup ratio, long and short calls, callbacks, meetings, enquiries received and replied, buyer calls received and answered, not-interested and not-picked counts, product count and edits, catalog quality score (`eng_cqs`)
- **Bot call history:** `bot_attempts`, `bot_answered`, `bot_answer_rate`, outcome counts (`calls_meeting_fixed`, `calls_not_interested`, `calls_general`, `calls_call_later`), `avg_answered_call_sec`, `past_objections`, `past_questions_asked`, `past_dispositions_detailed`, `asked_if_talking_to_bot`, `showed_frustration`, `already_in_touch_with_executive`, `do_not_call_requested`

## gc_enquiries_received.csv
`query_id`, `enquiry_date`, `buyer_glid`, `seller_glid`, `subject`, `message` (the buyer's enquiry text), `mcat_id`, `product_name`, `search_keyword`, `source_module` (app, mobile site, desktop…), `read_status`, `first_read_date` (when the seller first opened it), `buyer_city`, `buyer_state`, `buyer_country`, `buyer_company`, `buyer_designation`, `seller_city`, `seller_company`

## gc_enquiry_messages.csv
The back-and-forth inside each enquiry. `query_id` links to the enquiry. `message_from` = buyer / seller, `sequence`, `sender_glid`, `receiver_glid`, `reply_date`, `subject`, `reply_text`, `read_status`, `first_read_date`, `template_flag`

## gc_buyer_calls_received.csv
`call_id`, `call_datetime`, `end_time`, `duration_sec`, `talk_sec`, `call_status` (Connected / Not connected), `buyer_glid`, `seller_glid`, `caller_circle` (buyer's state), `caller_operator`, `mcat_id`, `transfer_flag`

## gc_buyleads_bought.csv
`purchase_id`, `purchase_date`, `buylead_id`, `seller_glid`, `buyer_glid`, `credits_used`, `purchase_mode`, `purchase_type`, `module`, `list_position`, `keyword`, `mcat_rank`, `is_pref_location`, `distance_city`
**Note:** the warehouse keeps only about 45 days of purchases, so this covers **16 Aug – 1 Oct** only.

## gc_whatsapp_messages.csv
WhatsApp messages on IndiaMART numbers (9696, 8181): `message_id`, `glid`, `entry_date`, `source`, `message_sender` (API = sent by IndiaMART, USER = sent by the seller, Agent), `campaign_name`, `message_status`, `sent_at`, `delivered_at`, `read_at`. This file has delivery status, not message text; the text is in the chatbot file.

## gc_whatsapp_chatbot_conversations.csv
`chat_id`, `glid`, `session_id`, `entry_date`, `source`, `user_message` (what the seller typed), `bot_reply`, `intent`, `intent_confidence`, `action_type`, `message_status`, `feedback`, `campaign_name`, `user_type`, `product_id`, `media_url`

## gc_buyer_call_extractions.csv
AI-extracted details of buyer–seller phone calls (PNS) the seller took part in. `seller_role_on_call` shows whether the seller was the BUYER or SELLER on that call; most are calls these sellers made as buyers. Columns: `file_id`, `glid`, `call_date`, `file_duration_sec`, `is_b2b`, `file_intent`, `languages`, `products_discussed`, `categories`, `prices_quoted`, `specs_discussed`, `stock_status`.
**Note:** outcome / buyer-intent / deal-readiness tags exist in the source but are permission-blocked for this export.

## gc_bot_calls.csv / gc_bot_call_turns.csv / gc_executive_calls.csv
The same structure as in the Persona dataset: answered VANI bot calls with summary, outcome and recording link; turn-by-turn transcripts where available; and executive calls (Aug–Sep, with 7 and 18 Sep missing).

## Not included
- **Buyer side:** left out by request.
- **Starter API list:** has to come from the tech team; these files stand in for the APIs.
