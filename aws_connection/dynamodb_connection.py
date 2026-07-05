# create_users_table.py — run once
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

dynamodb = boto3.resource(
    "dynamodb",
    region_name=os.getenv("AWS_REGION", "ap-south-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)

table = dynamodb.create_table(
    TableName="linktopus_users",
    KeySchema=[
        {"AttributeName": "email", "KeyType": "HASH"}
    ],
    AttributeDefinitions=[
        {"AttributeName": "email", "AttributeType": "S"}
    ],
    BillingMode="PAY_PER_REQUEST"
)

table.wait_until_exists()
print("Table created:", table.table_name)