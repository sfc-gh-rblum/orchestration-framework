import os
import structlog
from flask import Flask, jsonify, request
from agent_gateway import Agent
from agent_gateway.tools import CortexSearchTool, CortexAnalystTool, SQLTool, PythonTool
from snowflake.snowpark import Session
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()
print("🔧 Starting application initialization...")

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
print("📝 Logging configured")

# Environment variables will be automatically populated by Snowflake in SPCS
SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")
SNOWFLAKE_HOST = os.getenv("SNOWFLAKE_HOST")
SNOWFLAKE_DATABASE = os.getenv("SNOWFLAKE_DATABASE")
SNOWFLAKE_SCHEMA = os.getenv("SNOWFLAKE_SCHEMA")

# Custom environment variables for local testing only
SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_PASSWORD")
SNOWFLAKE_ROLE = os.getenv("SNOWFLAKE_ROLE")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE")

# Print current environment details
print("Account    : {}".format(SNOWFLAKE_ACCOUNT))
print("User       : {}".format(SNOWFLAKE_USER))
print("Host       : {}".format(SNOWFLAKE_HOST))
print("Database   : {}".format(SNOWFLAKE_DATABASE))
print("Schema     : {}".format(SNOWFLAKE_SCHEMA))
print("Warehouse  : {}".format(SNOWFLAKE_WAREHOUSE))
print("Directory  : {}".format(os.getcwd()))


def get_login_token():
    """
    Read the login token supplied automatically by Snowflake in SPCS.
    These tokens are short lived and should always be read right before creating any new connection.
    """
    try:
        with open("/snowflake/session/token", "r") as f:
            return f.read()
    except FileNotFoundError:
        return None


def get_connection_params():
    """
    Construct Snowflake connection params from environment variables.
    Uses OAuth token in SPCS, falls back to username/password for local development.
    """
    token = get_login_token()
    if token:
        print("🔑 Using OAuth token authentication")
        return {
            "account": SNOWFLAKE_ACCOUNT,
            "host": SNOWFLAKE_HOST,
            "authenticator": "oauth",
            "token": token,
            "warehouse": SNOWFLAKE_WAREHOUSE,
            "database": SNOWFLAKE_DATABASE,
            "schema": SNOWFLAKE_SCHEMA,
            "role": SNOWFLAKE_ROLE,  # Add role parameter for OAuth
            "insecure_mode": True,
        }
    else:
        print("👤 Using username/password authentication")
        return {
            "account": SNOWFLAKE_ACCOUNT,
            "host": SNOWFLAKE_HOST,
            "user": SNOWFLAKE_USER,
            "password": SNOWFLAKE_PASSWORD,
            "role": SNOWFLAKE_ROLE,
            "warehouse": SNOWFLAKE_WAREHOUSE,
            "database": SNOWFLAKE_DATABASE,
            "schema": SNOWFLAKE_SCHEMA,
        }


def create_snowflake_session():
    """Create and verify Snowflake session."""
    try:
        print("❄️  Initializing Snowflake connection...")

        # Create Snowflake session
        session = Session.builder.configs(get_connection_params()).create()
        session.sql_simplifier_enabled = True

        # Verify connection and get version info
        snowflake_environment = session.sql(
            "select current_user(), current_version()"
        ).collect()
        print("Snowflake version: {}".format(snowflake_environment[0][1]))
        print("Current user: {}".format(snowflake_environment[0][0]))

        # Generate demo services
        from agent_gateway.tools.utils import generate_demo_services

        print("🔄 Generating demo services...")
        generate_demo_services(session)
        print("✅ Demo services generated")

        # Verify and log current session details
        current_session = session.sql(
            "SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()"
        ).collect()
        print("✨ Connected to Snowflake successfully!")
        print(f"   Role: {current_session[0][0]}")
        print(f"   Warehouse: {current_session[0][1]}")
        print(f"   Database: {current_session[0][2]}")
        print(f"   Schema: {current_session[0][3]}")

        return session

    except Exception as e:
        print(f"❌ Snowflake connection failed: {str(e)}")
        logger.error(
            "snowflake_connection_failed", error=str(e), error_type=type(e).__name__
        )
        raise


