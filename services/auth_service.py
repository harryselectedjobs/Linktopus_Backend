from datetime import datetime, timedelta
import jwt

# services/auth_service.py

from utils.jwt_handler import (
    create_access_token,
    create_refresh_token,
    verify_token
)

users = {

    "rahul@test.com": {

        "id":1,
        "email":"rahul@test.com",
        "password":"123456"

    }

}


def login(email,password):

    user = users.get(email)

    if not user:
        return {"error":"User not found"}

    if user["password"] != password:
        return {"error":"Wrong Password"}

    access = create_access_token(user)

    refresh = create_refresh_token(user)

    return {

        "access_token":access,
        "refresh_token":refresh

    }

def refresh(refresh_token):

    payload = verify_token(refresh_token)

    if payload["type"] != "refresh":

        return {

            "error":"Invalid Refresh Token"

        }

    user = users[payload["email"]]

    new_access = create_access_token(user)

    return {

        "access_token":new_access

    }