-- Re-key Salesforce labeling rows from org_id to salesforce_tokens.id (token UUID).
-- Safe to run multiple times.

UPDATE connector_entity_semantics ces
SET connector_instance_id = tok.connector_id,
    connector_ref = jsonb_build_object(
        'connector_id', tok.connector_id,
        'org_id', tok.org_id
    )
FROM (
    SELECT DISTINCT ON (user_id, org_id)
        user_id,
        id::text AS connector_id,
        org_id::text AS org_id
    FROM salesforce_tokens
    ORDER BY user_id, org_id, issued_at DESC
) tok
WHERE ces.connector_type = 'salesforce'
  AND ces.user_id = tok.user_id
  AND ces.connector_instance_id = tok.org_id
  AND ces.connector_instance_id <> tok.connector_id;

UPDATE connector_labeling_jobs j
SET connector_instance_id = tok.connector_id,
    connector_ref = jsonb_build_object(
        'connector_id', tok.connector_id,
        'org_id', tok.org_id
    )
FROM (
    SELECT DISTINCT ON (user_id, org_id)
        user_id,
        id::text AS connector_id,
        org_id::text AS org_id
    FROM salesforce_tokens
    ORDER BY user_id, org_id, issued_at DESC
) tok
WHERE j.connector_type = 'salesforce'
  AND j.user_id = tok.user_id
  AND j.connector_instance_id = tok.org_id
  AND j.connector_instance_id <> tok.connector_id;

UPDATE connector_business_profiles p
SET connector_instance_id = tok.connector_id,
    connector_ref = jsonb_build_object(
        'connector_id', tok.connector_id,
        'org_id', tok.org_id
    )
FROM (
    SELECT DISTINCT ON (user_id, org_id)
        user_id,
        id::text AS connector_id,
        org_id::text AS org_id
    FROM salesforce_tokens
    ORDER BY user_id, org_id, issued_at DESC
) tok
WHERE p.connector_type = 'salesforce'
  AND p.user_id = tok.user_id
  AND p.connector_instance_id = tok.org_id
  AND p.connector_instance_id <> tok.connector_id;
