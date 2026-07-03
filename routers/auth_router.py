from fastapi import APIRouter,Depends

from models.auth_models import *

from services.auth_service import *

from middleware.auth_middleware import authenticate

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


@router.post("/login")
def login_route(request:LoginRequest):

    return login(
        request.email,
        request.password
    )


@router.post("/refresh")
def refresh_route(request:RefreshRequest):

    return refresh(
        request.refresh_token
    )


@router.get("/profile")
def profile(user=Depends(authenticate)):

    return {

        "message":"Authenticated",

        "user":user

    }