import requests
import json
from dotenv import load_dotenv
import os

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def extract_job_title_and_skills(job_description: str):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": """
You are an expert recruitment assistant preparing structured data for a LinkedIn Recruiter search API.

Given a job description, extract:

1. "job_title": ONE short job title (2-4 words) that real people would
   actually use in their LinkedIn headline for this role. Do NOT combine
   multiple concepts into one long phrase.
   Example: "Revenue Operations Manager"

2. "skills": the top 3-5 individual technical/professional skills, each as a
   SHORT standalone term (1-3 words), matching how skills appear on LinkedIn
   profiles. Do NOT write sentences or combine skills with "AND".
   Example: ["Revenue Operations", "SaaS Sales", "GTM Strategy", "Team Leadership"]

3. "company_list": an array of target companies.

IMPORTANT: company_list has TWO SEPARATE SECTIONS and MUST be constructed in this exact order.

SECTION A — EXPLICIT COMPANIES:
- Scan the ENTIRE job description from beginning to end.
- Identify EVERY company explicitly mentioned by name.
- This includes:
  - companies mentioned in the main job description
  - companies mentioned in "hired from" lists
  - companies mentioned in examples
  - companies mentioned in candidate/company background information
  - companies mentioned anywhere else in the provided job description
- Copy each company name EXACTLY as it appears in the job description.
- DO NOT shorten, normalize, summarize, merge, or remove companies.
- DO NOT limit the number of companies in SECTION A.
- If the job description explicitly contains 50 companies, return all 50 companies.
- If it explicitly contains 100 companies, return all 100 companies.
- Preserve the original order in which the companies appear whenever possible.
- Remove only exact duplicates.

SECTION B — ADDITIONAL COMPANIES:
- AFTER ALL explicitly mentioned companies have been included, add up to 15 additional well-known companies.
- These additional companies must NOT already appear in SECTION A.
- They should be relevant sources of candidates for this role and industry.
- The 15-company limit applies ONLY to SECTION B.
- Never remove an explicitly mentioned company to stay within the 15-company limit.

FINAL company_list:
[ALL companies from SECTION A] + [up to 15 additional companies from SECTION B]

4. "seniority_level": pick exactly ONE value that best matches the seniority
   indicated in the job description (usually stated in the title or in
   phrases like "this VP will..." or "reports into the SVP..."). Choose only
   from this exact list: ["Owner", "Partner", "CXO", "VP", "Director",
   "Manager", "Senior", "Entry", "Training", "Unpaid"]. Return the chosen
   value entirely in lowercase (e.g. "vp", "director", "entry") — do not
   return it in the original casing shown above.

5. A professional LinkedIn InMail message (80-120 words).
6. A LinkedIn connection note (maximum 300 characters).

Rules:
- The InMail must begin with: Hi [Candidate's Name],
- Always use the exact placeholder "[Candidate's Name]".
- Never use "[Name]", "[First Name]", "{name}", or any other variation.
- The connection note must also use "[Candidate's Name]" if a name is required.
- Do NOT include the signature in the connection note.
- The InMail must end with the following signature exactly:

Best regards,

Harry Brown
Owner @ Selected
Direct Dial: 0203 865 6229
Mobile: 07824 7011 54
Address: Argent House, Hook Rise South, Tolworth, Surrey, KT6 7LD

Return ONLY valid JSON in this exact format:
{
  "job_title": "...",
  "skills": ["skill1", "skill2", "skill3"],
  "company_list": ["Company A", "Company B", "..."],
  "seniority_level": "...",
  "inMailMessage": "...",
  "connectionNote": "..."
}
"""
            },
            {"role": "user", "content": job_description}
        ],
        "temperature": 0
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return json.loads(content)

### modification v2 ###

import json
import requests

def extract_values(job_description: str):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    system_prompt = """You are a job description parser and outreach message writer. Given a job description, extract fields and generate outreach content. Return ONLY valid JSON (no markdown, no commentary):

{
  "role": ["..."],
  "companies": ["..."],
  "location": ["..."],
  "inMailMessage": "...",
  "connectionNote": "..."
}

Rules:

1. "role": One or two job titles, not more than 2. These must be NORMALIZED, standardized titles as they commonly appear on LinkedIn — not a literal copy of whatever phrasing the JD uses internally.
   - If the JD uses a vague, internal, or non-standard title, map it to the closest real, widely-used LinkedIn title.
   - If the JD title is already standard, keep it as-is.
   - Never more than 2 titles.

2. "companies": List every company explicitly mentioned in the JD. Then ADD 3-5 additional companies similar in industry/domain to the mentioned ones.

3. "location": Identify the location(s) mentioned and expand into specific cities.
   - Specific city -> use as-is.
   - Broad region (e.g. "East Coast") -> expand into that region's major cities.
   - Whole country (e.g. "United States", "USA", "nationwide") -> expand into a national spread of major hub cities across multiple regions (West Coast, East Coast, Midwest, South).
   - Remote explicitly stated -> ["Remote"].
   - No location mentioned -> [] (empty array).

4. "inMailMessage": A professional LinkedIn InMail message, 80-120 words, written to a candidate about this role based on the JD.
   - Must begin exactly with: "Hi [Candidate's Name],"
   - Use the exact placeholder "[Candidate's Name]" — never "[Name]", "[First Name]", "{name}", or any other variation.
   - Briefly cover: the role, why the candidate might be a fit, and the company/opportunity, in a warm and professional recruiter tone.
   - Must end with exactly this signature, on its own lines, with no text after it:

Best regards,

Harry Brown
Owner @ Selected
Direct Dial: 0203 865 6229
Mobile: 07824 7011 54
Address: Argent House, Hook Rise South, Tolworth, Surrey, KT6 7LD

5. "connectionNote": A LinkedIn connection request note, maximum 300 characters (including spaces).
   - If addressing the candidate by name, use the exact placeholder "[Candidate's Name]" (same rule as above — no variations).
   - Short, warm, professional — a brief reason for connecting tied to the role, no signature.
   - Do NOT include the signature block in the connection note.
   - Must stay within 300 characters total.

Always return valid, parseable JSON matching the schema above. Do not wrap it in code fences."""

    payload = {
        "model": "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": job_description}
        ],
        "temperature": 0.3
    }

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)

    # Hard-enforce the connection note length cap, since the model can drift over the limit
    if "connectionNote" in result and len(result["connectionNote"]) > 300:
        result["connectionNote"] = result["connectionNote"][:300].rstrip()

    return result
