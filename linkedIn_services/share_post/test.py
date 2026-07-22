# check_token_response.py
from share_post_services import get_auth_code, REDIRECT_URI, CLIENT_ID, CLIENT_SECRET
import requests
import json

code = get_auth_code()

response = requests.post(
    "https://www.linkedin.com/oauth/v2/accessToken",
    data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    },
    headers={"Content-Type": "application/x-www-form-urlencoded"}
)
response.raise_for_status()
token_data = response.json()

with open("linkedin_token.json", "w") as f:
    json.dump(token_data, f, indent=2)

print("Saved. Keys returned:", list(token_data.keys()))
print("Has refresh_token:", "refresh_token" in token_data)