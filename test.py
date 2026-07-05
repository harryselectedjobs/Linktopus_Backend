# check_token_response.py — run locally on your PC
from share_post_services import get_auth_code, get_access_token
import requests

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
print(response.json())