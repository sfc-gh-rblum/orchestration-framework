import os
import structlog
from flask import Flask, jsonify, request
from agent_gateway import Agent
from agent_gateway.tools import CortexSearchTool, CortexAnalystTool, SQLTool, PythonTool
from snowflake.snowpark import Session
from dotenv import load_dotenv
import requests
from agent_gateway.tools.utils import _determine_runtime
import jwt
from functools import wraps

# Load environment variables
load_dotenv()

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


def validate_jwt(request):
    """Validate JWT token from request headers."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return False, "No Authorization header"

    try:
        # Extract token from "Snowflake Token=\"<token>\"" format
        if 'Snowflake Token="' in auth_header:
            token = auth_header.split('Snowflake Token="')[1].rstrip('"')
        else:
            token = auth_header.split("Bearer ")[1]

        # Load public key for verification
        with open("rsa_key.pub", "r") as f:
            public_key = f.read()

        # Verify the token
        jwt.decode(token, public_key, algorithms=["RS256"])
        return True, None
    except Exception as e:
        logger.error("jwt_validation_failed", error=str(e))
        return False, str(e)


def require_jwt(f):
    """Decorator to require JWT authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        is_valid, error = validate_jwt(request)
        if not is_valid:
            return jsonify(
                {"status": "error", "message": f"Authentication failed: {error}"}
            ), 401
        return f(*args, **kwargs)

    return decorated_function


def create_snowflake_session():
    """Create and verify Snowflake session."""
    try:
        # Determine if running in SPCS
        inside_snowflake = _determine_runtime()

        if inside_snowflake:
            # SPCS authentication
            snowflake_config = {
                "host": os.getenv("SNOWFLAKE_HOST"),
                "account": os.getenv("SNOWFLAKE_ACCOUNT"),
                "authenticator": "oauth",
                "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
                "database": os.getenv("SNOWFLAKE_DATABASE"),
                "schema": os.getenv("SNOWFLAKE_SCHEMA"),
            }
            # Read token from SPCS mount
            try:
                with open("/snowflake/session/token", "r") as f:
                    snowflake_config["token"] = f.read().strip()
            except Exception as e:
                logger.error("failed_to_read_token", error=str(e))
                raise
        else:
            # Regular authentication
            snowflake_config = {
                "account": os.getenv("SNOWFLAKE_ACCOUNT"),
                "user": os.getenv("SNOWFLAKE_USER"),
                "password": os.getenv("SNOWFLAKE_PASSWORD"),
                "role": os.getenv("SNOWFLAKE_ROLE"),
                "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
                "database": os.getenv("SNOWFLAKE_DATABASE"),
                "schema": os.getenv("SNOWFLAKE_SCHEMA"),
            }

        # Verify all required credentials are present
        required_configs = ["account", "warehouse", "database", "schema"]
        if not inside_snowflake:
            required_configs.extend(["user", "password", "role"])
        else:
            required_configs.extend(["host", "token"])

        missing_configs = [
            k
            for k in required_configs
            if k not in snowflake_config or not snowflake_config[k]
        ]
        if missing_configs:
            raise ValueError(
                f"Missing Snowflake configurations: {', '.join(missing_configs)}"
            )

        # Log connection attempt details (excluding sensitive info)
        log_config = {
            k: v for k, v in snowflake_config.items() if k not in ["password", "token"]
        }
        logger.info("attempting_snowflake_connection", **log_config)

        # Create Snowflake session
        session = Session.builder.configs(snowflake_config).create()

        # Verify and log current session details
        current_session = session.sql(
            "SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()"
        ).collect()
        logger.info(
            "snowflake_connection_successful",
            role=current_session[0][0],
            warehouse=current_session[0][1],
            database=current_session[0][2],
            schema=current_session[0][3],
        )
        return session

    except Exception as e:
        logger.error(
            "snowflake_connection_failed", error=str(e), error_type=type(e).__name__
        )
        raise


def initialize_agent_gateway(session):
    """Initialize Agent Gateway with Snowflake-based tools."""
    try:
        # Initialize Cortex Search Tool
        search_config = {
            "service_name": "SEC_SEARCH_SERVICE",
            "service_topic": "Snowflake's business,product offerings,and performance",
            "data_description": "Snowflake annual reports",
            "retrieval_columns": ["CHUNK", "RELATIVE_PATH"],
            "snowflake_connection": session,
            "k": 10,
        }
        search_tool = CortexSearchTool(**search_config)
        logger.info("search_tool_initialized")

        # Initialize Cortex Analyst Tool
        analyst_config = {
            "semantic_model": "sp500_semantic_model.yaml",
            "stage": "ANALYST",
            "service_topic": "S&P500 company and stock metrics",
            "data_description": "a table with stock and financial metrics about S&P500 companies",
            "snowflake_connection": session,
            "max_results": 5,
        }

        # First ensure we're in the right schema
        full_schema = (
            f"{os.getenv('SNOWFLAKE_DATABASE')}.{os.getenv('SNOWFLAKE_SCHEMA')}"
        )
        session.use_schema(full_schema)
        logger.info("using_schema", schema=full_schema)

        analyst_tool = CortexAnalystTool(**analyst_config)
        logger.info("analyst_tool_initialized")

        # Initialize SQL Tool for margin evaluation
        sql_query = f"""WITH CompanyMetrics AS (
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
            FROM {full_schema}.SP500
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
        logger.info("sql_tool_initialized")

        # Initialize Python Tool for web crawling
        def html_crawl(url):
            response = requests.get(url)
            return response.text

        python_crawler_config = {
            "tool_description": "reads the html from a given URL or website",
            "output_description": "html of a webpage",
            "python_func": html_crawl,
        }
        web_crawler = PythonTool(**python_crawler_config)
        logger.info("python_tool_initialized")

        # Initialize Agent with all tools
        agent = Agent(
            snowflake_connection=session,
            tools=[search_tool, analyst_tool, sql_tool, web_crawler],
            max_retries=3,
        )
        logger.info("agent_gateway_initialized")
        return agent

    except Exception as e:
        logger.error(
            "agent_gateway_initialization_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise


# Initialize Flask app
app = Flask(__name__)

# Initialize Snowflake session and Agent Gateway
try:
    snowflake_session = create_snowflake_session()
    agent = initialize_agent_gateway(snowflake_session)
except Exception as e:
    logger.error("initialization_failed", error=str(e), error_type=type(e).__name__)
    snowflake_session = None
    agent = None


@app.route("/health")
@require_jwt
def health():
    """Health check endpoint."""
    status = {
        "status": "healthy" if agent and snowflake_session else "unhealthy",
        "snowflake_connected": snowflake_session is not None,
        "agent_initialized": agent is not None,
    }
    status_code = 200 if status["status"] == "healthy" else 503
    return jsonify(status), status_code


@app.route("/api/prompt", methods=["POST"])
@require_jwt
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
        # if not data or "prompt" not in data:
        #     return jsonify({"status": "error", "message": "No prompt provided"}), 400

        prompt = data["prompt"]
        # prompt = data["data"][0][1]

        logger.info(
            "processing_prompt", message="Starting prompt processing", prompt=prompt
        )

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
    app.run(host="0.0.0.0", port=port)