def initialize_agent_gateway(session):
    """Initialize Agent Gateway with Snowflake-based tools."""
    try:
        print("\n🛠️  Initializing Agent Gateway tools...")

        # Initialize Cortex Search Tool
        print("   📚 Setting up Cortex Search Tool...")
        search_config = {
            "service_name": "SEC_SEARCH_SERVICE",
            "service_topic": "Snowflake's business,product offerings,and performance",
            "data_description": "Snowflake annual reports",
            "retrieval_columns": ["CHUNK", "RELATIVE_PATH"],
            "snowflake_connection": session,
            "k": 10,
        }
        search_tool = CortexSearchTool(**search_config)
        print("   ✅ Cortex Search Tool ready")

        # Initialize Cortex Analyst Tool
        print("   📊 Setting up Cortex Analyst Tool...")
        analyst_config = {
            "semantic_model": "sp500_semantic_model.yaml",
            "stage": "ANALYST",
            "service_topic": "S&P500 company and stock metrics",
            "data_description": "a table with stock and financial metrics about S&P500 companies",
            "snowflake_connection": session,
            "max_results": 5,
        }

        # First ensure we're in the right schema
        session.use_schema("CUBE_TESTING.PUBLIC")
        print("   🔄 Using demo schema: CUBE_TESTING.PUBLIC")

        analyst_tool = CortexAnalystTool(**analyst_config)
        print("   ✅ Cortex Analyst Tool ready")

        # Initialize SQL Tool for margin evaluation
        print("   💾 Setting up SQL Tool...")
        sql_query = """WITH CompanyMetrics AS (
            SELECT
                LONGNAME,
                SECTOR,
                INDUSTRY,
                CURRENTPRICE,
                MARKETCAP,
                EBITDA,
                CASE
                    WHEN MARKETCAP > 0 AND EBITDA IS NOT NULL THEN (EBITDA * 100.0) / MARKETCAP
                    ELSE NULL
                END AS EBITDA_Margin
            FROM CUBE_TESTING.PUBLIC.SP500
        ),
        AverageMetrics AS (
            SELECT
                AVG(EBITDA_Margin) AS Average_EBITDA_Margin
            FROM CompanyMetrics
        ),
        NormalizedMetrics AS (
            SELECT
                cm.LONGNAME,
                cm.SECTOR,
                cm.INDUSTRY,
                cm.CURRENTPRICE,
                cm.MARKETCAP,
                cm.EBITDA,
                cm.EBITDA_Margin,
                CASE
                    WHEN am.Average_EBITDA_Margin > 0 THEN cm.EBITDA_Margin / am.Average_EBITDA_Margin
                    ELSE NULL
                END AS Normalized_EBITDA_Margin
            FROM CompanyMetrics cm
            CROSS JOIN AverageMetrics am
        )
        SELECT
            LONGNAME,
            SECTOR,
            INDUSTRY,
            CURRENTPRICE,
            MARKETCAP,
            EBITDA,
            EBITDA_Margin,
            Normalized_EBITDA_Margin
        FROM NormalizedMetrics;"""

        sql_tool_config = {
            "name": "margin_eval",
            "connection": session,
            "sql_query": sql_query,
            "tool_description": "Calculates the normalized EBITDA Margin as a % relative to the SP500 average",
            "output_description": "EBITDA Margin %",
        }
        sql_tool = SQLTool(**sql_tool_config)
        print("   ✅ SQL Tool ready")

        # Initialize Python Tool for web crawling
        print("   🌐 Setting up Web Crawler Tool...")

        def html_crawl(url):
            response = requests.get(url)
            return response.text

        python_crawler_config = {
            "tool_description": "reads the html from a given URL or website",
            "output_description": "html of a webpage",
            "python_func": html_crawl,
        }
        web_crawler = PythonTool(**python_crawler_config)
        print("   ✅ Web Crawler Tool ready")

        # Initialize Agent with all tools
        print("\n🤖 Initializing Agent with all tools...")
        agent = Agent(
            snowflake_connection=session,
            tools=[search_tool, analyst_tool, sql_tool, web_crawler],
            max_retries=3,
        )
        print("✨ Agent Gateway initialization complete!")
        return agent

    except Exception as e:
        print(f"❌ Agent Gateway initialization failed: {str(e)}")
        logger.error(
            "agent_gateway_initialization_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise


# Initialize Flask app
app = Flask(__name__)
print("\n🌟 Starting Flask application...")

# Initialize Snowflake session and Agent Gateway
try:
    snowflake_session = create_snowflake_session()
    agent = initialize_agent_gateway(snowflake_session)
except Exception as e:
    print(f"❌ Initialization failed: {str(e)}")
    logger.error("initialization_failed", error=str(e), error_type=type(e).__name__)
    snowflake_session = None
    agent = None


@app.route("/health")
def health():
    """Health check endpoint."""
    status = {
        "status": "healthy" if agent and snowflake_session else "unhealthy",
        "snowflake_connected": snowflake_session is not None,
        "agent_initialized": agent is not None,
    }
    return jsonify(status)


@app.route("/api/prompt", methods=["POST"])
def process_prompt():
    """Process prompts through the Agent Gateway."""
    if not agent or not snowflake_session:
        return jsonify(
            {
                "status": "error",
                "message": "Service not properly initialized. Check /health endpoint for details.",
            }
        ), 503

    try:
        data = request.get_json()
        if not data or "prompt" not in data:
            return jsonify({"status": "error", "message": "No prompt provided"}), 400

        prompt = data["prompt"]
        logger.info(
            "processing_prompt", message="Starting prompt processing", prompt=prompt
        )

        # Get a fresh session for this request
        # session = create_snowflake_session()

        # Process prompt through Agent Gateway with detailed logging
        try:
            response = agent(prompt)
            logger.info(
                "prompt_processed",
                message="Successfully processed prompt",
                prompt=prompt,
                response_type=type(response).__name__,
            )
        except Exception as e:
            logger.error(
                "agent_execution_failed",
                error=str(e),
                error_type=type(e).__name__,
                prompt=prompt,
            )
            raise

        return jsonify({"status": "success", "response": response})

    except Exception as e:
        logger.error(
            "prompt_processing_failed",
            error=str(e),
            error_type=type(e).__name__,
            prompt=data.get("prompt") if data else None,
        )
        return jsonify(
            {"status": "error", "message": f"Error processing prompt: {str(e)}"}
        ), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"\n🚀 Starting server on port {port}...")
    print("📡 Server is ready to accept connections!")
    app.run(host="0.0.0.0", port=port, debug=True)
