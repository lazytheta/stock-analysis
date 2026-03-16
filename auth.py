"""
Authentication module — Supabase Auth with email/password and Google OAuth.

Keeps auth logic out of the main app. All functions return typed tuples
so callers can handle errors consistently.
"""

import base64
import logging
import os
from datetime import date

import streamlit as st

logger = logging.getLogger(__name__)

from error_logger import log_error


def _get_secret(name):
    """Read a secret from Streamlit secrets or environment variables."""
    try:
        val = st.secrets.get(name)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(name)


def init_auth_client():
    """Create an anonymous (unauthenticated) Supabase client for login/signup."""
    from supabase import create_client

    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")
    return create_client(url, key)


def login_email_password(email, password):
    """Sign in with email/password.

    Returns (client, user, None) on success or (None, None, error_message) on failure.
    """
    try:
        client = init_auth_client()
        resp = client.auth.sign_in_with_password({"email": email, "password": password})
        return client, resp.user, None
    except Exception as e:
        msg = str(e)
        if "Invalid login credentials" in msg:
            return None, None, "Invalid email or password."
        if "Email not confirmed" in msg:
            return None, None, "Please verify your email before signing in."
        logger.warning("Login failed: %s", e)
        log_error("AUTH_ERROR", f"Login failed: {msg}", page="Login")
        return None, None, f"Login failed: {msg}"


def signup_email_password(email, password, metadata=None):
    """Create a new account with email/password.

    Returns (client, user, None) on success or (None, None, error_message) on failure.
    """
    try:
        client = init_auth_client()
        options = {"email": email, "password": password}
        if metadata:
            options["options"] = {"data": metadata}
        resp = client.auth.sign_up(options)
        user = resp.user
        if user and user.identities and len(user.identities) == 0:
            return None, None, "An account with this email already exists."
        return client, user, None
    except Exception as e:
        msg = str(e)
        if "already registered" in msg.lower():
            return None, None, "An account with this email already exists."
        logger.warning("Signup failed: %s", e)
        log_error("AUTH_ERROR", f"Signup failed: {msg}", page="Signup")
        return None, None, f"Signup failed: {msg}"


def get_google_oauth_url():
    """Generate a Supabase OAuth URL for Google sign-in (implicit flow).

    Returns the authorization URL string, or None on error.
    """
    try:
        client = init_auth_client()
        redirect_url = _get_secret("OAUTH_REDIRECT_URL") or "http://localhost:8501"
        resp = client.auth.sign_in_with_oauth(
            {
                "provider": "google",
                "options": {
                    "redirect_to": redirect_url,
                },
            }
        )
        return resp.url
    except Exception as e:
        logger.warning("Google OAuth URL generation failed: %s", e)
        return None


def inject_oauth_fragment_handler():
    """Inject JavaScript that captures OAuth tokens from the URL fragment.

    Supabase implicit flow returns tokens in the URL hash (#access_token=...),
    which Streamlit cannot read. This JS converts them to query parameters
    and reloads the page so handle_oauth_callback() can pick them up.
    Must be called early, before render_login_page().
    """
    import streamlit.components.v1 as components
    components.html("""
    <script>
        const hash = window.parent.location.hash;
        if (hash && hash.includes('access_token')) {
            const params = new URLSearchParams(hash.substring(1));
            const access_token = params.get('access_token');
            const refresh_token = params.get('refresh_token');
            if (access_token && refresh_token) {
                const url = new URL(window.parent.location.href);
                url.hash = '';
                url.searchParams.set('access_token', access_token);
                url.searchParams.set('refresh_token', refresh_token);
                window.parent.location.href = url.toString();
            }
        }
    </script>
    """, height=0)


def handle_oauth_callback():
    """Check for OAuth tokens in query params and restore the session.

    The implicit flow puts tokens in the URL fragment (#), which our
    inject_oauth_fragment_handler() JS converts to query parameters (?).
    Returns (client, user) on success or (None, None) if no callback present.
    """
    params = st.query_params

    access_token = params.get("access_token")
    refresh_token = params.get("refresh_token")

    if not access_token or not refresh_token:
        return None, None

    try:
        client = init_auth_client()
        client.auth.set_session(access_token, refresh_token)
        user = client.auth.get_user().user
        st.query_params.clear()
        return client, user
    except Exception as e:
        logger.warning("OAuth session restore failed: %s", e)
        log_error("AUTH_ERROR", f"OAuth callback failed: {e}", page="Login")
        st.query_params.clear()
        return None, None


def save_session_to_browser(client):
    """Store Supabase refresh token in browser localStorage for persistent login."""
    try:
        session = client.auth.get_session()
        if session and session.refresh_token:
            st.html(f"""
            <script>
                localStorage.setItem('lt_refresh_token', '{session.refresh_token}');
            </script>
            """, unsafe_allow_javascript=True)
    except Exception:
        pass


def clear_browser_session():
    """Remove stored tokens from browser localStorage."""
    st.html("""
    <script>
        localStorage.removeItem('lt_refresh_token');
    </script>
    """, unsafe_allow_javascript=True)


