-- 0022_ai_route_reasoning.sql
-- Per-route reasoning effort, owned by the AI Control Plane (Admin -> AI ->
-- Routing) — not Settings -> AI and not an env var. 'default' keeps each
-- provider's own behavior; the engine applies the value to any request that
-- states no explicit preference. Classification stays pinned OFF by the
-- engine regardless (its rule predates this column and bills real money).
ALTER TABLE app.ai_routes ADD COLUMN IF NOT EXISTS reasoning TEXT NOT NULL DEFAULT 'default';
ALTER TABLE app.ai_routes DROP CONSTRAINT IF EXISTS ck_ai_routes_reasoning;
ALTER TABLE app.ai_routes
    ADD CONSTRAINT ck_ai_routes_reasoning
    CHECK (reasoning IN ('default', 'off', 'low', 'medium', 'high'));
