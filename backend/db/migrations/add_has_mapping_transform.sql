-- Distinguish mapping-stage transforms (split/combine before migrate) from
-- post-extract extraction_rules transforms.
ALTER TABLE extraction_results
ADD COLUMN IF NOT EXISTS has_mapping_transform BOOLEAN DEFAULT FALSE;
