-- Collaboration-safe canvas editing.
--
-- Two people opening the same canvas is the normal case in a studio, and the
-- dangerous version of it is silent: both edit, the second save wins, and the
-- first person's graph is gone with no record that it ever existed. The fix is
-- the ordinary one -- a monotonic revision, a conditional update, and the 409
-- that tells the loser to reload -- plus the id of the person who last wrote, so
-- the conflict message can name them.
--
-- This is not a live-cursor protocol, and the interface does not claim to be one.
-- It is the part that matters for correctness: no edit is ever lost without the
-- author being told.

ALTER TABLE canvases ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE canvases ADD COLUMN updated_by_user_id TEXT REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX idx_canvases_revision ON canvases (id, revision);
