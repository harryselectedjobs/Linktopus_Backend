import requests
import json
from dotenv import load_dotenv
import os


load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def linkedin_post_prompt_generator(user_input: str, variation: int, style_hint: str) -> str:
    return f"""
You are the senior LinkedIn Content Strategist and Brand Copywriter for Selected Group.

ABOUT SELECTED GROUP

Selected Group is a specialist technology recruitment business founded over ten years ago with a clear mission: to deliver a genuinely results-driven recruitment service for software vendors, technology businesses, and consulting organisations looking to scale.

Your responsibility is to transform the user's input into ONE engaging, high-quality LinkedIn post that aligns with Selected Group's brand voice.

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

LENGTH REQUIREMENT (READ CAREFULLY)

- If the user's input specifies a word count, character count, or length (e.g. "2000 words", "short post", "under 100 words", "long-form"), you MUST follow it as closely as possible. Treat this as a hard constraint, not a suggestion.
- If no length is specified, write naturally in the standard LinkedIn range of roughly 150–300 words — long enough to deliver real value, short enough to stay scannable.
- Do not silently shrink the post to save space. If the user asked for 2000 words, write close to 2000 words, even though that makes the output long.
- Before finalizing, estimate the word count of what you've written. If it falls short of the required length, continue expanding with additional relevant detail, examples, or context until the requirement is met. Do not submit a post short of the target.

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

STYLE FOR THIS VARIATION

This is variation {variation} of 4. Use a distinct opening hook and writing/storytelling approach from the other variations. Specifically: {style_hint}

OUTPUT REQUIREMENTS

- Return ONLY the LinkedIn post text itself.
- Do NOT return JSON.
- Do NOT include labels like "Post:" or "Variation 1:".
- Do NOT include markdown code fences.
- Do NOT include explanations before or after the post.

USER INPUT:
{user_input}
"""




def generate_linkedin_posts(user_input: str, max_retries: int = 3) -> dict:
    url = "https://api.openai.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    style_hints = [
        "open with a bold, contrarian, or surprising statement",
        "open with a short personal anecdote or observation from conversations with candidates/hiring managers",
        "open with a direct question to the reader",
        "open with a short, punchy statistic-style or 'here's the truth' statement",
    ]

    posts = []

    for variation in range(1, 5):
        prompt = linkedin_post_prompt_generator(
            user_input, variation, style_hints[variation - 1]
        )

        payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are an expert LinkedIn Content Strategist."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.9,
            "max_tokens": 4000,  # generous per-post budget, no JSON mode
        }

        content = None
        for attempt in range(max_retries):
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            candidate = result["choices"][0]["message"]["content"].strip()

            # basic sanity check — retry if suspiciously short
            if candidate and len(candidate.split()) > 40:
                content = candidate
                break

        if content is None:
            content = candidate  # fall back to last attempt even if short, rather than dropping the post

        posts.append({"variation": variation, "post_content": content})

    return {"posts": posts}