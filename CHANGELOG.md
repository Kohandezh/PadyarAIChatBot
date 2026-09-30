# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The retrieval benchmark (`scripts/run_eval.py`) now fails CI when a metric drops below its measured floor (`data/eval/floors.json`), reports answered queries separately from ranking, and runs five ablation modes (`bm25`, `dense`, `hybrid`, `full`, `full_no_intent`). Results: `docs/features/eval-benchmark/RESULTS.md`.

## [0.1.0] - 2026-09-14

First tagged release: the product as it serves live event installs
today.

### Added

- Tiered answer pipeline: curated exact matches, local BM25 + embeddings
  retrieval with reranking and a per-install intent head, then grounded AI
  selection over record ids — the paid models run only when the local tiers
  are not confident.
- Guide and events knowledge tiers (hours, entrances, transit, halls and
  booth numbers, talks/panels) refreshed from the official event sources,
  with context-based suggestion chips that always offer the next question.
- White-label branding (palette, logo, backgrounds), the per-install
  optional-module system, and the leads pipeline (exhibition lead capture,
  company self-edit on one-time links, bulk confirm campaigns); AI calls
  route through the Padyar AI Control Plane, 11 provider types with models
  set per route.
- CI/CD on GitHub Actions: full test suite, retrieval/safety eval gates,
  and a self-hosted auto-deploy with automatic rollback.

### Fixed

- Free-form visitor questions are understood and matched reliably (no more
  wrong answers or unrelated videos), the synonym list refreshes without
  re-login, dataset saves are dependable under concurrency, and the backup
  button works (the 2026-06-27 update, `docs/CHANGELOG-2026-06-27-fa.md`).
