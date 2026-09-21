from functools import lru_cache
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_name = "E-book Store API"
    database_url = os.getenv("DATABASE_URL", "sqlite:///./ebooks.db")
    session_secret = os.getenv("SESSION_SECRET", "change-this-development-session-secret")
    mpesa_consumer_key = os.getenv("MPESA_CONSUMER_KEY", "")
    mpesa_consumer_secret = os.getenv("MPESA_CONSUMER_SECRET", "")
    mpesa_shortcode = os.getenv("MPESA_SHORTCODE", "")
    mpesa_passkey = os.getenv("MPESA_PASSKEY", "")
    mpesa_callback_url = os.getenv("MPESA_CALLBACK_URL", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
