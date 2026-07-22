from fastapi import APIRouter, Query

from linkedIn_services.linkedin_recruiter_automation.automation_service import book_one_hour_meeting
from models.calendar_event import BookingAvailability, BookMeetingRequest
from repository.schedule_calendar_services import check_booking_availability

router = APIRouter(
    prefix="/calendar",
    tags=["Calendar Booking"]
)


@router.get("/check-booking-availability",response_model=BookingAvailability,summary="Check whether a user can book a meeting")
def check_booking_availability_route(email: str = Query(..., description="Attendee email")):
    return check_booking_availability(email).model_dump()



@router.post(
    "/book-meeting",
    summary="Book a one-hour meeting"
)
async def book_meeting(request: BookMeetingRequest):
    """
    Books a one-hour meeting in the London timezone.
    """
    return await book_one_hour_meeting(
        date=request.date,
        start_time=request.start_time,
        email=request.email,
        title=request.title,
    )




