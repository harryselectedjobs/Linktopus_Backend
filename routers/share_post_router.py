from fastapi import APIRouter, Depends

from linkedIn_services.share_post.share_post_services import get_LinkedIn_Posts, share_on_linkedIn
from models.share_post_models import *


from middleware.auth_middleware import authenticate
from repository.schedule_post_services import get_all_posts, get_posts_by_date, save_post

router = APIRouter(
    prefix="/share-post",
tags=["SharePost"]
)


@router.post("/generate")
def generate_post_route(request: GeneratePostRequest, user=Depends(authenticate)):

    return get_LinkedIn_Posts(
        request.user_input
    )


@router.post("/publish")
def share_post_route(request: SharePostRequest, user=Depends(authenticate)):

    return share_on_linkedIn(
        request.post_text
    )

# -------------------------
# Save Scheduled Post
# -------------------------
@router.post("/posts")
def save_post_route(
    request: SavePostRequest,
    user=Depends(authenticate)
):
    return save_post(
        post_text=request.post_text,
        post_date=request.post_date
    )


# -------------------------
# Get All Posts
# -------------------------
@router.get("/posts")
def get_all_posts_route(
    user=Depends(authenticate)
):
    return get_all_posts()


# -------------------------
# Get Posts By Date
# -------------------------
@router.get("/posts/{post_date}")
def get_posts_by_date_route(
    post_date: str,
    user=Depends(authenticate)
):
    return get_posts_by_date(post_date)