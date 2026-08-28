# Grounded selection — several options, then a pick

**Status:** Implemented · **Domain:** chat · **Created:** 2026-08-28

## The problem, in the product owner's words

> "We have ~200 companies in AI. The bot always retrieves the FIRST option.
> Instead it should give several options as a numbered list and then ask which
> one the visitor wants to know more about. Second problem: the chatbot has no
> context — it needs at least the last 5 messages. And if in 2 days we deploy
> for a different category, we should not have to sit and hand-code everything
> again."

Three problems, one root cause. Retrieval returns exactly ONE document and the
pipeline serves that row verbatim, so a question that maps to 169 rows was
structurally unanswerable. The last box of the standard RAG diagram
(`[Query] -> [Vector search] -> [Top chunks] -> [LLM] -> [Response]`) was
missing: Tier 2 was an LLM guess from a title list, or a 503.

## What was built

### 1. The model became a CHOOSER, never an AUTHOR

`app/services/answer.select_records()` shows the model up to `ANSWER_TOPK`
retrieved records plus the last few turns and gets back ONE JSON object:

```json
{"mode": "answer" | "options" | "none", "ids": ["co-042"], "lead": "", "reason": ""}
```

Everything the visitor then reads is re-read from the database by
`answer.render_options()` / `_answer_from_entry()`. The model picks WHICH
record and WHICH shape; it writes none of the answer.

### 2. Three AI-free tiers around it

| Tier | Where | Network calls |
|------|-------|---------------|
| Pick (`local_pick`) | before retrieval | none |
| List rendering (`local_company_search`) | unchanged position | none |
| Selection (`ai_selected` / `ai_options`) | Tier 2 position | one |

The pick tier resolves a bare number, one ordinal word, or an exact offered
title against the ids stored on the previous turn. It lands in the unchanged
`_answer_from_entry`, so the company's booth clip plays — and it works with the
AI provider switched off.

### 3. Conversation memory

Three columns on `chat_logs` (`conversation_id`, `entry_id`, `offer_state`)
plus two indexes, keyed by the `padyar_conv` cookie the app already set and
never used. See `migrations/0009_conversation_memory.sql`.

## Grounding: seven mechanisms, all of them code that runs AFTER the model spoke

1. **No authoring role for an in-domain question.** The model receives a closed
   list and returns ids.
2. **Id intersection in Python.** An invented id is dropped before it is used;
   nothing surviving in mode `answer` discards the whole decision.
3. **Every fact-bearing string is re-read from the database at assembly time.**
4. **The lead firewall** (`answer.frame_is_grounded`): length, any digit in any
   script, contact shapes, a vocabulary subset check, and a name ban. Check D
   is the one that catches most — a digit filter alone passes «هفت شرکت» (a
   spelled-out count), «ورود رایگان است» (a price with no digit) and «شرکت
   آلفا بهترین گزینه است» (a ranking claim over a real exhibitor). The rule
   the last two checks enforce: **the lead introduces the list, the list says
   the names.** The numbered option lines are not a vocabulary source and the
   names on them are banned outright, because a bag-of-words subset test
   cannot judge a claim BETWEEN two names — it accepted «شرکت آلفا شرکت بتا را
   دارد», one real exhibitor said to own another, out of tokens that were all
   individually grounded.
5. **The free-prose verifier** (`answer.generated_prose_is_grounded`) over
   `get_openai_response`, which until now returned provider content straight
   into `ChatResponse.text` with zero checks. Numbers and links are matched as
   WHOLE tokens against the recorded facts, never as substrings: with the
   shipped defaults the joined source string contains every digit except 7 and
   9, so a substring test passed «سالن ۳ در ضلع شمالی است» and the fake link
   «otex.com». The visitor's own message is DELIBERATELY excluded from the
   source set, or a leading question would launder its own fabrication back at
   the visitor.
6. **Withheld personal data is never loaded.** The candidate payload uses
   `company_profiles.public_profile()`, whose SELECT names only allowlisted
   columns. `answer.py` never imports the admin-only `SELECT *` reader, and a
   test asserts that against the module source.
7. **The list headline says what the records ARE** (`answer._headline_noun`).
   The `ai_options` branch ranks over the whole corpus — exhibitor rows AND FAQ
   rows — so three FAQ records were headed «۳ شرکت:». A list is called by the
   collection noun only when every record in it has a `company_profiles` row;
   a mixed list is «مورد» / "items".

**What this does not promise:** the model can still pick the WRONG id out of
the allowlist. That is bounded upstream by the named-entity anchor and the
unknown-entity gate, both of which still run first.

## Deploying for a different category (the two-day promise)

Day 1 is content: `dataset`, `questions`, `synonyms`, `company_profiles`, the
clips under `media/videos/`, and the settings below. Day 2 is
`scripts/run_eval.py` against a golden set the customer writes, closing gaps
with curated questions and synonym rows in the admin panel. Both days are data.

Settings, all in Admin -> AI, no deploy:

| Key | What it is |
|-----|------------|
| `assistant_domain` / `assistant_domain_en` | what the assistant is about |
| `refusal_text_fa` / `refusal_text_en` | what it says when a question is not (password-gated) |
| `collection_noun_fa` / `collection_noun_en` | «شرکت» / "companies" in lists — used only when every listed record has a `company_profiles` row |
| `options_shown` | names per numbered list (1..15) — the kill switch |
| `chat_log_retention_days` | 0 = keep forever |

The one-time code edit that made this possible: `app/services/openai.py`'s four
fixed prompt sections now carry `{domain}` / `{domain_en}` and the refusal
sentences come from `app/services/scope.py`, instead of hardcoded
"INOTEX" / «اینوتکس».

**What stays INOTEX-shaped, and why that is acceptable:** the deterministic
company-list tier's vocabulary in `app/services/company_search.py` is Persian
exhibition language. A hospital's departments will not trigger it. Customer #2
still works on day one, because `mode: "options"` answers list questions for any
category at the cost of one LLM call. Moving that vocabulary to a hot-reloaded
data file is a separate, test-first follow-up.

## Operating it

- **Is JSON mode alive?** Admin -> AI -> "آزمون JSON" next to the provider Test
  button. It can be silently dead: some adapters drop the request field, the
  model answers in prose with HTTP 200, and the selection tier is permanently
  off with no error anywhere.
- **Which tier answered?** The dashboard's "پاسخ‌ها بر اساس روش" table, 24h.
  Watch `ai_options` in the first hour of an opening: a bot that asks
  "which one?" about questions it could have answered is the failure a visitor
  minds most, and `OPTIONS_MARGIN` is the dial.
- **Why did it choose that?** `conversation.selection.decided` in the log
  explorer (retrieval category) carries the candidate ids with their scores,
  the returned mode, the surviving ids and the model's own reason.
- **Regression net:** `scripts/smoke_options.py` against a running install.
  `scripts/run_eval.py` never calls the AI, so nothing else covers this tier.

## Measurement

`scripts/run_eval.py --recall-k` (2026-08-28, embedding + rerank, 60 golden
queries): recall@1 = 0.786, @3 = 0.857, @5 = 0.929, @8 = 0.952, @13 = 0.952.
Flat after 8, so `ANSWER_TOPK = 8`. See ADR-018.
