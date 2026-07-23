from fastapi import APIRouter, Depends, HTTPException

from linkedIn_services.share_post.share_post_on_page import post_to_selected_page
from linkedIn_services.share_post.share_post_services import get_LinkedIn_Posts, share_on_linkedIn
from models.share_post_models import *


from middleware.auth_middleware import authenticate
from repository.schedule_post_services import get_all_posts, get_posts_by_date, save_post, delete_old_posts

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
@router.post("/schedule-posts")
def save_post_route(
    request: SavePostRequest,
    user=Depends(authenticate)
):
    return save_post(
        post_text=request.post_text,
        post_date=request.post_date,
        account_type=request.account_type

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

# -------------------------
# Delete Old/History Posts
# -------------------------
@router.delete("/posts/history")
def delete_old_posts_route(
    user=Depends(authenticate)
):
    return delete_old_posts()


# Page post router #


@router.post("/page")
async def publish_page_post(request: LinkedInPagePostRequest):
    """
    Publish a post to the configured LinkedIn Company Page.
    """
    try:
        result = await post_to_selected_page(request.text)

        return {
            "success": True,
            "message": "Post published successfully.",
            "data": result,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )