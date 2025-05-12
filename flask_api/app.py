from quart import Quart, request
from flask_cors import CORS
import os

# import asyncio
from dotenv import load_dotenv
from snowflake.snowpark import Session
from agent_gateway import Agent
from agent_gateway.tools import CortexAnalystTool, CortexSearchTool, PythonTool
import requests

# Load environment variables
load_dotenv()

# Initialize Quart app
app = Quart(__name__)
CORS(app)

# Snowflake connection parameters
connection_parameters = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA"),
}

# Add user/password if not using OAuth
if not os.getenv("SNOWFLAKE_HOST"):
    connection_parameters.update(
        {
            "user": os.getenv("SNOWFLAKE_USER"),
            "password": os.getenv("SNOWFLAKE_PASSWORD"),
        }
    )
else:
    connection_parameters.update(
        {
            "host": os.getenv("SNOWFLAKE_HOST"),
            "authenticator": "oauth",
        }
    )
    with open("/snowflake/session/token") as token_file:
        connection_parameters["token"] = token_file.read()

# Initialize Snowflake session
snowpark = Session.builder.configs(connection_parameters).getOrCreate()


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
search = CortexSearchTool(**search_config)
analyst = CortexAnalystTool(**analyst_config)

snowflake_tools = [search, analyst, crawler]

# Initialize agent
agent = Agent(
    snowflake_connection=snowpark,
    tools=snowflake_tools,
)


@app.route("/api/prompt", methods=["POST"])
async def handle_prompt():
    data = await request.get_json()
    if not data or "prompt" not in data:
        return {"message": "Invalid data - prompt is required"}, 400

    prompt = data["prompt"]
    try:
        # Use the agent to process the prompt
        response = await agent.acall(prompt)
        return response, 200
    except Exception as e:
        return {"message": f"Error processing prompt: {str(e)}"}, 500


# Health check endpoint
@app.route("/health")
async def health_check():
    return {"status": "healthy"}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
