-- Source-side metadata (semantic profile, source object api name, etc.)
ALTER TABLE extraction_results
ADD COLUMN IF NOT EXISTS extraction_metadata JSONB DEFAULT '{}'::jsonb;
