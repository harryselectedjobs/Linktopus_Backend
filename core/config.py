from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    UNIPILE_API_KEY: str

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()