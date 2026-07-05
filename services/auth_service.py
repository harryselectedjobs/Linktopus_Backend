from utils.jwt_handler import (
    create_access_token,
    create_refresh_token,
    verify_token
)
from services.user_service import verify_user_password, get_user


def login(email, password):
    user = verify_user_password(email, password)

    if not user:
        return {"error": "Invalid email or password"}

    access = create_access_token(user)
    refresh = create_refresh_token(user)

    return {
        "access_token": access,
        "refresh_token": refresh
    }


def refresh(refresh_token):
    payload = verify_token(refresh_token)

    if payload["type"] != "refresh":
        return {"error": "Invalid Refresh Token"}

    user = get_user(payload["email"])

    if not user:
        return {"error": "User not found"}

    new_access = create_access_token(user)

    return {"access_token": new_access}