import os
import uuid
import boto3
from dotenv import load_dotenv
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aws_connection.dynamodb_connection import _get_dynamodb_client

load_dotenv()

router = APIRouter()

DEFAULT_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"


# ── DynamoDB client / table setup ───────────────────────────────────────────

# def _get_dynamodb_client():
#     if os.getenv("AWS_ACCESS_KEY_ID"):
#         return boto3.resource(
#             "dynamodb",
#             region_name=os.getenv("AWS_REGION", "ap-south-1"),
#             aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
#             aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
#         )
#     return boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "ap-south-1"))


def create_project_candidates_table(table_name: str = "jobPipelineTable"):
    dynamodb = _get_dynamodb_client()

    existing_tables = dynamodb.meta.client.list_tables()["TableNames"]
    if table_name in existing_tables:
        print(f"Table '{table_name}' already exists.")
        return dynamodb.Table(table_name)

    table = dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "project_id", "KeyType": "HASH"},
            {"AttributeName": "candidate_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "project_id", "AttributeType": "S"},
            {"AttributeName": "candidate_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    print(f"Table '{table_name}' created successfully.")
    return table


def get_table(table_name: str = "jobPipelineTable"):
    return _get_dynamodb_client().Table(table_name)


# ── Table operations ─────────────────────────────────────────────────────────

def save_candidates(project_id: str, candidates: list, table_name: str = "jobPipelineTable"):
    """
    candidates: the list from search_result["items"] — matches Unipile's
    LinkedinSearch "PEOPLE" object shape. Saves ALL of them (up to ~200),
    with outreach flags initialized to False.
    """
    table = get_table(table_name)

    with table.batch_writer() as batch:
        for candidate in candidates:
            candidate_id = candidate.get("id")
            if not candidate_id:
                print(f"⚠️ Skipping candidate with no id: {candidate.get('name')}")
                continue

            batch.put_item(Item={
                "project_id": project_id,
                "candidate_id": candidate_id,
                "full_name": candidate.get("name", ""),
                "headline": candidate.get("headline", ""),
                "location": candidate.get("location", ""),
                "profile_url": candidate.get("profile_url", ""),
                "public_profile_url": candidate.get("public_profile_url", ""),
                "public_identifier": candidate.get("public_identifier", ""),
                "recruiter_candidate_id": candidate.get("recruiter_candidate_id", ""),
                "can_send_inmail": candidate.get("can_send_inmail", False),
                "network_distance": candidate.get("network_distance", ""),
                "inmail_sent": False,
                "connection_sent": False,
                "outreach_attempted": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

    print(f"✅ Saved {len(candidates)} candidates for project {project_id}")


def get_top_candidates(project_id: str, limit: int = 50, table_name: str = "jobPipelineTable"):
    table = get_table(table_name)
    response = table.query(
        KeyConditionExpression=Key("project_id").eq(project_id),
        Limit=limit,
    )
    return response.get("Items", [])


def mark_outreach_sent(project_id: str, candidate_id: str, inmail: bool = False,
                        connection: bool = False, attempted: bool = False,
                        table_name: str = "jobPipelineTable"):
    table = get_table(table_name)

    update_parts = ["outreach_attempted = :a"]
    values = {":a": attempted}

    if inmail:
        update_parts.append("inmail_sent = :i")
        values[":i"] = True
    if connection:
        update_parts.append("connection_sent = :c")
        values[":c"] = True

    table.update_item(
        Key={"project_id": project_id, "candidate_id": candidate_id},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeValues=values,
    )


