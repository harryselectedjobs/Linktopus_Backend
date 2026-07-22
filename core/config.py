import os
from dotenv import load_dotenv

load_dotenv()

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    UNIPILE_API_KEY: str
    UNIPILE_CALENDAR_API_KEY:str

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()