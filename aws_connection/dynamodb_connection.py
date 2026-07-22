import boto3
import os
from dotenv import load_dotenv

load_dotenv()


def _get_dynamodb_client():
    return boto3.resource(
        "dynamodb",
        region_name=os.getenv("AWS_REGION", "ap-south-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    )


def create_meeting_table():
    dynamodb = _get_dynamodb_client()

    existing_tables = dynamodb.meta.client.list_tables()["TableNames"]

    table_name = "MeetingSchedulingLinks"

    if table_name in existing_tables:
        print(f"Table '{table_name}' already exists.")
        return

    table = dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {
                "AttributeName": "meeting_id",
                "KeyType": "HASH"
            }
        ],
        AttributeDefinitions=[
            {
                "AttributeName": "meeting_id",
                "AttributeType": "S"
            }
        ],
        BillingMode="PAY_PER_REQUEST"
    )

    print("Creating table...")
    table.wait_until_exists()
    print(f"Table '{table_name}' created successfully.")


if __name__ == "__main__":
    create_meeting_table()

