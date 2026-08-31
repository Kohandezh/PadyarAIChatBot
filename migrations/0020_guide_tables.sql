-- Guide knowledge gets its own tables.
--
-- WHY FIVE TABLES AND NOT `dataset` ROWS
-- --------------------------------------
-- These five entities are STRUCTURED facts with their own query shapes —
-- key-value (hours, dates, weather, address, peak), points of interest with
-- coordinates (entrances, transit stations), and listable collections
-- (restaurants, news). Flattening them into `dataset` rows would lose the
-- types (BOOLEAN in_venue, DOUBLE PRECISION lat/lng, the news date pair) and
-- make deterministic answers impossible: retrieval scores prose, it cannot
-- say "in-venue restaurants first" or "newest news first, capped at five".
-- Separate tables keep the crawl import idempotent (one row per primary
-- key, upserted) and the serving tier in app/services/guide.py type-safe.
--
-- The source is the production `crawl` schema (database padyar_elecomp,
-- crawled 2026-08-31): crawl.guide_facts, crawl.gates, crawl.stations,
-- crawl.restaurants, crawl.news. This migration is the app-owned COPY; the
-- crawl tables stay the crawler's. scripts/import-guide-from-crawl.py moves
-- the rows across; re-running it refreshes them (there is no downgrade to
-- write — the app tables are ours now, and the crawl side is untouched).

CREATE TABLE IF NOT EXISTS app.guide_facts (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app.gates (
    name        TEXT PRIMARY KEY,
    gate_type   TEXT NOT NULL DEFAULT '',
    route_text  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app.stations (
    name         TEXT PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT '',
    line         TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    lat          DOUBLE PRECISION,
    lng          DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS app.restaurants (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    cuisine   TEXT NOT NULL DEFAULT '',
    area      TEXT NOT NULL DEFAULT '',
    distance  TEXT NOT NULL DEFAULT '',
    note      TEXT NOT NULL DEFAULT '',
    links     TEXT NOT NULL DEFAULT '[]',
    in_venue  BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS app.news (
    slug          TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    date_iso      TEXT NOT NULL DEFAULT '',
    date_jalali   TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '',
    featured      BOOLEAN NOT NULL DEFAULT false
);
