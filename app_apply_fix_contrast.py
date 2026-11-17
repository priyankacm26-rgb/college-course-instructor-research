import streamlit as st
import pandas as pd
from src.auth import login_user, get_current_user, logout_user
from src.database import (
    get_user_workspaces,
    get_client_colleges,
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
    [data-testid="stMainBlockContainer"] { max-width: 1440px; padding-top: 1.5rem; padding-bottom: 3rem; }
    [data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid var(--line); }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: #344054; }
    h1, h2, h3 { color: var(--ink) !important; letter-spacing: -0.025em; }
    h1 { font-size: 2.05rem !important; font-weight: 750 !important; }
    h2 { font-size: 1.42rem !important; font-weight: 700 !important; }
    h3 { font-size: 1.08rem !important; font-weight: 650 !important; }
    p, label, [data-testid="stCaptionContainer"] { color: #475467; }
    [data-testid="stTabs"] [role="tablist"] { gap: 8px; border-bottom: 1px solid var(--line); }
    [data-testid="stTabs"] button[role="tab"] { border-radius: 9px 9px 0 0; padding: .75rem 1.1rem; font-weight: 650; color: #475467; }
    [data-testid="stTabs"] button[aria-selected="true"] { color: var(--brand) !important; border-bottom: 3px solid var(--brand) !important; background: #EEF4FF; }
    [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input, [data-testid="stTextArea"] textarea, [data-testid="stSelectbox"] [data-baseweb="select"] > div { border: 1px solid #D7DFEA; border-radius: 9px; background: #FFFFFF; min-height: 42px; color: #172033 !important; -webkit-text-fill-color: #172033 !important; }
    [data-testid="stTextInput"] input::placeholder, [data-testid="stNumberInput"] input::placeholder, [data-testid="stTextArea"] textarea::placeholder { color: #98A2B3 !important; -webkit-text-fill-color: #98A2B3 !important; opacity: 1; }
    [data-testid="stSelectbox"] [data-baseweb="select"] *, [data-testid="stMultiSelect"] [data-baseweb="select"] * { color: #172033 !important; }
    [data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus, [data-testid="stTextArea"] textarea:focus { border-color: var(--brand); box-shadow: 0 0 0 2px rgba(36,87,197,.12); }
    [data-testid="stButton"] button, [data-testid="stDownloadButton"] button { border-radius: 9px; min-height: 42px; font-weight: 650; border: 1px solid #D0D8E5; }
    [data-testid="stButton"] button[kind="primary"], [data-testid="stDownloadButton"] button[kind="primary"] { background: var(--brand); border-color: var(--brand); color: #FFFFFF; }
    [data-testid="stButton"] button[kind="primary"]:hover { background: var(--brand-dark); border-color: var(--brand-dark); }
    [data-testid="stAlert"] { border-radius: 10px; border: 1px solid var(--line); }
    [data-testid="stExpander"] { border: 1px solid var(--line); border-radius: 10px; background: #FFFFFF; }
    [data-testid="stDataFrame"], [data-testid="stDataEditor"] { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
    [data-testid="stMetric"] { background: #FFFFFF; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 2px rgba(16,24,40,.03); }
    hr { border-color: var(--line); }
    .app-eyebrow { color: var(--brand); font-size: .76rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; margin-bottom: .35rem; }
    .app-hero { background: linear-gradient(115deg,#FFFFFF 0%,#F0F5FF 100%); border: 1px solid #E1E9F7; border-radius: 16px; padding: 24px 28px; margin: .4rem 0 1.5rem 0; box-shadow: 0 4px 16px rgba(16,24,40,.035); }
    .app-hero-title { color: #172033; font-size: 1.85rem; line-height: 1.2; font-weight: 780; letter-spacing: -.035em; margin: 0; }
    .app-hero-sub { color: #667085; font-size: .98rem; margin-top: .55rem; margin-bottom: 0; }
    .section-note { color: #667085; margin-top: -.35rem; margin-bottom: 1rem; }
    @media (max-width: 760px) { [data-testid="stMainBlockContainer"] { padding: 1rem .8rem 2rem .8rem; } .app-hero { padding: 18px; } .app-hero-title { font-size: 1.45rem; } }
    </style>
    """,
    unsafe_allow_html=True,
)

# ================= AUTHENTICATION GATE =================

st.session_state.setdefault("authenticated", False)

if not st.session_state.get("authenticated"):
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

tabs = st.tabs(["📚  Course Schedule", "👩‍🏫  Instructor", "📑  Evidence & Export"])

with tabs[0]:
    st.subheader("Course & section research")
    st.markdown("<p class='section-note'>Choose a configured college and schedule portal, then enter the course details to begin research.</p>", unsafe_allow_html=True)

    if client_colleges:
        college_by_id = {row["id"]: row for row in client_colleges}
        college_ids = list(college_by_id.keys())

        selected_college_id = st.selectbox(
            "Choose a college from this workspace",
            options=college_ids,
            format_func=lambda college_id: college_by_id[college_id]["name"],
            key="workspace_college_choice",
        )

        selected_college = college_by_id[selected_college_id]

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
            "Course Number", placeholder="350", key="c_code"
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
            "Course Number": course_code,
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
                st.success("Course search completed. Review the results below.")
            except Exception as exc:
                st.error(f"Course research failed: {exc}")
                st.info(
                    "If the portal uses a custom schedule interface, it may "
                    "need a dedicated adapter. Check the source URL and inputs."
                )

    if st.session_state.course_rows:
        st.divider()
        st.subheader("Course Data Review")
        st.caption("Verify and edit values against the official source.")
        course_df = pd.DataFrame(st.session_state.course_rows)
        edited = st.data_editor(
            course_df, num_rows="dynamic", use_container_width=True,
            key="course_editor",
        )
        st.session_state.course_rows = edited.to_dict("records")
        with st.expander("View Registration Search Results / Source Text"):
            st.text(st.session_state.course_context[:20000])

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
        "Is this instructor primary?", ["Unknown", "TRUE", "FALSE"]
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
                st.success("Instructor research completed. Verify each field.")
            except Exception as exc:
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
