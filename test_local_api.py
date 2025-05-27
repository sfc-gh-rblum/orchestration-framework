import requests

# import json
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


def test_api():
    """Test the local API endpoints."""
    # Get JWT token
    token = get_jwt_token()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f'Snowflake Token="{token}"',
    }

    # Test health endpoint
    print("\nTesting health endpoint...")
    health_response = requests.get("http://localhost:8081/health", headers=headers)
    print(f"Status Code: {health_response.status_code}")
    print(f"Response: {health_response.text}")

    # Test prompt endpoint
    print("\nTesting prompt endpoint...")
    prompt_data = {"prompt": "What is Apple's market cap?"}
    prompt_response = requests.post(
        "http://localhost:8081/api/prompt", headers=headers, json=prompt_data
    )
    print(f"Status Code: {prompt_response.status_code}")
    print(f"Response: {prompt_response.text}")


if __name__ == "__main__":
    test_api()
