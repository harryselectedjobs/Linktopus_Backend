import boto3
import os
import bcrypt
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def _get_dynamodb_client():
    return boto3.resource(
        "dynamodb",
        region_name=os.getenv("AWS_REGION", "ap-south-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    )


def _get_users_table():
    dynamodb = _get_dynamodb_client()
    return dynamodb.Table("linktopus_users")


def create_user(email: str, password: str):
    table = _get_users_table()

    existing = table.get_item(Key={"email": email})
    if "Item" in existing:
        return {"error": "User already exists"}

    hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

    table.put_item(Item={
        "email": email,
        "id": str(uuid.uuid4()),
        "password_hash": hashed_pw.decode("utf-8"),
        "created_at": datetime.utcnow().isoformat()
    })

    return {"status": "created", "email": email}


def get_user(email: str):
    table = _get_users_table()
    response = table.get_item(Key={"email": email})
    return response.get("Item")


def verify_user_password(email: str, password: str):
    user = get_user(email)
    if not user:
        return None

    stored_hash = user["password_hash"].encode("utf-8")
    if bcrypt.checkpw(password.encode("utf-8"), stored_hash):
        return user

    return None