-- Create an image repository
CREATE IMAGE REPOSITORY IF NOT EXISTS agent_gateway_repo;

-- Get the credentials for the registry
SHOW IMAGE REPOSITORIES;
DESC IMAGE REPOSITORY agent_gateway_repo;
