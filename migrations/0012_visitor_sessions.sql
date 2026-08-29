-- Server-issued sessions for a registered chat visitor.
--
-- WHY THIS TABLE EXISTS
-- ---------------------
-- Until now a registered visitor proved who they were by putting data in the
-- request: `ChatRequest.visitor` carried their job/position/interests in the
-- POST /chat body, and `challenge_id` carried their identity in the body of
-- POST /api/auth/profile and POST /api/visit-plan. Both are self-asserted.
-- Anyone who could type a request could claim to be anyone, read back that
-- person's name and masked phone, and rewrite their profile.
--
-- Identity now comes from a credential the SERVER minted: a random token in
-- an HttpOnly cookie, resolved to a row here by one middleware that runs on
-- every request (app/auth/visitor.py, app/main.py resolve_visitor). The
-- middleware reads the cookie and nothing else — no header, no body field, no
-- query string, no path segment — so there is no second way to be somebody.
--
-- WHY A TABLE AND NOT A STATELESS SIGNED TOKEN
-- -------------------------------------------
-- An HMAC token needs no storage and cannot be forged, which is why chat
-- tokens are built that way (app/auth/security.py). It is the wrong shape
-- here for one reason: A SESSION MUST BE REVOCABLE. A signed token is valid
-- until it expires and the server has no say. This session lasts 30 days on a
-- phone that gets lost in an exhibition hall, and it is the thing that lets
-- someone read and rewrite their own registration. "Log me out", "log me out
-- everywhere" and an operator killing a session all have to mean something
-- the same second they are asked for. A row can be deleted; a signature
-- cannot be un-signed.
--
-- The same argument already produced app.admin_sessions in
-- migrations/0001_initial.sql, and this table is deliberately its twin: token
-- primary key, an expiry that slides on activity, and a lazy delete when a
-- request arrives past it. The one addition is `last_seen`, so an operator
-- looking at the row can tell a live session from a dead one before the
-- expiry catches up.
--
-- WHICH "VISITOR" THIS IS
-- -----------------------
-- `visitor_id` references app.visitors — a member of the public who
-- registered in the chat (migrations/0010_conversations.sql). It is NOT
-- app.lead_visitors, which is booth STAFF holding a personal capture link
-- (migrations/0005_leads.sql). Two tables, two different people, one
-- unfortunate English word. The foreign key here is real, and unlike
-- conversations.visitor_id there is no '' case: a session belonging to nobody
-- has no meaning. ON DELETE CASCADE means deleting a visitor logs that person
-- out of every browser for free.
--
-- WHY NO `revoked` BOOLEAN
-- -----------------------
-- Revocation is DELETE, exactly as admin_sessions does it in all five of its
-- revoke paths. A flag would need sweeping anyway, and
-- tests/test_sql_boolean_portability.py keys its checks on the bare column
-- name across every table, so a new BOOLEAN `revoked` would make every
-- unrelated `revoked = 0` in app/ look like a bug.
--
-- INDEXES
-- -------
-- (visitor_id) makes "log this person out everywhere" and "cascade on visitor
-- delete" a lookup instead of a scan. (expiry) is for the sweep. Both mirror
-- ix_admin_sessions_*.
--
-- WHAT THIS DESTROYS: nothing. One new table, no existing table touched, no
-- existing row changed. There is no downgrade path — rolling back means
-- restoring a backup (app/services/pg_backup.py). An applied migration is
-- history; history gets appended to, never edited.
--
-- Take a backup before running this.

BEGIN;

CREATE TABLE IF NOT EXISTS app.visitor_sessions (
    -- secrets.token_urlsafe(32). The whole credential; never derived from
    -- anything the client sends.
    token       TEXT PRIMARY KEY,
    visitor_id  TEXT NOT NULL REFERENCES app.visitors(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Slides on every request that uses the session, like admin_sessions.
    expiry      TIMESTAMPTZ NOT NULL,
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_visitor_sessions_visitor
    ON app.visitor_sessions(visitor_id);
CREATE INDEX IF NOT EXISTS ix_visitor_sessions_expiry
    ON app.visitor_sessions(expiry);

COMMIT;
