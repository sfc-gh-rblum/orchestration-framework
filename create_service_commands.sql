-- Create compute pool if it doesn't exist
CREATE COMPUTE POOL IF NOT EXISTS agent_gateway_pool
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = STANDARD_2;

-- Create stage for service specs
CREATE STAGE IF NOT EXISTS SPCS_OF.SPCS_SCHEMA.service_specs;

-- Upload spec file to stage
PUT file://service.yml @SPCS_OF.SPCS_SCHEMA.service_specs OVERWRITE=TRUE AUTO_COMPRESS=FALSE;

-- Create service
CREATE SERVICE agent_gateway_api
  IN COMPUTE POOL agent_gateway_pool
  FROM @SPCS_OF.SPCS_SCHEMA.service_specs/service.yml;
