-- Rendered preset previews.
--
-- A preview is not a new kind of object: it is an ordinary generation job that
-- remembers which catalogue preset it was rendered for. The newest completed job
-- carrying a preset key becomes that preset's tile image, and the interface can
-- label it "your render" with the model and the date it was produced. No preview
-- exists until a workspace spends its own key to make one, which is the honest
-- answer for a BYO-key product.
--
-- The column is nullable: every job queued before this migration, and every job
-- queued from the studios rather than from a preset, has no preset key.

ALTER TABLE generation_jobs ADD COLUMN preset_key TEXT;

CREATE INDEX idx_jobs_preset ON generation_jobs (workspace_id, preset_key, created_at DESC);
