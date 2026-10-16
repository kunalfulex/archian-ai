-- SOQL relationship traversal uses relationshipName (e.g. Account, Foo__r), not lookup field API names.
-- Store it for validating SELECT paths like Account.Name against the related object's fields.

ALTER TABLE salesforce_object_fields
ADD COLUMN IF NOT EXISTS relationship_name TEXT;

COMMENT ON COLUMN salesforce_object_fields.relationship_name IS 'Salesforce field describe relationshipName; used in SOQL (e.g. Account.Name from Contact).';
