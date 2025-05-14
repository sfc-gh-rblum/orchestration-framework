import sys
import json
import base64


def decode_token(token):
    try:
        # Split the token into parts
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid token format")

        # Decode the payload (second part)
        # Add padding if necessary
        payload = parts[1]
        padding = 4 - (len(payload) % 4)
        if padding != 4:
            payload += "=" * padding

        # Decode base64
        decoded_bytes = base64.urlsafe_b64decode(payload)
        decoded = json.loads(decoded_bytes)

        print("\nDecoded token contents:")
        print(json.dumps(decoded, indent=2))

        # Print specific important fields
        print("\nImportant fields:")
        print(f"Scopes: {decoded.get('scp', 'Not found')}")
        print(f"Subject: {decoded.get('sub', 'Not found')}")
        print(f"Issuer: {decoded.get('iss', 'Not found')}")
        print(f"Expiration: {decoded.get('exp', 'Not found')}")
        print(f"Type: {decoded.get('type', 'Not found')}")
        print(f"Account ID: {decoded.get('accountId', 'Not found')}")

    except Exception as e:
        print(f"Error decoding token: {str(e)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python decode_token.py <token>")
        sys.exit(1)

    token = sys.argv[1]
    decode_token(token)
