import uuid
from datetime import datetime, timedelta, timezone

from aws_connection.dynamodb_connection import _get_dynamodb_client
from models.calendar_event import BookingAvailability, BookingReason

TABLE_NAME = "MeetingSchedulingLinks"


def add_meeting_record(payload: dict, expiry_hours: int = 72):
    """
    Stores a meeting scheduling request in DynamoDB.

    Args:
        payload (dict): Meeting payload.
        expiry_hours (int): Link expiry time in hours.

    Returns:
        dict: Created record.
    """
    dynamodb = _get_dynamodb_client()
    table = dynamodb.Table(TABLE_NAME)

    meeting_id = str(uuid.uuid4())

    now = datetime.now(timezone.utc)
    expires_at = int((now + timedelta(hours=expiry_hours)).timestamp())

    item = {
        "meeting_id": meeting_id,
        "payload": payload,
        "used": False,
        "scheduled": False,
        "created_at": now.isoformat(),
        "expires_at": expires_at,
    }

    table.put_item(Item=item)

    return item


def check_booking_availability(email: str) -> BookingAvailability:
    """
    Checks whether a user can book a meeting.
    """
    dynamodb = _get_dynamodb_client()
    table = dynamodb.Table("MeetingSchedulingLinks")

    email = email.strip().lower()

    # Retrieve all items (handles pagination)
    items = []
    response = table.scan()
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = table.scan(
            ExclusiveStartKey=response["LastEvaluatedKey"]
        )
        items.extend(response.get("Items", []))

    # Debug - remove after verifying
    print("Items in DynamoDB:")
    for item in items:
        print(item)

    for item in items:
        payload = item.get("payload", {})
        title = payload.get("title")

        attendees = payload.get("attendees", [])

        for attendee in attendees:
            attendee_email = attendee.get("email", "").strip().lower()

            if attendee_email == email:
                if item.get("used", False):
                    return BookingAvailability(
                        booking_available=False,
                        reason=BookingReason.ALREADY_BOOKED,
                        title=title,
                    )

                return BookingAvailability(
                    booking_available=True,
                    reason=BookingReason.CAN_BOOK,
                    title=title,
                )

    return BookingAvailability(
        booking_available=False,
        reason=BookingReason.UNAUTHORIZED_USER,
        title=None,
    )

# my_payload = {
#     "title": "Python Hiring 2026",
#     "attendees": [
#         {
#             "email": "rkgupta@virtualemployee.com"
#         }
#     ]
# }

# response = add_meeting_record(my_payload)
# print(response)

# resp = check_booking_availability("rkgupta@virtualemployee.com")
# print(resp.model_dump())