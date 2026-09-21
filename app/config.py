from functools import lru_cache
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_name = "E-book Store API"
    database_url = os.getenv("DATABASE_URL", "sqlite:///./ebooks.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
