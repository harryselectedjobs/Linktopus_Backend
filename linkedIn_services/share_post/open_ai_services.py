import requests
import json
from dotenv import load_dotenv
import os

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")





def generate_linkedin_posts(prompt: str, max_retries: int = 3) -> dict:
    url = "https://api.openai.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are an expert LinkedIn Content Strategist."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.9,
        "max_tokens": 16000,
        "response_format": {"type": "json_object"}
    }

    for attempt in range(max_retries):
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        return json.loads(content)
