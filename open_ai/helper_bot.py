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
                "content": (
                    "You are an expert recruitment assistant. "
                    "Extract the job title and the top 3 most important technical skills "
                    "from the given job description.\n\n"
                    "Return ONLY valid JSON in this format:\n"
                    "{\n"
                    '  "job_title": "...",\n'
                    '  "skills": ["skill1", "skill2", "skill3"]\n'
                    "}"
                )
            },
            {
                "role": "user",
                "content": job_description
            }
        ],
        "temperature": 0
    }

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]

    return json.loads(content)


# jd = """
# # Associate Value Engineer (AI-Driven Data Science & Analytics)
#
# ## Location
# Raleigh, North Carolina, United States (Hybrid)
#
# ## Employment Type
# Full-Time
#
# ## Job Summary
#
# We are looking for an enthusiastic Associate Value Engineer (AI-Driven Data Science & Analytics) to join our growing team. This role is ideal for recent graduates or early-career professionals with a strong technical background who are passionate about data analytics, artificial intelligence, and solving complex business challenges.
#
# As an Associate Value Engineer, you will work at the intersection of technology, business, and customer success. You will collaborate with customers, sales teams, and engineering teams to analyze business processes, develop AI-driven solutions, and demonstrate the value of data analytics through proof-of-concept projects. This position offers hands-on experience with enterprise software, machine learning, SQL, and process intelligence while building strong customer relationships.
#
# ## Key Responsibilities
#
# - Analyze customer business processes to identify opportunities for improvement using AI and data analytics.
# - Build and optimize data models using SQL and analytics platforms.
# - Design, prototype, and demonstrate AI-powered solutions, including machine learning and Large Language Model (LLM) applications.
# - Develop proof-of-concept (PoC) projects that showcase measurable business value.
# - Translate customer requirements into scalable technical solutions.
# - Deliver engaging presentations, product demonstrations, and technical workshops for customers and stakeholders.
# - Collaborate with implementation, product, and engineering teams to ensure successful solution delivery.
# - Build trusted relationships with customers by providing technical expertise and strategic guidance.
# - Quantify business impact through ROI and value realization analyses.
# - Create reusable technical assets, documentation, and best practices for future customer engagements.
# - Stay current with emerging AI technologies, analytics tools, and industry trends.
#
# ## Required Qualifications
#
# - Bachelor's degree in Computer Science, Data Science, Engineering, Mathematics, Industrial Engineering, Data Analytics, or a related STEM discipline.
# - 1–3 years of professional experience or significant internship experience in data analytics, software engineering, or business intelligence.
# - Strong knowledge of SQL and relational databases.
# - Experience with Power BI, Tableau, or similar data visualization tools.
# - Strong analytical and problem-solving abilities.
# - Excellent verbal and written communication skills.
# - Ability to explain technical concepts to both technical and non-technical audiences.
# - Customer-focused mindset with strong collaboration skills.
# - Self-motivated with a passion for continuous learning and innovation.
#
# ## Preferred Qualifications
#
# - Experience with Python and machine learning frameworks.
# - Familiarity with Large Language Models (LLMs) and Generative AI.
# - Experience with proof-of-concept (PoC) development.
# - Knowledge of business process improvement methodologies.
# - Experience with SAP, Oracle, or other enterprise systems.
# - Understanding of supply chain or finance business processes.
# - Experience developing business cases using ROI and Total Cost of Ownership (TCO).
# - Previous customer-facing, consulting, or pre-sales experience.
#
# ## Technical Skills
#
# - SQL
# - Python
# - Machine Learning
# - Generative AI / LLMs
# - Power BI
# - Tableau
# - Data Modeling
# - Business Intelligence
# - Data Visualization
# - Process Mining
# - Business Analysis
# - Proof of Concept (PoC)
# - SAP (Preferred)
# - Oracle (Preferred)
#
# ## Soft Skills
#
# - Analytical Thinking
# - Communication
# - Problem Solving
# - Customer Relationship Management
# - Presentation Skills
# - Collaboration
# - Time Management
# - Critical Thinking
# - Adaptability
# - Continuous Learning
#
# ## What We Offer
#
# - Comprehensive onboarding and professional development.
# - Mentorship from experienced industry professionals.
# - Hands-on experience with enterprise AI and analytics solutions.
# - Opportunities to work on impactful customer projects.
# - Collaborative and inclusive work environment.
# - Career growth within a fast-growing technology organization.
# - Flexible hybrid work model.
# - Competitive salary and performance-based rewards.
#
# ## Ideal Candidate
#
# The ideal candidate is passionate about artificial intelligence, data analytics, and business transformation. They enjoy solving complex problems, working directly with customers, and using technology to create measurable business value. They are eager to learn, thrive in a collaborative environment, and aspire to build a successful career in enterprise software and AI-driven consulting.
# """
#
# response_q = extract_job_title_and_skills(jd)
# print(response_q)