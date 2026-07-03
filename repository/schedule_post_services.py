import uuid
from datetime import datetime

from aws_connection.dynamodb_connection import _get_dynamodb_client

TABLE_NAME = "linkedin_posts"


def get_posts_table():
    dynamodb = _get_dynamodb_client()
    return dynamodb.Table(TABLE_NAME)


def save_post(post_text: str, post_date: str):
    """
    Save a new LinkedIn post.

    Args:
        post_text: Content of the post
        post_date: Scheduled/Post date (ISO format preferred)

    Returns:
        dict: Saved item
    """
    table = get_posts_table()

    item = {
        "post_id": str(uuid.uuid4()),
        "post_text": post_text,
        "post_date": post_date,
        "created_date": datetime.utcnow().isoformat()
    }

    table.put_item(Item=item)

    return item

def get_all_posts():
    """
    Returns all posts.
    """

    table = get_posts_table()

    response = table.scan()

    items = response.get("Items", [])

    # Handle pagination
    while "LastEvaluatedKey" in response:
        response = table.scan(
            ExclusiveStartKey=response["LastEvaluatedKey"]
        )
        items.extend(response.get("Items", []))

    # Sort by post_date
    items.sort(key=lambda x: x.get("post_date", ""))

    return items

def get_posts_by_date(post_date: str):
    """
    Returns all posts for a given date.

    Example:
        get_posts_by_date("2026-07-10")
    """

    table = get_posts_table()

    response = table.scan(
        FilterExpression="post_date = :date",
        ExpressionAttributeValues={
            ":date": post_date
        }
    )

    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression="post_date = :date",
            ExpressionAttributeValues={
                ":date": post_date
            },
            ExclusiveStartKey=response["LastEvaluatedKey"]
        )
        items.extend(response.get("Items", []))

    return items