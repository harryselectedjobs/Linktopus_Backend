# middleware/auth_middleware.py

from fastapi import Header,HTTPException

from utils.jwt_handler import verify_token


def authenticate(

    authorization:str = Header(None)

):

    if authorization is None:

        raise HTTPException(401,"Missing Token")

    token = authorization.split(" ")[1]

    payload = verify_token(token)

    if payload["type"] != "access":

        raise HTTPException(
            401,
            "Access Token Required"
        )

    return payload