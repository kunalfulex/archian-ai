-- Enrich Google Sheets entity_display_name: "Workbook · Tab"

UPDATE connector_entity_semantics ces
SET entity_display_name = CASE
    WHEN ges.spreadsheet_name IS NOT NULL
         AND TRIM(ges.spreadsheet_name) != ''
         AND LOWER(TRIM(ges.spreadsheet_name)) != LOWER(TRIM(SPLIT_PART(ces.entity_key, '::', 2)))
    THEN TRIM(ges.spreadsheet_name) || ' · ' || TRIM(SPLIT_PART(ces.entity_key, '::', 2))
    ELSE TRIM(SPLIT_PART(ces.entity_key, '::', 2))
END,
updated_at = NOW()
FROM gsheets_extracted_schemas ges
WHERE ces.connector_type = 'google_sheets'
  AND ces.entity_key LIKE '%::%'
  AND ges.user_id = ces.user_id
  AND ges.google_sheets_connection_id::text = ces.connector_instance_id
  AND ges.spreadsheet_id = SPLIT_PART(ces.entity_key, '::', 1)
  AND ges.sheet_name = SPLIT_PART(ces.entity_key, '::', 2)
  AND COALESCE(ges.is_deleted, FALSE) = FALSE;
