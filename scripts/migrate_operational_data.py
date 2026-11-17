
import argparse
import getpass
import json
import sys
from pathlib import Path

import pandas as pd
from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "Three_College_Account_Configuration_Glendale_Link_Added.xlsx"
SECRETS = ROOT / ".streamlit" / "secrets.toml"

CLIENT_ID = "45345850-d963-472e-977e-01215af9862d"


def clean(value):
    if pd.isna(value):
        return None
    return str(value).strip()


def load_data():
    accounts = pd.read_excel(WORKBOOK, sheet_name="Account Master")
    links = pd.read_excel(WORKBOOK, sheet_name="Account Links")
    courses = pd.read_excel(WORKBOOK, sheet_name="Course Master")

    return accounts, links, courses


def prepare_records(accounts, links, courses):
    colleges = []
    portals = []
    terms = []
    course_records = []

    account_map = {}

    for _, row in accounts.iterrows():
        account_id = clean(row["Account ID"])
        name = clean(row["College / Account Name"])

        if not account_id or not name:
            continue

        metadata = {
            str(k): clean(v)
            for k, v in row.to_dict().items()
        }

        colleges.append({
            "client_id": CLIENT_ID,
            "account_id": account_id,
            "name": name,
            "homepage_url": clean(row["Official Website"]),
            "metadata": metadata,
        })

        account_map[account_id] = name

        for term_column in ["Target Term 1", "Target Term 2"]:
            term_name = clean(row.get(term_column))

            if term_name:
                terms.append({
                    "account_id": account_id,
                    "term_name": term_name,
                    "metadata": {
                        "source_column": term_column
                    }
                })

    for _, row in links.iterrows():
        account_id = clean(row["Account ID"])
        url = clean(row["Official URL / Entry Point"])

        if account_id not in account_map or not url:
            continue

        portals.append({
            "account_id": account_id,
            "portal_name": clean(row["Link Type"]),
            "schedule_url": url,
            "status": clean(row["Status"]),
            "metadata": {
                str(k): clean(v)
                for k, v in row.to_dict().items()
            }
        })

    for _, row in courses.iterrows():
        account_id = clean(row["Account ID"])

        if account_id not in account_map:
            continue

        course_records.append({
            "account_id": account_id,
            "course_number": clean(row["Course Number"]),
            "course_title": clean(row["Course Title"]),
            "metadata": {
                str(k): clean(v)
                for k, v in row.to_dict().items()
            }
        })

    return colleges, portals, terms, course_records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    accounts, links, courses = load_data()
    colleges, portals, terms, course_records = prepare_records(
        accounts, links, courses
    )

    print("\nOPERATIONAL MIGRATION REPORT")
    print("=" * 45)
    print("Colleges:", len(colleges))
    print("Portal links:", len(portals))
    print("Target terms:", len(terms))
    print("Course records:", len(course_records))
    print("Subject configurations: preserved in JSONB snapshot")
    print("\nNo database changes made during preparation.")

    if not args.apply:
        print("\nDRY RUN ONLY.")
        print("Run with --apply after reviewing the counts.")
        return

    confirmation = input(
        "\nType IMPORT to write operational records: "
    ).strip()

    if confirmation != "IMPORT":
        print("Import cancelled.")
        return

    import tomllib

    with open(SECRETS, "rb") as file:
        secrets = tomllib.load(file)

    supabase = create_client(
        secrets["SUPABASE_URL"],
        secrets["SUPABASE_KEY"]
    )

    email = input("Supabase admin email: ").strip()
    password = getpass.getpass("Supabase password: ")

    auth = supabase.auth.sign_in_with_password({
        "email": email,
        "password": password
    })

    if not auth.user:
        raise RuntimeError("Supabase login failed.")

    membership = (
        supabase.table("client_members")
        .select("role")
        .eq("client_id", CLIENT_ID)
        .eq("user_id", auth.user.id)
        .execute()
    )

    if not membership.data or membership.data[0]["role"] not in (
        "owner", "admin"
    ):
        raise PermissionError("Workspace admin access required.")

    # Insert or update colleges using the workspace/account identifier.
    supabase.table("colleges").upsert(
        colleges,
        on_conflict="client_id,account_id"
    ).execute()

    college_rows = (
        supabase.table("colleges")
        .select("id, account_id")
        .eq("client_id", CLIENT_ID)
        .execute()
    ).data or []

    college_ids = {
        row["account_id"]: row["id"]
        for row in college_rows
    }

    # Insert terms only when the same college/term does not exist.
    existing_terms = (
        supabase.table("terms")
        .select("college_id, term_name")
        .eq("client_id", CLIENT_ID)
        .execute()
    ).data or []

    existing_term_keys = {
        (r["college_id"], r["term_name"])
        for r in existing_terms
    }

    new_terms = []

    for record in terms:
        college_id = college_ids[record["account_id"]]
        key = (college_id, record["term_name"])

        if key not in existing_term_keys:
            new_terms.append({
                "client_id": CLIENT_ID,
                "college_id": college_id,
                "term_name": record["term_name"],
                "metadata": record["metadata"]
            })
            existing_term_keys.add(key)

    if new_terms:
        supabase.table("terms").insert(new_terms).execute()

    # Avoid duplicate portal records.
    existing_portals = (
        supabase.table("college_portals")
        .select("college_id, portal_name, schedule_url")
        .eq("client_id", CLIENT_ID)
        .execute()
    ).data or []

    existing_portal_keys = {
        (r["college_id"], r["portal_name"], r["schedule_url"])
        for r in existing_portals
    }

    new_portals = []

    for record in portals:
        college_id = college_ids[record["account_id"]]
        key = (
            college_id,
            record["portal_name"],
            record["schedule_url"]
        )

        if key not in existing_portal_keys:
            new_portals.append({
                "client_id": CLIENT_ID,
                "college_id": college_id,
                "portal_name": record["portal_name"],
                "schedule_url": record["schedule_url"],
                "status": record["status"],
                "metadata": record["metadata"]
            })
            existing_portal_keys.add(key)

    if new_portals:
        supabase.table("college_portals").insert(new_portals).execute()

    # Avoid duplicate course records.
    existing_courses = (
        supabase.table("courses")
        .select("college_id, course_number, course_title")
        .eq("client_id", CLIENT_ID)
        .execute()
    ).data or []

    existing_course_keys = {
        (r["college_id"], r["course_number"], r["course_title"])
        for r in existing_courses
    }

    new_courses = []

    for record in course_records:
        college_id = college_ids[record["account_id"]]
        key = (
            college_id,
            record["course_number"],
            record["course_title"]
        )

        if key not in existing_course_keys:
            new_courses.append({
                "client_id": CLIENT_ID,
                "college_id": college_id,
                "course_number": record["course_number"],
                "course_title": record["course_title"],
                "metadata": record["metadata"]
            })
            existing_course_keys.add(key)

    if new_courses:
        supabase.table("courses").insert(new_courses).execute()

    print("\nOperational migration completed.")
    print("Colleges:", len(colleges))
    print("New terms:", len(new_terms))
    print("New portals:", len(new_portals))
    print("New courses:", len(new_courses))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nMIGRATION ERROR: {exc}")
        sys.exit(1)
