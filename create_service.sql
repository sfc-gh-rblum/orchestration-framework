-- Create compute pool if it doesn't exist
CREATE COMPUTE POOL IF NOT EXISTS agent_gateway_pool
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = STANDARD_2;

-- Create service
CREATE SERVICE agent_gateway_api
  IN COMPUTE POOL agent_gateway_pool
  FROM SPECIFICATION '/service.yml'
  EXTERNAL_ACCESS_INTEGRATIONS = ()
  MIN_INSTANCES = 1
  MAX_INSTANCES = 1;
