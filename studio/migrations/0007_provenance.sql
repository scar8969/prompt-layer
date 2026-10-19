-- Provenance for derived generations, and the index the queue needs to resume.
--
-- A variation, an edit, an extension and a transform are all new jobs. They
-- point at the job they came from rather than replacing it, so the original
-- output and the request that produced it stay exactly as they were.

ALTER TABLE generation_jobs ADD COLUMN parent_job_id TEXT
    REFERENCES generation_jobs (id) ON DELETE SET NULL;

-- What this job is to its parent: variation, inpaint, outpaint, extension,
-- transform. Empty for a job that stands on its own.
ALTER TABLE generation_jobs ADD COLUMN relation TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_jobs_parent ON generation_jobs (parent_job_id);
