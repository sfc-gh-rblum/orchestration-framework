-- Create image repository
CREATE IMAGE REPOSITORY IF NOT EXISTS spcs_of.spcs_schema.agent_gateway_repo;

-- Get repository URL and credentials
DESC IMAGE REPOSITORY spcs_of.spcs_schema.agent_gateway_repo;
