from quart import Quart, request, jsonify, Response
import os

# import asyncio
from dotenv import load_dotenv
from snowflake.snowpark import Session
import snowflake.connector

os.environ["LOGGING_LEVEL"] = "DEBUG"
from agent_gateway import Agent
from agent_gateway.tools import CortexAnalystTool, CortexSearchTool, PythonTool
import requests

# Load environment variables
load_dotenv()

# Initialize Quart app
app = Quart(__name__)


# Enable CORS
@app.after_request
async def after_request(response: Response) -> Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Snowflake-Account, X-Snowflake-Host, X-Snowflake-User, X-Snowflake-Warehouse, X-Snowflake-Database, X-Snowflake-Schema, X-Snowflake-Authorization-Token-Type",
    }
    for key, value in headers.items():
        response.headers[key] = value
    return response


@app.route("/options", methods=["OPTIONS"])
async def handle_options():
    return "", 204


# Snowflake connection parameters
connection_parameters = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT", "demo175.prod1.us-west-2.aws"),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA"),
    "user": os.getenv("SNOWFLAKE_USER"),
    "password": os.getenv("SNOWFLAKE_PASSWORD"),
}

# Initialize Snowflake session
try:
    snowpark = Session.builder.configs(connection_parameters).getOrCreate()
    app.logger.info("Successfully created Snowflake session")
except Exception as e:
    app.logger.warning(f"Could not create Snowflake session: {str(e)}")
    app.logger.warning("Running in local mode without Snowflake connection")
    snowpark = None


# Configure tools
def html_crawl(url):
    response = requests.get(url)
    return response.text


python_crawler_config = {
    "tool_description": "reads the html from a given URL or website",
    "output_description": "html of a webpage",
    "python_func": html_crawl,
}

search_config = {
    "service_name": "SEC_SEARCH_SERVICE",
    "service_topic": "Snowflake's business,product offerings,and performance",
    "data_description": "Snowflake annual reports",
    "retrieval_columns": ["CHUNK", "RELATIVE_PATH"],
    "snowflake_connection": snowpark,
}

analyst_config = {
    "semantic_model": "sp500_semantic_model.yaml",
    "stage": "ANALYST",
    "service_topic": "S&P500 company and stock metrics",
    "data_description": "a table with stock and financial metrics about S&P500 companies",
    "snowflake_connection": snowpark,
}

# Initialize tools
crawler = PythonTool(**python_crawler_config)
tools = [crawler]

# Only add Snowflake tools if connection is available
if snowpark is not None:
    search = CortexSearchTool(**search_config)
    analyst = CortexAnalystTool(**analyst_config)
    tools.extend([search, analyst])

# Initialize agent
agent = Agent(
    snowflake_connection=snowpark,
    tools=tools,
)


@app.route("/api/prompt", methods=["POST"])
async def handle_prompt():
    try:
        data = await request.get_json()
        if not data or "prompt" not in data:
            return {"message": "Invalid data - prompt is required"}, 400

        # Get Snowflake connection parameters from headers
        headers = dict(request.headers)
        app.logger.info(f"Received headers: {headers}")

        # Check if we have any Snowflake headers
        has_snowflake_headers = any(
            key.startswith("X-Snowflake-") for key in headers.keys()
        )

        if has_snowflake_headers:
            # If we have Snowflake headers, validate them
            required_headers = [
                "X-Snowflake-Account",
                "X-Snowflake-Host",
                "Authorization",
                "X-Snowflake-Warehouse",
                "X-Snowflake-Database",
                "X-Snowflake-Schema",
            ]

            missing_headers = [
                header for header in required_headers if not headers.get(header)
            ]
            if missing_headers:
                return {
                    "error": f"Missing required headers: {', '.join(missing_headers)}",
                    "required_headers": required_headers,
                }, 400

            try:
                # Create Snowflake session
                auth_header = headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    return {
                        "error": "Invalid Authorization header format. Must start with 'Bearer '"
                    }, 400

                token = auth_header.split("Bearer ")[1].strip('"')
                session = snowflake.connector.connect(
                    user=headers.get("X-Snowflake-User"),
                    account=headers.get("X-Snowflake-Account").lower(),
                    warehouse=headers.get("X-Snowflake-Warehouse"),
                    database=headers.get("X-Snowflake-Database"),
                    schema=headers.get("X-Snowflake-Schema"),
                    authenticator="oauth",
                    token=token,
                    client_session_keep_alive=True,
                )
                app.logger.info("Successfully created Snowflake session")

                # Execute the query
                cursor = session.cursor()
                cursor.execute("SELECT CURRENT_ACCOUNT()")
                current_account = cursor.fetchone()[0]
                app.logger.info(f"Connected to Snowflake account: {current_account}")

                return jsonify({"message": "Success", "account": current_account})

            except Exception as e:
                app.logger.error(f"Error connecting to Snowflake: {str(e)}")
                return jsonify({"error": str(e)}), 500
        else:
            # For local testing without Snowflake headers
            try:
                # Process the prompt using the agent
                response = await agent.acall(data["prompt"])
                return jsonify(
                    {
                        "message": "Success",
                        "prompt": data["prompt"],
                        "response": response["output"],
                        "sources": response.get("sources"),
                    }
                )
            except Exception as e:
                app.logger.error(f"Error processing prompt: {str(e)}")
                return jsonify({"error": str(e)}), 500

    except Exception as e:
        app.logger.error(f"Error in handle_prompt: {str(e)}")
        app.logger.exception("Full traceback:")
        return {"message": f"Error processing prompt: {str(e)}"}, 500


# Health check endpoint
@app.route("/health")
async def health_check():
    return {"status": "healthy"}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8081))
    app.run(host="0.0.0.0", port=port)
