from functools import lru_cache
import os

from dotenv import load_dotenv

# This local app is configured from its project `.env`; prefer it over stale
# values inherited from a parent terminal or system environment.
load_dotenv(override=True)


class Settings:
    app_name = "E-book Store API"
    database_url = os.getenv("DATABASE_URL", "sqlite:///./ebooks.db")
    session_secret = os.getenv("SESSION_SECRET", "change-this-development-session-secret")
    google_oauth_client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    google_oauth_client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    # Set this when the public URL differs from the URL FastAPI sees behind a proxy.
    google_oauth_redirect_url = os.getenv("GOOGLE_OAUTH_REDIRECT_URL", "")
    master_admin_email = os.getenv("MASTER_ADMIN_EMAIL", "barasapeter52@gmail.com").strip().casefold()
    mpesa_consumer_key = os.getenv("MPESA_CONSUMER_KEY", "")
    mpesa_consumer_secret = os.getenv("MPESA_CONSUMER_SECRET", "")
    mpesa_shortcode = os.getenv("MPESA_SHORTCODE", "")
    mpesa_passkey = os.getenv("MPESA_PASSKEY", "")
    mpesa_callback_url = os.getenv("MPESA_CALLBACK_URL", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
