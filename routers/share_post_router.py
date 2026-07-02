from fastapi import APIRouter, Depends

from linkedIn_services.share_post.share_post_services import get_LinkedIn_Posts, share_on_linkedIn
from models.share_post_models import *


from middleware.auth_middleware import authenticate

router = APIRouter(
    prefix="/share-post"
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