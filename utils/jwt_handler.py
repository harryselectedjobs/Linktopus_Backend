# utils/jwt_handler.py

from datetime import datetime, timedelta
import jwt

from config import (
    SECRET,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS
)

ALGORITHM = "HS256"


def create_access_token(user):

    payload = {
        "user_id": user["id"],
        "email": user["email"],
        "type": "access",
        "exp": datetime.utcnow() + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )
    }

    return jwt.encode(
        payload,
        SECRET,
        algorithm=ALGORITHM
    )


def create_refresh_token(user):

    payload = {
        "user_id": user["id"],
        "email": user["email"],
        "type": "refresh",
        "exp": datetime.utcnow() + timedelta(
            days=REFRESH_TOKEN_EXPIRE_DAYS
        )
    }

    return jwt.encode(
        payload,
        SECRET,
        algorithm=ALGORITHM
    )


def verify_token(token):

    return jwt.decode(
        token,
        SECRET,
        algorithms=[ALGORITHM]
    )