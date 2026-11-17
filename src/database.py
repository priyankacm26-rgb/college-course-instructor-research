
"""Supabase connection and client workspace data access."""

import streamlit as st
from supabase import create_client, Client


def get_supabase_client() -> Client:
    """Create an authenticated Supabase client."""

    supabase = create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"]
    )

    access_token = st.session_state.get("access_token")
    refresh_token = st.session_state.get("refresh_token")

    if access_token and refresh_token:
        supabase.auth.set_session(
            access_token,
            refresh_token
        )

    return supabase


def save_auth_session(auth_response):
    """Save authentication tokens in Streamlit session state."""

    st.session_state["access_token"] = (
        auth_response.session.access_token
    )
    st.session_state["refresh_token"] = (
        auth_response.session.refresh_token
    )
    st.session_state["user_id"] = auth_response.user.id
    st.session_state["user_email"] = auth_response.user.email
    st.session_state["authenticated"] = True


def clear_auth_session():
    """Clear the local Streamlit authentication state."""

    keys = [
        "access_token",
        "refresh_token",
        "user_id",
        "user_email",
        "authenticated",
        "active_client_id",
    ]

    for key in keys:
        st.session_state.pop(key, None)


def get_user_workspaces(supabase, user_id: str):
    """Return only workspaces where the user has membership."""

    response = (
        supabase.table("client_members")
        .select("client_id, role, clients(id, name)")
        .eq("user_id", user_id)
        .execute()
    )

    return response.data or []


def get_client_colleges(supabase, client_id: str):
    """Load colleges belonging to the selected client."""

    response = (
        supabase.table("colleges")
        .select("*")
        .eq("client_id", client_id)
        .order("name")
        .execute()
    )

    return response.data or []


def get_client_portals(supabase, client_id: str):
    """Load portal configurations belonging to the selected client."""

    response = (
        supabase.table("college_portals")
        .select("*")
        .eq("client_id", client_id)
        .execute()
    )

    return response.data or []


def get_client_portals_for_college(
    supabase,
    client_id: str,
    college_id: str
):
    """Load portal links for one college within a client workspace."""

    response = (
        supabase.table("college_portals")
        .select("*")
        .eq("client_id", client_id)
        .eq("college_id", college_id)
        .execute()
    )

    return response.data or []


def get_client_terms(
    supabase,
    client_id: str,
    college_id: str = None
):
    """Load terms for the selected client or a specific college."""

    query = (
        supabase.table("terms")
        .select("*")
        .eq("client_id", client_id)
    )

    if college_id:
        query = query.eq("college_id", college_id)

    response = query.order("term_name").execute()

    return response.data or []


def get_client_courses(
    supabase,
    client_id: str,
    college_id: str = None
):
    """Load course records for the selected client."""

    query = (
        supabase.table("courses")
        .select("*")
        .eq("client_id", client_id)
    )

    if college_id:
        query = query.eq("college_id", college_id)

    response = query.execute()

    return response.data or []


def get_client_subjects(
    supabase,
    client_id: str,
    college_id: str = None
):
    """Load subjects for the selected client."""

    query = (
        supabase.table("subjects")
        .select("*")
        .eq("client_id", client_id)
    )

    if college_id:
        query = query.eq("college_id", college_id)

    response = query.execute()

    return response.data or []


def get_client_configuration(supabase, client_id: str):
    """Load the complete workbook snapshot for one client."""

    response = (
        supabase.table("client_configurations")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    )

    if response.data:
        return response.data[0]

    return None
