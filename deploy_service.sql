-- Create the service
CREATE SERVICE agent_gatewayof_api
  IN COMPUTE POOL agent_gateway_pool
  EXTERNAL_ACCESS_INTEGRATIONS = (snowflake_acccess_integration)
  FROM SPECIFICATION $$
spec:
  containers:
    - name: flask-of-api
      image: sfsenorthamerica-demo175.registry.snowflakecomputing.com/spcs_of/spcs_schema/agent_gateway_repo/flaskof-api:latest
      env:
        SNOWFLAKE_ACCOUNT: "SFSENORTHAMERICA-DEMO175"
        SNOWFLAKE_DATABASE: "SPCS_OF"
        SNOWFLAKE_SCHEMA: "SPCS_SCHEMA"
        SNOWFLAKE_WAREHOUSE: "DEMO_WH"
        SNOWFLAKE_ROLE: "ACCOUNTADMIN"
        SNOWFLAKE_HOST: "sfsenorthamerica-demo175.snowflakecomputing.com"
        PORT: "8080"
      secrets:
        - snowflakeSecret:
            objectName: "agent_secret"
          envVarName: "SNOWFLAKE_USER"
          secretKeyRef: "username"
        - snowflakeSecret:
            objectName: "agent_secret"
          envVarName: "SNOWFLAKE_PASSWORD"
          secretKeyRef: "password"
  endpoints:
    - name: flask-api
      port: 8080
      public: true
      protocol: HTTP
  networkPolicyConfig:
    allowInternetEgress: true
  logExporters:
    eventTableConfig:
      logLevel: INFO
  platformMonitor:
    metricConfig:
      groups:
        - system
        - network
        - status
$$;

-- Verify the service status
SHOW SERVICES LIKE 'agent_gatewayof_api';

-- Get the service endpoint URL
DESC SERVICE agent_gatewayof_api;
