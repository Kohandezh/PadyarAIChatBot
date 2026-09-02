# elecomp-chat-training

**Status:** shipped 2026-09-01 (live on the elecomp install, /opt/padyar-elecomp)
**Domain:** chat / retrieval / guide tier
**Scope:** ELECOMP ONLY — nothing here touches the inotex install.

## What this is

Train the elecomp chatbot on the data the install itself produces. Three
inputs, three outputs, all deployed on the GPU box (gpu@192.168.100.6,
which is also the production host for `padyar-elecomp`):

1. **chat_logs harvest** — real visitor queries that a local tier already
   served confidently become curated `questions` rows, so Tier 0 (exact),
   Tier 1 (BM25 + embeddings) and the intent head all learn them at the
   next reindex. Guards: `entry_id` names the served record, confidence
   >= 0.70, serving sources only, normalized dedup (a hand mapping always
   wins), junk filter (greetings/ordinals/pager words/short queries).
   First run (2026-09-01, 361 logs): 13 new question rows.

2. **talksiran crawl** — the event platform (talksiran.com) crawled with
   consent of robots.txt: 192 talks/panels/pitches + 169 exhibitor
   profiles. Events land in their OWN table (`app.talks_events`,
   migration 0021 on the server lineage) and are served deterministically
   by a new events section in `app/services/guide.py` (server copy —
   `.server-guide.py` in this repo root is the working copy of that
   file): day-scoped listings («پنل‌های امروز/فردا»), type filters,
   title-keyword lookups («پنل هوش مصنوعی»), and a defer rule so
   specific-but-unmatched questions («پنل GRC کی هست») fall through to
   the model tiers instead of getting an unrelated list.

3. **GPU-trained embeddings — evaluated and REJECTED on the numbers.** All
   2,916 questions (incl. harvested) mapped over 809 entries (48 dataset +
   761 companies) fine-tuned a multilingual sentence-transformer teacher on
   the Tesla P40 (holdout retrieval accuracy@1 = 1.0), then distilled into
   model2vec static models two ways: corpus-only vocabulary and full
   teacher vocabulary. Both measurably LOSE to the shipped
   `potion-multilingual-128M` on the same 120-query holdout:

   | model | hit@1 | hit@3 |
   |-------|-------|-------|
   | potion (shipped baseline) | **0.9333** | **0.9750** |
   | distilled, corpus vocab | 0.8750 | 0.9500 |
   | distilled, teacher vocab | 0.8750 | 0.9500 |

   At this corpus size, distillation transfers the domain but loses more
   generalization than it gains. Decision: **the embedding model stays
   potion**; a confident worse retriever is a regression, not a feature.
   The training pipeline itself (finetune/distill/eval scripts, teacher
   and static model dirs, `eval_report*.json`) lives on the GPU box only
   (`/home/gpu/train-work`) — it produced a rejected candidate, so it is
   deliberately NOT in this repo: an unwired pipeline is dead code. If a
   future retry with more data wins, the scripts come back WITH their
   deployment wiring. The swap itself would only be `ai_embedding_model`
   + `EMBEDDING_COSINE_FLOOR/SPAN`.

## Where things live

| Artifact | Location |
|----------|----------|
| Crawler (`talksiran.com`) | `scripts/train/talksiran_crawl.py` |
| chat_logs harvester | `scripts/train/harvest_chat_questions.py` |
| Pairs/corpus/holdout exporter | `scripts/train/export_training_data.py` |
| Events table migration | `scripts/train/0021_talks_events.sql` (applied as the server lineage's `migrations/0021_talks_events.sql`) |
| Events importer | `scripts/train/import_talks_events.py` |
| Exhibitor merge into `companies` | `scripts/train/merge_talksiran_exhibitors.py` |
| Guide events tier (deployed copy) | `app/services/guide.py` ON THE SERVER (working copy: `scripts/train/server-guide-events-tier.py`) |
| GPU training pipeline (rejected candidate) | server only: `/home/gpu/train-work/scripts/` — not shipped |

On the server: working dir `/home/gpu/train-work` (scripts, data,
talksiran.json, dumps), venvs `/home/gpu/train-venv` (ML stack) and
`/home/gpu/crawl-venv` (httpx + bs4). Backups taken before any write:
`/home/gpu/train-work/padyar_{elecomp,inotex}_pre_train_20260901_*.dump`
plus `app/services/guide.py.bak-20260901`.

## Runbook (refresh cycle)

```bash
# on the GPU box, as gpu
cd /home/gpu/train-work
crawl-venv/bin/python scripts/talksiran_crawl.py --out talksiran      # re-crawl
train-venv/bin/python scripts/eval_static.py ...                      # after retrain

# as root, against the install
cd /opt/padyar-elecomp && set -a && . .env && set +a
SEED_DEFAULT_CONTENT=false .venv/bin/python /home/gpu/train-work/scripts/harvest_chat_questions.py --apply
SEED_DEFAULT_CONTENT=false .venv/bin/python /home/gpu/train-work/scripts/import_talks_events.py --input /home/gpu/train-work/talksiran/talksiran.json --apply
systemctl restart padyar-elecomp                                       # reindex + intent retrain
```

## Measured results

- Events tier (live, 2026-09-01): correct day listings, type filters,
  title lookups, defer behavior verified against production data.
- chat_logs harvest: 13 new question rows; after restart the boot logs
  show `[embeddings] indexed 2916 texts` and
  `[intent] trained on 2916 questions / 748 intents` — the harvested
  queries are in every local tier.
- Embedding model: eval numbers above — baseline retained.

## Open items

- The guide/events lineage lives only on the server copy; folding it
  back into this repo's main is a separate cleanup.
- 20 of 189 exhibitors had no parseable detail page on the first crawl.

## Exhibitor merge (2026-09-01, applied)

`merge_talksiran_exhibitors.py` merged the 169 talksiran exhibitors into
`companies` (workbook stays the source of truth — only EMPTY columns are
filled, never overwritten, with a provenance note):

- 117 matched companies filled (mostly CEO as contact_name + hall)
- 3 genuinely new companies inserted (`ts-*` ids, source `talksiran`)
  with the standard 4 Persian anchors each (12 anchor rows; retrieval
  without anchors was a measured failure mode)
- 4 near-duplicate inserts prevented by the token-overlap matcher
  (empty-token short names like «شرکت زر پی» are excluded — an empty set
  matches everything)
- After restart: `[embeddings] indexed 2928 texts`,
  `[intent] trained on 2928 questions / 751 intents`
