import streamlit as st
import pandas as pd
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse
from src.auth import login_user, get_current_user, logout_user
from src.database import (
    get_user_workspaces,
    get_client_colleges,
    get_client_portals,
    get_client_portals_for_college,
    get_client_terms,
)

from src.research import Researcher
from src.exporter import build_workbook

st.set_page_config(
    page_title="College Research Agent",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root { --brand: #2457C5; --brand-dark: #183F98; --ink: #172033; --muted: #667085; --line: #E5EAF2; --surface: #FFFFFF; --canvas: #F5F7FB; }
    [data-testid="stAppViewContainer"] { background: var(--canvas); }
    [data-testid="stHeader"] { background: rgba(245,247,251,.92); }
    [data-testid="stMainBlockContainer"] { max-width: 1180px; padding-top: 1.1rem; padding-bottom: 2rem; }
    [data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid var(--line); }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: #344054; }
    h1, h2, h3 { color: var(--ink) !important; letter-spacing: -0.025em; }
    h1 { font-size: 2.05rem !important; font-weight: 750 !important; }
    h2 { font-size: 1.42rem !important; font-weight: 700 !important; }
    h3 { font-size: 1.08rem !important; font-weight: 650 !important; }
    p, label, [data-testid="stCaptionContainer"] { color: #475467; }
    [data-testid="stTabs"] [role="tablist"] { gap: 5px; border-bottom: 1px solid var(--line); }
    [data-testid="stTabs"] button[role="tab"] { border-radius: 8px 8px 0 0; padding: .58rem .82rem; font-size: .92rem; font-weight: 650; color: #475467; }
    [data-testid="stTabs"] button[aria-selected="true"] { color: var(--brand) !important; border-bottom: 3px solid var(--brand) !important; background: #EEF4FF; }
    [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input, [data-testid="stTextArea"] textarea, [data-testid="stSelectbox"] [data-baseweb="select"] > div { border: 1px solid #D7DFEA; border-radius: 9px; background: #FFFFFF; min-height: 42px; color: #172033 !important; -webkit-text-fill-color: #172033 !important; }
    [data-testid="stTextInput"] input::placeholder, [data-testid="stNumberInput"] input::placeholder, [data-testid="stTextArea"] textarea::placeholder { color: #98A2B3 !important; -webkit-text-fill-color: #98A2B3 !important; opacity: 1; }
    [data-testid="stSelectbox"] [data-baseweb="select"] *, [data-testid="stMultiSelect"] [data-baseweb="select"] * { color: #172033 !important; }
    [data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus, [data-testid="stTextArea"] textarea:focus { border-color: var(--brand); box-shadow: 0 0 0 2px rgba(36,87,197,.12); }
    /* Consistent blue theme: high-contrast blue buttons with white labels. */
    [data-testid="stButton"] button, [data-testid="stDownloadButton"] button {
        border-radius: 9px !important;
        min-height: 42px;
        font-weight: 650 !important;
        background: #2457C5 !important;
        border: 1px solid #2457C5 !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
    }
    [data-testid="stButton"] button:hover, [data-testid="stDownloadButton"] button:hover {
        background: #183F98 !important;
        border-color: #183F98 !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
    }
    [data-testid="stButton"] button p, [data-testid="stDownloadButton"] button p,
    [data-testid="stButton"] button span, [data-testid="stDownloadButton"] button span {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
    }
    [data-testid="stButton"] button:disabled, [data-testid="stDownloadButton"] button:disabled {
        background: #A9BFEF !important;
        border-color: #A9BFEF !important;
        color: #FFFFFF !important;
        opacity: .8;
    }
    [data-testid="stAlert"] { border-radius: 10px; border: 1px solid var(--line); }
    [data-testid="stExpander"] { border: 1px solid var(--line); border-radius: 10px; background: #FFFFFF; }
    [data-testid="stDataFrame"], [data-testid="stDataEditor"] { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
    [data-testid="stMetric"] { background: #FFFFFF; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 2px rgba(16,24,40,.03); }
    hr { border-color: var(--line); }
    .app-eyebrow { color: var(--brand); font-size: .76rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; margin-bottom: .35rem; }
    .app-hero { background: linear-gradient(115deg,#FFFFFF 0%,#F0F5FF 100%); border: 1px solid #E1E9F7; border-radius: 14px; padding: 18px 22px; margin: .25rem 0 1.1rem 0; box-shadow: 0 3px 12px rgba(16,24,40,.03); }
    .app-hero-title { color: #172033; font-size: 1.6rem; line-height: 1.2; font-weight: 780; letter-spacing: -.035em; margin: 0; }
    .app-hero-sub { color: #667085; font-size: .92rem; margin-top: .4rem; margin-bottom: 0; }
    .section-note { color: #667085; margin-top: -.35rem; margin-bottom: 1rem; }
    @media (max-width: 760px) { [data-testid="stMainBlockContainer"] { padding: 1rem .8rem 2rem .8rem; } .app-hero { padding: 18px; } .app-hero-title { font-size: 1.45rem; } }
    </style>
    """,
    unsafe_allow_html=True,
)

# ================= AUTHENTICATION GATE =================

st.session_state.setdefault("authenticated", False)

if not st.session_state.get("authenticated"):
    # Compact, centered login layout; the full dashboard keeps its wide layout.
    st.markdown(
        """
        <style>
        [data-testid="stMainBlockContainer"] {
            max-width: 760px !important;
            padding-top: 2.5rem !important;
            padding-bottom: 2rem !important;
        }
        [data-testid="stForm"] {
            max-width: 520px;
            margin: 1rem auto 0 auto;
            padding: 1.4rem 1.6rem 1.2rem 1.6rem;
            background: #FFFFFF;
            border: 1px solid #E5EAF2;
            border-radius: 14px;
            box-shadow: 0 8px 28px rgba(16,24,40,.06);
        }
        [data-testid="stForm"] [data-testid="stTextInput"] input {
            min-height: 40px;
        }
        [data-testid="stForm"] [data-testid="stFormSubmitButton"] button {
            min-height: 40px;
            padding: 0 1.25rem;
            border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='app-hero'><div class='app-eyebrow'>College Research AI</div>"
        "<p class='app-hero-title'>Welcome back</p>"
        "<p class='app-hero-sub'>Sign in to research college schedules, courses and faculty evidence.</p></div>",
        unsafe_allow_html=True,
    )
    st.subheader("Sign in to your workspace")

    with st.form("login_form"):
        email = st.text_input("Email address")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In", type="primary")

    if submitted:
        try:
            login_user(email, password)
            st.success("Login successful!")
            st.rerun()
        except Exception as exc:
            st.error(f"Login failed: {exc}")

    st.stop()


# Validate the existing session
supabase, current_user = get_current_user()

if not current_user:
    st.warning("Your session has expired. Please sign in again.")
    st.stop()

# Load the workspaces assigned to this user
workspaces = get_user_workspaces(
    supabase,
    current_user.id
)

if not workspaces:
    st.error("No client workspace is assigned to this account.")
    st.stop()

# Workspace selection
st.markdown("<div class='app-eyebrow'>Workspace</div>", unsafe_allow_html=True)
def workspace_name(item):
    relation = item.get("clients")
    if isinstance(relation, dict):
        return relation.get("name", "Unnamed Workspace")
    if isinstance(relation, list) and relation:
        return relation[0].get("name", "Unnamed Workspace")
    return "Unnamed Workspace"


workspace_options = {
    item["client_id"]: workspace_name(item)
    for item in workspaces
}

selected_client_id = st.selectbox(
    "Select Client Workspace",
    options=list(workspace_options.keys()),
    format_func=lambda client_id: workspace_options[client_id],
    key="active_client_id",
)

# Sidebar logout
with st.sidebar:
    st.write(f"Signed in: {current_user.email}")
    if st.button("Logout", use_container_width=True):
        logout_user()
        st.rerun()

# =======================================================


st.markdown(
    "<div class='app-hero'><div class='app-eyebrow'>College Research AI</div>"
    "<p class='app-hero-title'>College Research Dashboard</p>"
    "<p class='app-hero-sub'>Research official course schedules, verify instructors, and organize supporting evidence in one workspace.</p></div>",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Research Settings")
    timeout = st.number_input(
        "Page timeout (seconds)", min_value=10, max_value=120, value=60
    )
    use_browser = st.checkbox(
        "Use Playwright for JavaScript pages", value=True
    )
    st.info(
        "Playwright is recommended for dynamic registration systems. "
        "Some college portals may require a dedicated adapter."
    )

    st.divider()
    st.subheader("Workspace College Configuration")

    try:
        client_colleges = get_client_colleges(supabase, selected_client_id)
        st.success(f"Loaded {len(client_colleges)} college(s) from this workspace.")
    except Exception as exc:
        client_colleges = []
        st.error(f"Could not load workspace colleges: {exc}")

    st.divider()
    st.markdown("### Supported research")
    st.write(
        "• Course schedule research\n"
        "• Instructor profile research\n"
        "• Evidence collection\n"
        "• Excel export"
    )

session_defaults = {
    "course_rows": [],
    "instructor_rows": [],
    "evidence_rows": [],
    "course_context": "",
    "instructor_context": "",
}
for key, value in session_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

researcher = Researcher(timeout=int(timeout), use_browser=use_browser)


def save_research_run(
    run_type,
    college_id,
    college_name,
    term_name,
    status,
    inputs,
    records=None,
    evidence=None,
    context="",
    error="",
):
    """Persist one completed or failed research attempt for this workspace."""
    payload = {
        "run_type": run_type,
        "college_name": college_name,
        "inputs": inputs or {},
        "records": records or [],
        "evidence": evidence or [],
        "context": context or "",
        "error": error or "",
    }
    response = supabase.table("research_runs").insert({
        "client_id": selected_client_id,
        "college_id": college_id,
        "term_name": term_name or None,
        "status": status,
        "results": payload,
    }).execute()
    return response.data[0] if response.data else None


active_course_college_id = None
tabs = st.tabs([
    "📚  Course Schedule",
    "👩‍🏫  Instructor",
    "📑  Evidence & Export",
    "🕘  Research History",
    "⚙️  College Configuration",
    "🧾  Salesforce Export",
])

with tabs[0]:
    st.subheader("Course & section research")
    st.markdown("<p class='section-note'>Choose a configured college and schedule portal, then enter the course details to begin research.</p>", unsafe_allow_html=True)

    if client_colleges:
        college_by_id = {row["id"]: row for row in client_colleges}

        # Only show colleges that have both a schedule URL and at least one term.
        # Fetch workspace-wide configuration once instead of querying each college.
        try:
            workspace_portals = get_client_portals(supabase, selected_client_id)
            workspace_terms = get_client_terms(supabase, selected_client_id)

            colleges_with_schedule = {
                row.get("college_id")
                for row in workspace_portals
                if row.get("college_id") and (row.get("schedule_url") or "").strip()
            }
            colleges_with_terms = {
                row.get("college_id")
                for row in workspace_terms
                if row.get("college_id") and (row.get("term_name") or "").strip()
            }

            configured_college_ids = [
                college_id
                for college_id in college_by_id
                if college_id in colleges_with_schedule
                and college_id in colleges_with_terms
            ]
        except Exception as exc:
            configured_college_ids = []
            st.error(f"Could not determine configured colleges: {exc}")

        if configured_college_ids:
            selected_college_id = st.selectbox(
                "Choose a configured college",
                options=configured_college_ids,
                format_func=lambda college_id: college_by_id[college_id]["name"],
                key="workspace_college_choice",
            )

            selected_college = college_by_id[selected_college_id]
            active_course_college_id = selected_college_id

            try:
                selected_portals = get_client_portals_for_college(
                    supabase,
                    selected_client_id,
                    selected_college_id,
                )
                selected_terms = get_client_terms(
                    supabase,
                    selected_client_id,
                    selected_college_id,
                )
            except Exception as exc:
                selected_portals = []
                selected_terms = []
                st.error(f"Could not load college links or terms: {exc}")

            schedule_candidates = [
                portal for portal in selected_portals
                if portal.get("schedule_url")
                and (
                    (portal.get("portal_name") or "").strip().lower() == "cs"
                    or any(
                        keyword in (portal.get("portal_name") or "").lower()
                        for keyword in (
                            "class schedule",
                            "registration portal",
                            "self-service",
                            "schedule search",
                        )
                    )
                )
            ]

            # Prefer a schedule-specific link; fall back to the college homepage
            # only when no schedule/registration portal URL is configured.
            if not schedule_candidates:
                schedule_candidates = [
                    portal for portal in selected_portals
                    if portal.get("schedule_url")
                ]

            portal_labels = {
                index: (
                    f'{portal.get("portal_name") or "Portal"} — '
                    f'{portal.get("schedule_url")}'
                )
                for index, portal in enumerate(schedule_candidates)
            }

            selected_portal_index = None
            if portal_labels:
                selected_portal_index = st.selectbox(
                    "Official schedule / registration link",
                    options=list(portal_labels.keys()),
                    format_func=lambda index: portal_labels[index],
                    key=f"workspace_portal_{selected_college_id}",
                )
            else:
                st.warning("No portal URL is configured for this college.")

            term_names = [
                row.get("term_name")
                for row in selected_terms
                if row.get("term_name")
            ]
            selected_term = None
            if term_names:
                selected_term = st.selectbox(
                    "Target term",
                    options=term_names,
                    key=f"workspace_term_{selected_college_id}",
                )
            else:
                st.warning("No target terms are configured for this college.")

            # Use a callback so Streamlit updates the form widget state before
            # the next script run. This reliably fills the fields below.
            def apply_workspace_settings(college_name, schedule_url, term_name):
                st.session_state["c_college"] = college_name
                st.session_state["c_schedule"] = schedule_url
                st.session_state["c_term"] = term_name or ""
                st.session_state["workspace_settings_applied"] = True

            st.button(
                "Apply college settings",
                key="apply_workspace_college",
                type="primary",
                use_container_width=False,
                disabled=(selected_portal_index is None),
                on_click=apply_workspace_settings,
                args=(
                    selected_college["name"],
                    (
                        schedule_candidates[selected_portal_index]["schedule_url"]
                        if selected_portal_index is not None else ""
                    ),
                    selected_term or "",
                ),
            )

            if st.session_state.pop("workspace_settings_applied", False):
                st.success(
                    "College, schedule URL and term loaded into the research form."
                )

            with st.expander("View configured portal links"):
                if selected_portals:
                    st.dataframe(
                        pd.DataFrame(selected_portals),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("No portal links are stored for this college.")

            with st.expander("View configured target terms"):
                if selected_terms:
                    st.dataframe(
                        pd.DataFrame(selected_terms),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("No target terms are stored for this college.")
        else:
            st.warning(
                "No colleges have both a schedule portal and academic term configured. "
                "Open College Configuration to set them up."
            )
    else:
        st.warning(
            "No colleges are configured for this workspace. "
            "Ask a workspace admin to import the college configuration."
        )

    col1, col2 = st.columns(2)
    with col1:
        college = st.text_input(
            "College / University",
            placeholder="University name",
            key="c_college",
        )
        term = st.text_input(
            "Term", placeholder="Fall 2026", key="c_term"
        )
        schedule_url = st.text_input(
            "Official Class Schedule URL",
            placeholder="https://college.edu/class-schedule",
            key="c_schedule",
        )
    with col2:
        srn = st.text_input("CRN / Section ID (Optional)", key="c_srn")
        course_code = st.text_input(
            "Course Number (Optional)",
            placeholder="Leave blank to search all courses in the subject where supported",
            key="c_code",
            help="Leave blank to retrieve all courses in the selected subject and term where the college portal supports it. Enter a number to narrow the search.",
        )
        subject = st.text_input(
            "Subject / Department",
            placeholder="FYS or First Year Seminar",
            key="c_subject",
        )
        instructor_name = st.text_input(
            "Instructor Name (Optional)", key="c_instructor"
        )

    st.divider()
    if st.button(
        "Start course research",
        type="primary",
        use_container_width=True,
        key="research_course_button",
    ):
        required = {
            "College / University": college,
            "Term": term,
            "Official Class Schedule URL": schedule_url,
            "Subject / Department": subject,
        }
        missing = [field for field, value in required.items() if not value.strip()]

        if missing:
            st.error("Please complete: " + ", ".join(missing))
        else:
            try:
                with st.spinner("Opening schedule and searching courses..."):
                    result = researcher.research_course(
                        college=college.strip(),
                        term=term.strip(),
                        schedule_url=schedule_url.strip(),
                        srn=srn.strip(),
                        course_code=course_code.strip(),
                        subject=subject.strip(),
                        instructor=instructor_name.strip(),
                    )
                st.session_state.course_rows = result["course"]
                st.session_state.evidence_rows.extend(result["evidence"])
                st.session_state.course_context = result.get("context", "")
                try:
                    saved_run = save_research_run(
                        run_type="course",
                        college_id=active_course_college_id,
                        college_name=college.strip(),
                        term_name=term.strip(),
                        status="completed",
                        inputs={
                            "college": college.strip(),
                            "term": term.strip(),
                            "schedule_url": schedule_url.strip(),
                            "crn_or_section_id": srn.strip(),
                            "course_number": course_code.strip(),
                            "subject": subject.strip(),
                            "instructor": instructor_name.strip(),
                        },
                        records=result["course"],
                        evidence=result["evidence"],
                        context=result.get("context", ""),
                    )
                    if saved_run:
                        st.success(
                            f"Course search completed and saved. Run ID: {saved_run.get('id', 'saved')}"
                        )
                    else:
                        st.success("Course search completed. Review the results below.")
                        st.warning("Research results were not confirmed as saved in Supabase.")
                except Exception as save_exc:
                    st.success("Course search completed. Review the results below.")
                    st.warning(f"Could not save this run to Supabase: {save_exc}")
            except Exception as exc:
                try:
                    save_research_run(
                        run_type="course",
                        college_id=active_course_college_id,
                        college_name=college.strip(),
                        term_name=term.strip(),
                        status="failed",
                        inputs={
                            "college": college.strip(),
                            "term": term.strip(),
                            "schedule_url": schedule_url.strip(),
                            "crn_or_section_id": srn.strip(),
                            "course_number": course_code.strip(),
                            "subject": subject.strip(),
                            "instructor": instructor_name.strip(),
                        },
                        error=str(exc),
                    )
                except Exception:
                    pass
                st.error(f"Course research failed: {exc}")
                st.info(
                    "If the portal uses a custom schedule interface, it may "
                    "need a dedicated adapter. Check the source URL and inputs."
                )

    if st.session_state.course_rows:
        st.divider()
        st.subheader("Course Data Review")
        st.caption("Verify and edit values against the official source.")

        # Instructor names from course results are actionable. Selecting a name
        # pre-fills the Instructor tab and attempts official-source discovery.
        course_instructors = []
        for course_row in st.session_state.course_rows:
            raw_names = course_row.get("Instructor") or course_row.get("Instructor Name") or ""
            if isinstance(raw_names, str):
                names = [part.strip() for part in raw_names.replace(";", ",").split(",") if part.strip()]
                # Preserve common "Last, First" names as a single name when
                # the source stores that format in one field.
                if raw_names.count(",") == 1 and len(names) == 2 and len(names[0].split()) == 1 and len(names[1].split()) == 1:
                    names = [raw_names.strip()]
            elif isinstance(raw_names, (list, tuple)):
                names = [str(part).strip() for part in raw_names if str(part).strip()]
            else:
                names = []
            for name in names:
                if name.casefold() not in {x.casefold() for x in course_instructors}:
                    course_instructors.append(name)

        if course_instructors:
            st.markdown("**Instructor verification — click a name to research it**")
            with st.container(key="instructor_name_buttons"):
                instructor_cols = st.columns(min(3, len(course_instructors)))
                for idx, name in enumerate(course_instructors):
                    with instructor_cols[idx % len(instructor_cols)]:
                        if st.button(name, key=f"course_instructor_pick_{idx}", use_container_width=True):
                            st.session_state["i_college"] = st.session_state.get("c_college", "")
                            st.session_state["i_name"] = name
                            st.session_state["i_department"] = (
                                st.session_state.course_rows[0].get("Course Subject / Department Name")
                                or st.session_state.course_rows[0].get("Course Discipline")
                                or st.session_state.get("c_subject", "")
                            )
                            st.session_state["i_faculty_url"] = ""
                            st.session_state["i_department_url"] = ""
                            st.session_state["i_division_url"] = ""
                            st.session_state["i_primary"] = "Unknown"
                            st.session_state["auto_instructor_request"] = True
                            st.rerun()

        course_df = pd.DataFrame(st.session_state.course_rows)
        edited = st.data_editor(
            course_df, num_rows="dynamic", use_container_width=True,
            key="course_editor",
        )
        st.session_state.course_rows = edited.to_dict("records")
        with st.expander("View Registration Search Results / Source Text"):
            st.text(st.session_state.course_context[:20000])

    # Auto-run the same instructor research method used by the Instructor tab.
    # UNCW, Chico State and Central Michigan retain their dedicated lookups;
    # Kirkwood HUM uses its official public Arts & Humanities faculty page.
    if st.session_state.pop("auto_instructor_request", False):
        auto_college = st.session_state.get("i_college", "").strip()
        auto_name = st.session_state.get("i_name", "").strip()
        auto_department = st.session_state.get("i_department", "").strip()
        if not auto_name or not auto_college:
            st.error("The selected course row is missing the college or instructor name.")
        else:
            try:
                with st.spinner(f"Researching {auto_name} at {auto_college}..."):
                    auto_result = researcher.research_instructor(
                        college=auto_college,
                        name=auto_name,
                        department=auto_department,
                        faculty_url="",
                        department_url="",
                        division_url="",
                        primary=st.session_state.get("i_primary", "Unknown"),
                        schedule_url=st.session_state.get("c_schedule", ""),
                    )
                st.session_state.instructor_rows = [auto_result["instructor"]]
                st.session_state.evidence_rows.extend(auto_result["evidence"])
                st.session_state.instructor_context = auto_result.get("context", "")
                has_match = any(
                    "name found" in str(item.get("Evidence note", "")).casefold()
                    or "exact instructor name matched" in str(item.get("Evidence note", "")).casefold()
                    or "official peoplesearch" in str(item.get("Evidence note", "")).casefold()
                    for item in auto_result.get("evidence", [])
                )
                if has_match:
                    st.success(f"Official-source match found for {auto_name}. Review the evidence and extracted fields in the Instructor tab.")
                st.dataframe(pd.DataFrame(st.session_state.instructor_rows), use_container_width=True, hide_index=True)
                auto_college_id = next((
                    row.get("id") for row in client_colleges
                    if str(row.get("name", "")).strip().casefold() == auto_college.casefold()
                ), None)
                try:
                    save_research_run(
                        run_type="instructor", college_id=auto_college_id,
                        college_name=auto_college, term_name=None,
                        status="completed" if has_match else "needs_review",
                        inputs={"college": auto_college, "instructor_name": auto_name, "department": auto_department, "source": "course_result_click"},
                        records=[auto_result["instructor"]], evidence=auto_result["evidence"],
                        context=auto_result.get("context", ""),
                    )
                except Exception as save_exc:
                    st.warning(f"Instructor details were retrieved but could not be saved to research history: {save_exc}")
            except Exception as auto_exc:
                st.error(f"Instructor research failed for {auto_name}: {auto_exc}")

with tabs[1]:
    st.subheader("Instructor verification")
    st.markdown("<p class='section-note'>Verify instructor identity, academic role, department and publicly listed contact details.</p>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        i_college = st.text_input("College / University", key="i_college")
        i_name = st.text_input("Instructor Full Name", key="i_name")
        i_department = st.text_input(
            "Known Department / Course (Optional)", key="i_department"
        )
    with col2:
        faculty_url = st.text_input(
            "Official Faculty Directory / Profile URL", key="i_faculty_url"
        )
        department_url = st.text_input(
            "Official Department Page URL (Optional)", key="i_department_url"
        )
        division_url = st.text_input(
            "Official School / College Page URL (Optional)", key="i_division_url"
        )
    primary = st.selectbox(
        "Is this instructor primary?", ["Unknown", "TRUE", "FALSE"],
        key="i_primary",
    )

    if st.button(
        "Start instructor research",
        type="primary",
        use_container_width=True,
        key="research_instructor_button",
    ):
        if not i_college.strip():
            st.error("College / University is required.")
        elif not i_name.strip():
            st.error("Instructor full name is required.")
        elif not faculty_url.strip():
            st.error("At least one official faculty profile URL is required.")
        else:
            try:
                with st.spinner("Researching official instructor sources..."):
                    result = researcher.research_instructor(
                        college=i_college.strip(),
                        name=i_name.strip(),
                        department=i_department.strip(),
                        faculty_url=faculty_url.strip(),
                        department_url=department_url.strip(),
                        division_url=division_url.strip(),
                        primary=primary,
                    )
                st.session_state.instructor_rows = [result["instructor"]]
                st.session_state.evidence_rows.extend(result["evidence"])
                st.session_state.instructor_context = result.get("context", "")
                instructor_college_id = next(
                    (
                        row.get("id")
                        for row in client_colleges
                        if str(row.get("name", "")).strip().casefold()
                        == i_college.strip().casefold()
                    ),
                    None,
                )
                try:
                    saved_run = save_research_run(
                        run_type="instructor",
                        college_id=instructor_college_id,
                        college_name=i_college.strip(),
                        term_name=None,
                        status="completed",
                        inputs={
                            "college": i_college.strip(),
                            "instructor_name": i_name.strip(),
                            "department": i_department.strip(),
                            "faculty_url": faculty_url.strip(),
                            "department_url": department_url.strip(),
                            "division_url": division_url.strip(),
                            "is_primary": primary,
                        },
                        records=[result["instructor"]],
                        evidence=result["evidence"],
                        context=result.get("context", ""),
                    )
                    if saved_run:
                        st.success(
                            f"Instructor research completed and saved. Run ID: {saved_run.get('id', 'saved')}"
                        )
                    else:
                        st.success("Instructor research completed. Verify each field.")
                        st.warning("Research results were not confirmed as saved in Supabase.")
                except Exception as save_exc:
                    st.success("Instructor research completed. Verify each field.")
                    st.warning(f"Could not save this run to Supabase: {save_exc}")
            except Exception as exc:
                instructor_college_id = next(
                    (
                        row.get("id")
                        for row in client_colleges
                        if str(row.get("name", "")).strip().casefold()
                        == i_college.strip().casefold()
                    ),
                    None,
                )
                try:
                    save_research_run(
                        run_type="instructor",
                        college_id=instructor_college_id,
                        college_name=i_college.strip(),
                        term_name=None,
                        status="failed",
                        inputs={
                            "college": i_college.strip(),
                            "instructor_name": i_name.strip(),
                            "department": i_department.strip(),
                            "faculty_url": faculty_url.strip(),
                            "department_url": department_url.strip(),
                            "division_url": division_url.strip(),
                            "is_primary": primary,
                        },
                        error=str(exc),
                    )
                except Exception:
                    pass
                st.error(f"Instructor research failed: {exc}")

    if st.session_state.instructor_rows:
        st.divider()
        st.subheader("Instructor Data Review")
        instructor_df = pd.DataFrame(st.session_state.instructor_rows)
        edited = st.data_editor(
            instructor_df, num_rows="dynamic", use_container_width=True,
            key="instructor_editor",
        )
        st.session_state.instructor_rows = edited.to_dict("records")
        with st.expander("View Instructor Source Text"):
            st.text(st.session_state.instructor_context[:20000])

with tabs[2]:
    st.subheader("Evidence & export")
    st.markdown("<p class='section-note'>Review source links and supporting evidence, then export your verified research workbook.</p>", unsafe_allow_html=True)
    if st.session_state.evidence_rows:
        evidence_df = pd.DataFrame(
            st.session_state.evidence_rows
        ).drop_duplicates()
        st.dataframe(evidence_df, use_container_width=True, hide_index=True)
    else:
        st.info(
            "Research a course or instructor to populate the evidence register."
        )

    st.divider()
    st.subheader("Excel Export")
    col1, col2, col3 = st.columns(3)
    col1.metric("Course Records", len(st.session_state.course_rows))
    col2.metric("Instructor Records", len(st.session_state.instructor_rows))
    col3.metric("Evidence Records", len(st.session_state.evidence_rows))

    if st.button(
        "Prepare Excel workbook",
        type="primary",
        use_container_width=True,
        key="build_workbook_button",
    ):
        if not (
            st.session_state.course_rows or st.session_state.instructor_rows
        ):
            st.error(
                "Run at least one course or instructor research workflow first."
            )
        else:
            try:
                workbook_data = build_workbook(
                    st.session_state.course_rows,
                    st.session_state.instructor_rows,
                    st.session_state.evidence_rows,
                )
                st.download_button(
                    label="Download Verified Research Workbook",
                    data=workbook_data,
                    file_name="college_research_output.xlsx",
                    mime=(
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                    use_container_width=True,
                )
            except Exception as exc:
                st.error(f"Excel export failed: {exc}")

    st.divider()
    if st.button(
        "Clear This Session",
        use_container_width=True,
        key="clear_session_button",
    ):
        for key, value in session_defaults.items():
            st.session_state[key] = value
        st.rerun()

with tabs[3]:
    st.subheader("Research history")
    st.markdown(
        "<p class='section-note'>Completed and failed research attempts saved for this workspace.</p>",
        unsafe_allow_html=True,
    )
    try:
        history_response = (
            supabase.table("research_runs")
            .select("id, college_id, term_name, status, results, created_at")
            .eq("client_id", selected_client_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        history_rows = history_response.data or []
        if history_rows:
            summary_rows = []
            for item in history_rows:
                result_payload = item.get("results") or {}
                summary_rows.append({
                    "Run ID": item.get("id"),
                    "Type": result_payload.get("run_type", "unknown"),
                    "College": result_payload.get("college_name", ""),
                    "Term": item.get("term_name") or "",
                    "Status": item.get("status", ""),
                    "Created": item.get("created_at", ""),
                    "Records": len(result_payload.get("records") or []),
                })
            st.dataframe(
                pd.DataFrame(summary_rows),
                use_container_width=True,
                hide_index=True,
            )
            run_options = {
                str(item.get("id")): item
                for item in history_rows
            }
            selected_run_id = st.selectbox(
                "View saved research run",
                options=list(run_options.keys()),
                format_func=lambda run_id: (
                    f"{run_options[run_id].get('results', {}).get('run_type', 'run').title()} — "
                    f"{run_options[run_id].get('results', {}).get('college_name', 'Unknown college')} — "
                    f"{run_options[run_id].get('created_at', '')}"
                ),
                key="research_history_selection",
            )
            selected_run = run_options[selected_run_id]
            with st.expander("Saved run details", expanded=True):
                st.json(selected_run.get("results") or {})
        else:
            st.info(
                "No research runs have been saved for this workspace yet. "
                "Run a course or instructor search to create the first history record."
            )
    except Exception as history_exc:
        st.error(f"Could not load research history: {history_exc}")
        st.caption(
            "Check that the research_runs table has SELECT permission for workspace members."
        )


with tabs[4]:
    st.subheader("College portal & term configuration")
    st.markdown(
        "<p class='section-note'>Add official schedule portals and academic terms "
        "for colleges in this workspace. Existing configurations are not changed.</p>",
        unsafe_allow_html=True,
    )

    if not client_colleges:
        st.warning("No colleges are available in this workspace.")
    else:
        config_college_by_id = {row["id"]: row for row in client_colleges}
        config_college_ids = list(config_college_by_id.keys())

        config_college_id = st.selectbox(
            "Choose a college to configure",
            options=config_college_ids,
            format_func=lambda college_id: config_college_by_id[college_id]["name"],
            key="config_college_choice",
        )
        config_college = config_college_by_id[config_college_id]

        try:
            current_portals = get_client_portals_for_college(
                supabase, selected_client_id, config_college_id
            )
            current_terms = get_client_terms(
                supabase, selected_client_id, config_college_id
            )
        except Exception as exc:
            current_portals = []
            current_terms = []
            st.error(f"Could not load current configuration: {exc}")

        portal_col, term_col = st.columns(2)

        with portal_col:
            st.markdown("#### Add official schedule portal")
            with st.form("add_college_portal_form", clear_on_submit=False):
                portal_name = st.text_input(
                    "Portal name",
                    value="Class Schedule",
                    help="Example: Class Schedule, Banner Self-Service, Schedule Search",
                )
                schedule_url_input = st.text_input(
                    "Official schedule URL",
                    placeholder="https://college.edu/class-schedule",
                )
                platform_input = st.text_input(
                    "Platform (optional)",
                    placeholder="Banner, Self-Service, PeopleSoft, etc.",
                )
                portal_submitted = st.form_submit_button(
                    "Save schedule portal", type="primary", use_container_width=True
                )

            if portal_submitted:
                parsed_url = urlparse(schedule_url_input.strip())
                if not portal_name.strip():
                    st.error("Portal name is required.")
                elif parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                    st.error("Enter a valid official URL beginning with https:// or http://.")
                else:
                    try:
                        existing = (
                            supabase.table("college_portals")
                            .select("id, portal_name, schedule_url")
                            .eq("client_id", selected_client_id)
                            .eq("college_id", config_college_id)
                            .execute()
                        )
                        duplicate = any(
                            (row.get("schedule_url") or "").strip().rstrip("/")
                            == schedule_url_input.strip().rstrip("/")
                            for row in (existing.data or [])
                        )
                        if duplicate:
                            st.warning("This schedule URL is already saved for this college.")
                        else:
                            supabase.table("college_portals").insert({
                                "client_id": selected_client_id,
                                "college_id": config_college_id,
                                "portal_name": portal_name.strip(),
                                "schedule_url": schedule_url_input.strip(),
                                "platform": platform_input.strip() or None,
                                "status": "active",
                                "metadata": {
                                    "added_via": "college_configuration",
                                    "review_status": "client_supplied",
                                },
                            }).execute()
                            st.success("Schedule portal saved.")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Could not save schedule portal: {exc}")

        with term_col:
            st.markdown("#### Add academic term")
            with st.form("add_college_term_form", clear_on_submit=False):
                term_name_input = st.text_input(
                    "Term name",
                    placeholder="Fall 2026",
                )
                term_code_input = st.text_input(
                    "Term code (optional)",
                    placeholder="2026FA",
                )
                term_submitted = st.form_submit_button(
                    "Save academic term", type="primary", use_container_width=True
                )

            if term_submitted:
                if not term_name_input.strip():
                    st.error("Term name is required.")
                else:
                    try:
                        existing = (
                            supabase.table("terms")
                            .select("id, term_name")
                            .eq("client_id", selected_client_id)
                            .eq("college_id", config_college_id)
                            .execute()
                        )
                        duplicate = any(
                            (row.get("term_name") or "").strip().casefold()
                            == term_name_input.strip().casefold()
                            for row in (existing.data or [])
                        )
                        if duplicate:
                            st.warning("This academic term is already saved for this college.")
                        else:
                            supabase.table("terms").insert({
                                "client_id": selected_client_id,
                                "college_id": config_college_id,
                                "term_name": term_name_input.strip(),
                                "term_code": term_code_input.strip() or None,
                                "metadata": {
                                    "added_via": "college_configuration",
                                    "review_status": "client_supplied",
                                },
                            }).execute()
                            st.success("Academic term saved.")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Could not save academic term: {exc}")

        st.divider()
        st.markdown(f"#### Existing configuration — {config_college['name']}")
        existing_left, existing_right = st.columns(2)

        with existing_left:
            st.markdown("**Saved schedule portals**")
            if current_portals:
                portal_display = [
                    {
                        "Portal": row.get("portal_name"),
                        "URL": row.get("schedule_url"),
                        "Platform": row.get("platform"),
                        "Status": row.get("status"),
                    }
                    for row in current_portals
                ]
                st.dataframe(
                    pd.DataFrame(portal_display),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No schedule portal saved yet.")

        with existing_right:
            st.markdown("**Saved academic terms**")
            if current_terms:
                term_display = [
                    {
                        "Term": row.get("term_name"),
                        "Term code": row.get("term_code"),
                    }
                    for row in current_terms
                ]
                st.dataframe(
                    pd.DataFrame(term_display),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No academic terms saved yet.")

        st.caption(
            "Only records for the selected college and workspace are displayed. "
            "Existing portal and term records are read-only here; adding a new "
            "record does not overwrite them."
        )

# =======================================================
# SALESFORCE PRINTABLE VIEW -> PDF -> EXCEL
# College-name search flow retained from the pre-integration version.
# Existing course/instructor research code above is unchanged.
# =======================================================
with tabs[5]:
    st.subheader("Salesforce account PDF-to-Excel automation")
    st.markdown(
        "Enter the college or university name. The automation opens Salesforce "
        "in Edge, lets you complete login/MFA, searches for the account by name, "
        "opens Printable View, saves a PDF, and converts it into Excel."
    )

    sf_college_name = st.text_input(
        "Salesforce Account / College Name",
        placeholder="Example: California State University, Chico",
        key="salesforce_export_college_name",
    )

    st.info(
        "Salesforce login and MFA are completed by you in the visible Edge window. "
        "The automation reuses its persistent Edge profile and does not store "
        "your password or MFA code."
    )

    sf_script_dir = os.path.dirname(os.path.abspath(__file__))
    sf_pipeline_path = os.path.join(
        sf_script_dir, "salesforce_universal_pipeline.py"
    )

    if st.button(
        "Start Salesforce PDF-to-Excel",
        type="primary",
        use_container_width=True,
        key="start_salesforce_pdf_excel",
    ):
        clean_college_name = sf_college_name.strip()
        if not clean_college_name:
            st.error("Enter a college or university name.")
        elif not os.path.isfile(sf_pipeline_path):
            st.error(
                "salesforce_universal_pipeline.py is missing from the project root. "
                "Copy the supplied pipeline script beside app.py."
            )
        else:
            try:
                creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                subprocess.Popen(
                    [sys.executable, sf_pipeline_path, clean_college_name],
                    cwd=sf_script_dir,
                    creationflags=creation_flags,
                )
                st.success(
                    "Salesforce automation started in a separate console. "
                    "Complete login/MFA in Edge and follow the console prompts."
                )
                st.markdown(
                    "1. Complete Salesforce login/MFA in Edge.\n"
                    "2. The script searches Salesforce for the college name.\n"
                    "3. If automatic selection is not confirmed, open the correct Account record manually and press ENTER in the console.\n"
                    "4. The script opens Printable View and creates the PDF, JSON, and Excel files in the project's `output` folder."
                )
            except Exception as sf_error:
                st.error(f"Could not start Salesforce export: {sf_error}")

    st.caption(
        "This tab does not change course research, instructor research, evidence, "
        "research history, college configuration, or existing Supabase records."
    )
