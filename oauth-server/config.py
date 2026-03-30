"""Configuration from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# Tastytrade OAuth endpoints (from .well-known/openid-configuration)
TASTYTRADE_AUTHORIZE_URL = "https://my.tastytrade.com/auth.html"
TASTYTRADE_TOKEN_URL = "https://api.tastytrade.com/oauth/token"

# Tastytrade OAuth app credentials
TASTYTRADE_CLIENT_ID = os.environ["TASTYTRADE_CLIENT_ID"]
TASTYTRADE_CLIENT_SECRET = os.environ["TASTYTRADE_CLIENT_SECRET"]
TASTYTRADE_REDIRECT_URI = os.environ.get(
    "TASTYTRADE_REDIRECT_URI",
    "http://localhost:8000/auth/tastytrade/callback",
)

# Supabase
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# Streamlit app
STREAMLIT_APP_URL = os.environ.get("STREAMLIT_APP_URL", "http://localhost:8501")
