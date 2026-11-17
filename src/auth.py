
"""Authentication helpers for the multi-client Streamlit application."""

import streamlit as st

from src.database import (
    get_supabase_client,
    save_auth_session,
    clear_auth_session,
)


def login_user(email: str, password: str):
    """Authenticate a user using Supabase email/password login."""

    supabase = get_supabase_client()

    response = supabase.auth.sign_in_with_password({
        "email": email.strip(),
        "password": password,
    })

    if not response.user or not response.session:
        raise ValueError("Login failed. Please check your credentials.")

    save_auth_session(response)

    return supabase, response.user


def get_current_user():
    """Restore and validate the current user's authentication session."""

    if not st.session_state.get("authenticated"):
        return None, None

    try:
        supabase = get_supabase_client()

        # Retrieve the current session, allowing the SDK to refresh it.
        session = supabase.auth.get_session()

        if not session:
            clear_auth_session()
            return None, None

        # Keep the latest tokens after possible refresh.
        st.session_state["access_token"] = session.access_token
        st.session_state["refresh_token"] = session.refresh_token

        user_response = supabase.auth.get_user(session.access_token)

        if not user_response.user:
            clear_auth_session()
            return None, None

        st.session_state["user_id"] = user_response.user.id
        st.session_state["user_email"] = user_response.user.email

        return supabase, user_response.user

    except Exception:
        clear_auth_session()
        return None, None


def logout_user():
    """Sign out and clear the local Streamlit session."""

    try:
        supabase = get_supabase_client()
        supabase.auth.sign_out()
    except Exception:
        pass

    clear_auth_session()
