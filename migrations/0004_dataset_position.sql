-- Give `dataset` an explicit display order.
--
-- WHY
-- ---
-- `app/routers/public.py` read the knowledge base with `ORDER BY rowid` —
-- SQLite's implicit insertion counter. PostgreSQL has no such column, so
-- after the migration `/api/dataset` returned:
--
--     psycopg.errors.UndefinedColumn: column "rowid" does not exist
--
-- That is a hard 500 on the endpoint the public chat UI uses to load its
-- knowledge base. It was the last SQLite-ism left in the application.
--
-- WHY NOT JUST `ORDER BY id`
-- --------------------------
-- Because `id` is TEXT, so that sorts alphabetically and would silently
-- reshuffle what visitors see: `inotex-app` and `inotex-booth` would jump
-- ahead of `inotex-overview`. The existing order is curated, not incidental —
-- overview, then date, venue, hours, booth, programs, and so on. Losing it
-- quietly is a worse bug than the 500, because nobody would notice it was a
-- regression.
--
-- So the order is preserved explicitly below, taken from the rowid sequence
-- in the pre-migration SQLite database (`chat_history.db`), which is the only
-- surviving record of it.
--
-- Positions are spaced by 10 so an operator can later insert an entry between
-- two others without renumbering the whole table.

ALTER TABLE dataset ADD COLUMN IF NOT EXISTS position INTEGER;

-- The curated INOTEX order, exactly as it was served before the migration.
UPDATE dataset SET position = v.pos
FROM (VALUES
    ('inotex-overview',        10),
    ('inotex-date',            20),
    ('inotex-venue',           30),
    ('inotex-hours',           40),
    ('inotex-booth',           50),
    ('inotex-programs',        60),
    ('inotex-pitch',           70),
    ('inotex-contact',         80),
    ('inotex-exhibitors',      90),
    ('inotex-visitors',       100),
    ('inotex-stats',          110),
    ('inotex-app',            120),
    ('inotex-volunteer',      130),
    ('inotex-organizers',     140),
    ('inotex-targeted-visit', 150),
    ('inotex-news',           160)
) AS v(id, pos)
WHERE dataset.id = v.id;

-- Anything this install has beyond the seeded INOTEX set (customer-added
-- entries, or a different deployment entirely) gets a deterministic position
-- after them, ordered by id. Deterministic matters more than clever here:
-- two installs running this migration must end up with the same order.
WITH numbered AS (
    SELECT id, row_number() OVER (ORDER BY id) AS n
      FROM dataset
     WHERE position IS NULL
)
UPDATE dataset SET position = 1000 + (numbered.n * 10)
  FROM numbered
 WHERE dataset.id = numbered.id;

-- Serves `ORDER BY position` on the public read path.
CREATE INDEX IF NOT EXISTS ix_dataset_position ON dataset (position);
