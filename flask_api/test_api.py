import requests
import json

BASE_URL = "http://localhost:5000"


def test_health():
    response = requests.get(f"{BASE_URL}/health")
    print(f"Health Check Status: {response.status_code}")
    print(f"Response: {response.json()}")
    print("-" * 50)


def test_items():
    # Test GET all items
    response = requests.get(f"{BASE_URL}/api/items")
    print(f"GET all items Status: {response.status_code}")
    print(f"Response: {response.json()}")
    print("-" * 50)

    # Test POST new item
    new_item = {"name": "Test Item", "description": "This is a test item"}
    response = requests.post(
        f"{BASE_URL}/api/items",
        headers={"Content-Type": "application/json"},
        data=json.dumps(new_item),
    )
    print(f"POST new item Status: {response.status_code}")
    print(f"Response: {response.json()}")
    item_id = response.json().get("id")
    print("-" * 50)

    # Test GET specific item
    response = requests.get(f"{BASE_URL}/api/items/{item_id}")
    print(f"GET specific item Status: {response.status_code}")
    print(f"Response: {response.json()}")
    print("-" * 50)


if __name__ == "__main__":
    print("Testing API endpoints...\n")
    test_health()
    test_items()