def inject_remember_me_handler():
    """Check localStorage for a stored refresh token and pass it via query param.

    Must be called early, before render_login_page().
    """
    st.html("""
    <script>
        const url = new URL(window.location.href);
        if (!url.searchParams.has('remember_token') && !url.searchParams.has('access_token')) {
            const token = localStorage.getItem('lt_refresh_token');
            if (token) {
                url.searchParams.set('remember_token', token);
                window.location.href = url.toString();
            }
        }
    </script>
    """, unsafe_allow_javascript=True)


def handle_remember_me():
    """Restore session from stored refresh token in query params.

    Returns (client, user) on success or (None, None) if not present/failed.
    """
    params = st.query_params
    remember_token = params.get("remember_token")
    if not remember_token:
        return None, None

    try:
        client = init_auth_client()
        client.auth.refresh_session(remember_token)
        user = client.auth.get_user().user
        st.query_params.clear()
        return client, user
    except Exception as e:
        logger.warning("Remember-me session restore failed: %s", e)
        log_error("AUTH_ERROR", f"Remember-me restore failed: {e}", page="Login")
        st.query_params.clear()
        # Token is invalid/expired — clear it from browser
        clear_browser_session()
        return None, None


def logout():
    """Sign out and clear all session state."""
    client = st.session_state.get("supabase_client")
    if client:
        try:
            client.auth.sign_out()
        except Exception:
            pass
    clear_browser_session()
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def render_login_page():
    """Render the full login/signup UI with hero landing page."""

    # ── Load logo ──
    _logo_b64 = None
    try:
        from assets.logo_b64 import LOGO_B64
        _logo_b64 = LOGO_B64
    except ImportError:
        pass

    _logo_html = (
        f'<img src="data:image/png;base64,{_logo_b64}" '
        f'style="width:80px;margin-bottom:24px;filter:drop-shadow(0 2px 8px rgba(0,0,0,0.1))" />'
    ) if _logo_b64 else ''

    # ── Full landing page via single markdown block ──
    st.markdown(
        f"""
<style>
/* Hero */
.lt-hero {{
    text-align: center;
    padding: 48px 24px 32px;
    max-width: 640px;
    margin: 0 auto;
}}
.lt-hero h1 {{
    font-size: 2.6rem;
    font-weight: 800;
    line-height: 1.12;
    letter-spacing: -0.02em;
    margin: 0 0 16px;
}}
.lt-hero h1 .accent {{
    background: linear-gradient(135deg, #81b29a, #5a9178);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}
.lt-hero .subtitle {{
    font-size: 1.05rem;
    color: #6e6e73;
    line-height: 1.65;
    margin: 0 auto 36px;
    max-width: 480px;
}}
/* Feature grid */
.lt-features {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    max-width: 600px;
    margin: 0 auto 28px;
}}
.lt-feat {{
    text-align: center;
    padding: 16px 8px;
    border-radius: 12px;
    border: 1px solid rgba(129,178,154,0.2);
    background: rgba(129,178,154,0.04);
    transition: border-color 0.2s, background 0.2s;
}}
.lt-feat:hover {{
    border-color: rgba(129,178,154,0.45);
    background: rgba(129,178,154,0.08);
}}
.lt-feat .icon {{
    font-size: 1.3rem;
    display: block;
    margin-bottom: 6px;
    color: #81b29a;
}}
.lt-feat .label {{
    font-size: 0.78rem;
    font-weight: 600;
    color: #48484a;
    letter-spacing: 0.01em;
}}
/* Trust bar */
.lt-trust {{
    display: flex;
    justify-content: center;
    gap: 20px;
    flex-wrap: wrap;
    margin-bottom: 8px;
}}
.lt-trust span {{
    font-size: 0.78rem;
    color: #8e8e93;
    display: inline-flex;
    align-items: center;
    gap: 5px;
}}
/* Dark mode overrides */
@media (prefers-color-scheme: dark) {{
    .lt-hero h1 {{ color: #f5f5f7; }}
    .lt-hero h1 .accent {{
        background: linear-gradient(135deg, #81b29a, #a8d5ba);
        -webkit-background-clip: text;
        background-clip: text;
    }}
    .lt-hero .subtitle {{ color: #98989d; }}
    .lt-feat {{ border-color: rgba(129,178,154,0.15); background: rgba(129,178,154,0.05); }}
    .lt-feat:hover {{ border-color: rgba(129,178,154,0.35); background: rgba(129,178,154,0.10); }}
    .lt-feat .label {{ color: #d1d1d6; }}
    .lt-trust span {{ color: #8e8e93; }}
}}
/* Responsive */
@media (max-width: 500px) {{
    .lt-features {{ grid-template-columns: repeat(2, 1fr); }}
    .lt-hero h1 {{ font-size: 2rem; }}
}}
</style>

<div class="lt-hero">
    {_logo_html}
    <h1>Track your options.<br><span class="accent">Optimize your income.</span></h1>
    <p class="subtitle">
        Connect your Tastytrade or IBKR account and get instant insights
        into your wheel strategy, P&amp;L, and portfolio performance.
    </p>
    <div class="lt-features">
        <div class="lt-feat"><span class="icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#81b29a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></span><span class="label">Real-time portfolio</span></div>
        <div class="lt-feat"><span class="icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#81b29a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg></span><span class="label">Wheel tracking</span></div>
        <div class="lt-feat"><span class="icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#81b29a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg></span><span class="label">P&amp;L reports</span></div>
        <div class="lt-feat"><span class="icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#81b29a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></span><span class="label">DCF valuations</span></div>
    </div>
    <div class="lt-trust">
        <span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#81b29a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Read-only access</span>
        <span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#81b29a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg> Tastytrade &amp; IBKR</span>
        <span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#81b29a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> Free to use</span>
    </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # ── Login form ──
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        tab_login, tab_signup = st.tabs(["Sign in", "Create account"])

        with tab_login:
            with st.form("login_form"):
                email = st.text_input("Email", key="login_email")
                password = st.text_input("Password", type="password", key="login_password")
                remember = st.checkbox("Keep me logged in", value=True, key="login_remember")
                submitted = st.form_submit_button("Sign In", use_container_width=True, type="primary")
            if submitted and email and password:
                with st.spinner("Signing in..."):
                    client, user, error = login_email_password(email, password)
                if error:
                    st.error(error)
                else:
                    st.session_state["supabase_client"] = client
                    st.session_state["user"] = {"id": str(user.id), "email": user.email}
                    if remember:
                        st.session_state["_save_remember_token"] = True
                    st.rerun()

        with tab_signup:
            with st.form("signup_form"):
                _s1, _s2 = st.columns(2)
                with _s1:
                    first_name = st.text_input("First name", key="signup_first_name")
                with _s2:
                    last_name = st.text_input("Last name", key="signup_last_name")
                _s3, _s4 = st.columns(2)
                with _s3:
                    title = st.selectbox("Title", ["Mr", "Mrs", "Ms", "Mx"], key="signup_title")
                with _s4:
                    date_of_birth = st.date_input("Date of birth", value=None, min_value=date(1920, 1, 1), format="DD/MM/YYYY", key="signup_dob")
                country = st.selectbox(
                    "Country",
                    [
                        "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Argentina",
                        "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
                        "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin",
                        "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil",
                        "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia", "Cameroon",
                        "Canada", "Cape Verde", "Central African Republic", "Chad", "Chile",
                        "China", "Colombia", "Comoros", "Congo", "Costa Rica", "Croatia",
                        "Cuba", "Cyprus", "Czech Republic", "Denmark", "Djibouti",
                        "Dominican Republic", "East Timor", "Ecuador", "Egypt", "El Salvador",
                        "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia",
                        "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany",
                        "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guinea-Bissau",
                        "Guyana", "Haiti", "Honduras", "Hungary", "Iceland", "India",
                        "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Ivory Coast",
                        "Jamaica", "Japan", "Jordan", "Kazakhstan", "Kenya", "Kiribati",
                        "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho",
                        "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg",
                        "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta",
                        "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia",
                        "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique",
                        "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands", "New Zealand",
                        "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia",
                        "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama",
                        "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland",
                        "Portugal", "Qatar", "Romania", "Russia", "Rwanda",
                        "Saint Kitts and Nevis", "Saint Lucia",
                        "Saint Vincent and the Grenadines", "Samoa", "San Marino",
                        "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia",
                        "Seychelles", "Sierra Leone", "Singapore", "Slovakia", "Slovenia",
                        "Solomon Islands", "Somalia", "South Africa", "South Korea",
                        "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden",
                        "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand",
                        "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey",
                        "Turkmenistan", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates",
                        "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu",
                        "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe",
                    ],
                    index=None,
                    placeholder="Select your country",
                    key="signup_country",
                )
                email = st.text_input("Email", key="signup_email")
                password = st.text_input("Password", type="password", key="signup_password")
                password2 = st.text_input("Confirm password", type="password", key="signup_password2")
                submitted = st.form_submit_button("Create Account", use_container_width=True, type="primary")
            if submitted:
                if not first_name or not last_name:
                    st.error("Please fill in your name.")
                elif not email or not password:
                    st.error("Please fill in all fields.")
                elif password != password2:
                    st.error("Passwords do not match.")
                elif len(password) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    metadata = {
                        "title": title,
                        "first_name": first_name,
                        "last_name": last_name,
                        "date_of_birth": date_of_birth.isoformat() if date_of_birth else None,
                        "country": country,
                    }
                    with st.spinner("Creating account..."):
                        client, user, error = signup_email_password(email, password, metadata)
                    if error:
                        st.error(error)
                    else:
                        st.success("Account created! Please check your email to verify, then sign in.")

        st.markdown(
            '<p style="text-align: center; font-size: 0.75rem; color: #86868b; margin-top: 16px;">'
            'By signing in you agree to our '
            '<a href="?_account_page=Terms+of+Service" target="_self" style="color: #86868b;">Terms of Service</a>'
            ' and '
            '<a href="?_account_page=Privacy+Policy" target="_self" style="color: #86868b;">Privacy Policy</a>'
            '</p>',
            unsafe_allow_html=True,
        )
