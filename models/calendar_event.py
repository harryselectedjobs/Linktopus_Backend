from typing import List, Optional
from pydantic import BaseModel


class EventTime(BaseModel):
    date_time: str
    time_zone: str


class Attendee(BaseModel):
    email: str


class BookEventRequest(BaseModel):
    start: EventTime
    end: EventTime
    title: str
    attendees: List[Attendee]


from enum import Enum
from typing import Optional
from pydantic import BaseModel


class BookingReason(str, Enum):
    ALREADY_BOOKED = "Already Booked"
    UNAUTHORIZED_USER = "Unauthorized user"
    CAN_BOOK = "You can book meetings"


class BookingAvailability(BaseModel):
    booking_available: bool
    reason: BookingReason
    title: Optional[str] = None

from pydantic import BaseModel


class BookMeetingRequest(BaseModel):
    date: str          # YYYY-MM-DD
    start_time: str    # HH:MM (24-hour, London time)
    email: str
    title: str