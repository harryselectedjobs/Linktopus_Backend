import requests
import webbrowser
import os
from dotenv import load_dotenv
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

from linkedIn_services.share_post.open_ai_services import generate_linkedin_posts
from linkedIn_services.share_post.token_manager import get_valid_access_token
# ============================================================
# CONFIGURATION — fill these in
# ============================================================
load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
SCOPES = os.getenv("SCOPES")
print(CLIENT_ID)
# ============================================================

auth_code = None


def linkedin_post_prompt_generator(user_input: str) -> str:
    return f"""
You are the senior LinkedIn Content Strategist and Brand Copywriter for Selected Group.

ABOUT SELECTED GROUP

Selected Group is a specialist technology recruitment business founded over ten years ago with a clear mission: to deliver a genuinely results-driven recruitment service for software vendors, technology businesses, and consulting organisations looking to scale.

Your responsibility is to transform the user's input into engaging, high-quality LinkedIn posts that align with Selected Group's brand voice.

The user may provide:
- A topic
- A rough idea
- Bullet points
- Company updates
- Talent insights
- Hiring trends
- Recruitment advice
- Candidate observations
- Industry news
- Product or service announcements
- Event summaries
- Or any combination of the above.

INSTRUCTIONS

- Carefully understand the user's intent.
- Expand naturally where appropriate without inventing facts, statistics, client names, or achievements.
- Write like an experienced technology recruitment professional who regularly speaks with candidates, hiring managers, founders, and technology leaders.
- Maintain a professional, insightful, and authentic tone.
- Start with a compelling hook.
- Use short paragraphs for readability.
- Deliver genuine value before promotion.
- End naturally with a takeaway or question when appropriate.
- Include 3–8 relevant hashtags when suitable.
- Avoid AI clichés, corporate buzzwords, clickbait, and excessive emojis.
- Never mention AI or that the content was generated.

VARIATIONS

Generate FOUR DISTINCT LinkedIn posts.

Each variation should:
- Have a different opening hook.
- Use a different writing style or storytelling approach.
- Avoid repeating the same sentences or structure.
- Be unique while conveying the same core idea.

OUTPUT REQUIREMENTS

Return ONLY valid JSON.

The response MUST strictly follow this schema:

{{
    "posts": [
        {{
            "variation": 1,
            "post_content": "<LinkedIn Post 1>"
        }},
        {{
            "variation": 2,
            "post_content": "<LinkedIn Post 2>"
        }},
        {{
            "variation": 3,
            "post_content": "<LinkedIn Post 3>"
        }},
        {{
            "variation": 4,
            "post_content": "<LinkedIn Post 4>"
        }}
    ]
}}

Do not return markdown.
Do not include explanations.
Do not include additional keys.
Ensure the output is valid JSON.

USER INPUT:
{user_input}
"""


def get_LinkedIn_Posts(user_input: str):
    prompt = linkedin_post_prompt_generator(user_input)
    responses = generate_linkedin_posts(prompt)
    return responses


# --------------------------------------------------------------------- #

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Auth successful! You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>Auth failed. No code received.</h2>")

    def log_message(self, format, *args):
        pass  # suppress server logs


def get_auth_code():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "test123"
    }
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(params)
    print(f"\n[1] Opening browser for LinkedIn login...")
    webbrowser.open(url)

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    print("[1] Waiting for callback on http://localhost:8080/callback ...")
    server.handle_request()  # handles one request then stops
    return auth_code


def get_access_token(code):
    print("\n[2] Exchanging code for access token...")
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
    token = response.json()["access_token"]
    print(f"[2] Access token received.")
    return token


def get_user_profile(token):
    print("\n[3] Fetching user profile...")
    response = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    profile = response.json()
    print(f"[3] Logged in as: {profile.get('name')} ({profile.get('email')})")
    return profile["sub"]


def post_to_linkedin(token, sub, text):
    print("\n[4] Publishing post to LinkedIn...")
    payload = {
        "author": f"urn:li:person:{sub}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE"
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }
    response = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0"
        },
        json=payload
    )
    response.raise_for_status()
    post_id = response.json().get("id")
    print(f"[4] Post published! ID: {post_id}")
    print(f"\n✅ Done! Check your LinkedIn profile — the post is live.")
    return post_id


def share_on_linkedIn(post_text: str):
    access_token = get_valid_access_token()
    sub = get_user_profile(access_token)
    post_id = post_to_linkedin(access_token, sub, post_text)
    return {"status": "posted", "linkedin_urn": post_id}

