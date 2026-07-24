import uuid
from datetime import datetime

from aws_connection.dynamodb_connection import _get_dynamodb_client

TABLE_NAME = "linkedin_posts"


def get_posts_table():
    dynamodb = _get_dynamodb_client()
    return dynamodb.Table(TABLE_NAME)

def normalize_word(word: str) -> str:
    return word.strip().capitalize()

def save_post(post_text: str, post_date: str,account_type:str):
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
        "accountType":normalize_word(account_type),
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

def delete_old_posts():
    """
    Deletes all posts with post_date before today.
    Keeps today's and future posts.

    Example:
        If today is 2026-07-07, deletes all posts on or before 2026-07-06,
        keeps posts on 2026-07-07 onward.
    """

    table = get_posts_table()

    today = datetime.utcnow().date().isoformat()  # e.g. "2026-07-07"

    response = table.scan(
        FilterExpression="post_date < :today",
        ExpressionAttributeValues={
            ":today": today
        }
    )

    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression="post_date < :today",
            ExpressionAttributeValues={
                ":today": today
            },
            ExclusiveStartKey=response["LastEvaluatedKey"]
        )
        items.extend(response.get("Items", []))

    deleted_count = 0
    for item in items:
        table.delete_item(Key={"post_id": item["post_id"]})
        deleted_count += 1

    return {
        "deleted_count": deleted_count,
        "cutoff_date": today
    }