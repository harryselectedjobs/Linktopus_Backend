from pydantic import BaseModel


class LoginRequest(BaseModel):

    email:str
    password:str


class RefreshRequest(BaseModel):

    refresh_token:str