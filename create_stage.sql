-- Create stage if it doesn't exist
CREATE STAGE IF NOT EXISTS SPCS_OF.SPCS_SCHEMA.service_specs;

-- List contents to verify
LIST @SPCS_OF.SPCS_SCHEMA.service_specs;
