import json
import time
import os

TOKEN_PATH = os.path.join(os.path.dirname(__file__), "linkedin_token.json")


def load_token():
    with open(TOKEN_PATH) as f:
        return json.load(f)


def get_valid_access_token():
    token_data = load_token()

    if "expires_at" not in token_data:
        token_data["expires_at"] = time.time() + token_data["expires_in"] - 300
        with open(TOKEN_PATH, "w") as f:
            json.dump(token_data, f, indent=2)

    if time.time() > token_data["expires_at"]:
        raise RuntimeError(
            "LinkedIn access token expired. Re-run check_token_response.py locally "
            "and re-upload linkedin_token.json to the server."
        )

    return token_data["access_token"]