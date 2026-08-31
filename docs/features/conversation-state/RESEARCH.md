# Conversation state — small talk, self-introductions, affirmations, converse mode

Shipped 2026-08-31, branch `feat/conversation-state`. Batch 1 of the
"Padyar 2.0" conversation program.

## The scenario (why this exists)

Two live failures at the Elecomp instance (2026-08-31):

1. A visitor said «سلام چطوری؟ اسم من سینا هست اسم تو چی هست؟» — introducing
   THEMSELF. The named-entity anchor matched the token «سینا» to the company
   «گسترش فناوری‌های پیشرفته و هوشمند سینا» and served its profile.
2. A visitor said «بگو» after the bot had offered something — it was read as
   a brand-new query and the bot re-introduced itself.

Root cause: every tier treated each message as a standalone knowledge
question. Nothing in the pipeline represented "this message is about the
conversation itself."

## What ships

- **`app/services/conversational.py`** — `classify_conversations` (small talk
  phrase set; self-introduction patterns with the remainder rule: intro +
  real question ⇒ not conversational), `is_gibberish` (all tokens unknown AND
  ≤2 tokens AND every token ≤4 chars — conservative so a real short entity
  word can never be dismissed), `store_proposal`/`take_proposal`
  (per-conversation `chat_proposal:{id}` settings rows).
- **`app/routers/chat.py`** — the gates, in pipeline order: gibberish answers
  locally («متوجه منظورت نشدم…», source `local_gibberish`); small talk and
  self-introductions null every local tier and reach Tier 2 with ZERO
  candidates (`allow_empty=True`); affirmative («بگو/بله/آره/بده…») replays
  the stored proposal query or re-serves the offered list; negation
  («نه/ولش کن…») closes politely. The converse decision is served with
  source `ai_converse`.
- **`app/services/answer.py`** — selection mode **`converse`**: the model may
  answer greetings/small talk/self-introductions/meta questions/thanks with a
  1–2 sentence lead under a firewall (assistant identity + HISTORY facts
  only; no record facts, no numbers — digit-bearing leads are degraded to
  `none` and blanked). `proposal: true` when the lead is an explicit
  offer-question. `select_records(..., allow_empty=False)` keeps every other
  zero-candidate call free of a paid round trip.
- **`app/services/conversations.py`** — the summarizer prompt now always
  keeps a stated visitor name as «نام بازدیدکننده: X»; the recall question
  («اسمم چیه؟») is answered by the model from the summary in HISTORY.
- **`app/services/scope.py`** — the cold refusal is now
  «راستش متوجه منظورت نشدم. می‌تونی سؤالت رو یه جور دیگه بپرسی؟».
- **Kill switch**: settings key `chat_conversational_tier` — `"0"` restores
  the pre-gate pipeline entirely, no deploy.
- **Eval**: `data/eval/conversations.json` + `scripts/run_eval.py
  --conversations` — five multi-turn scenarios. Offline baseline after this
  batch: self-intro and gibberish PASS; smalltalk, affirmative-replay and
  name-recall stay RED offline (they need the model — an AI stub for the
  harness is the follow-up; the behavior itself is pinned green in
  `tests/test_conversational.py` with a stubbed provider, 81 tests total).

## Known limits (deliberate, v1)

- Converse can never serve a record list (its firewall forbids record
  facts), so a proposal's «بگو» re-sends the stored QUERY; a subject-aware
  store (e.g. `list:<category>`) is the planned follow-up.
- Proposal rows live in the `settings` table (`chat_proposal:*`); a
  dedicated table is the follow-up if volume ever matters.
- «من دانشجو هستم» classifies as a self-introduction (name «دانشجو») —
  benign: the consequence is deferral to the model, never a wrong record.

## Tests

`tests/test_conversational.py` (gates, kill-switch, affirm replay,
converse e2e, proposal handshake), `tests/test_converse_mode.py` (prompt,
parser, firewall, refusal), `tests/test_summary_name.py` (prompt +
round-trip), `tests/test_run_eval_conversations.py` (harness contract).
