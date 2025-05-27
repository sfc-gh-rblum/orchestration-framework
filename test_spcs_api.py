import requests
import json
from generateJWT import JWTGenerator
from datetime import timedelta


def get_jwt_token():
    """Generate a JWT token for authentication."""
    generator = JWTGenerator(
        "sfsenorthamerica-demo175",
        "rblum",
        "./rsa_key.p8",
        timedelta(minutes=59),
        timedelta(minutes=54),
    )
    return generator.get_token()


def test_spcs_api():
    """Test the SPCS API endpoints."""
    # Get JWT token
    token = get_jwt_token()

    # Print token for debugging
    print("\nUsing JWT Token:")
    print(token)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f'Snowflake Token="{token}"',  # Changed back to Snowflake Token format
    }

    # Print headers for debugging
    print("\nRequest Headers:")
    print(json.dumps(headers, indent=2))

    base_url = "https://btae3td-sfsenorthamerica-demo175.snowflakecomputing.app"

    # Test health endpoint
    print("\nTesting SPCS health endpoint...")
    try:
        health_response = requests.get(
            f"{base_url}/health", headers=headers, verify=True
        )
        print(f"Status Code: {health_response.status_code}")
        print(f"Response Headers: {dict(health_response.headers)}")
        print(f"Response: {health_response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error testing health endpoint: {str(e)}")

    # Test prompt endpoint
    print("\nTesting SPCS prompt endpoint...")
    prompt_data = {"prompt": "What is Apple's market cap?"}
    try:
        prompt_response = requests.post(
            f"{base_url}/api/prompt", headers=headers, json=prompt_data, verify=True
        )
        print(f"Status Code: {prompt_response.status_code}")
        print(f"Response Headers: {dict(prompt_response.headers)}")
        print(f"Response: {prompt_response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error testing prompt endpoint: {str(e)}")


if __name__ == "__main__":
    test_spcs_api()
