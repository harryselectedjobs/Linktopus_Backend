from pydantic import BaseModel


class GeneratePostRequest(BaseModel):
    user_input: str


class SharePostRequest(BaseModel):
    post_text: str