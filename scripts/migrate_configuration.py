
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

WORKSPACE_ID = "45345850-d963-472e-977e-01215af9862d"
WORKSPACE_NAME = "Priyanka Research Workspace"


def load_secrets():
    import tomllib

    with open(SECRETS, "rb") as file:
        return tomllib.load(file)


def load_workbook():
    if not WORKBOOK.exists():
        raise FileNotFoundError(f"Workbook not found: {WORKBOOK}")

    excel = pd.ExcelFile(WORKBOOK)
    snapshot = {}

    for sheet in excel.sheet_names:
        df = pd.read_excel(WORKBOOK, sheet_name=sheet)
        records = json.loads(
            df.to_json(orient="records", date_format="iso")
        )
        snapshot[sheet] = records

    return {
        "workbook_name": WORKBOOK.name,
        "sheets": snapshot,
    }


def print_report(data):
    print("\nWORKBOOK MIGRATION DRY RUN")
    print("=" * 45)
    print("Workspace:", WORKSPACE_NAME)
    print("Workspace ID:", WORKSPACE_ID)
    print("Workbook:", data["workbook_name"])

    total = 0
    for sheet, records in data["sheets"].items():
        count = len(records)
        total += count
        print(f"{sheet}: {count} records")

    print("-" * 45)
    print("Total records:", total)
    print("No database changes made.")


def upload_snapshot(data):
    secrets = load_secrets()

    supabase = create_client(
        secrets["SUPABASE_URL"],
        secrets["SUPABASE_KEY"],
    )

    email = input("Supabase admin email: ").strip()
    password = getpass.getpass("Supabase password: ")

    response = supabase.auth.sign_in_with_password({
        "email": email,
        "password": password,
    })

    if not response.user or not response.session:
        raise RuntimeError("Supabase login failed.")

    # Verify the workspace is accessible to this account.
    membership = (
        supabase.table("client_members")
        .select("client_id, role")
        .eq("client_id", WORKSPACE_ID)
        .eq("user_id", response.user.id)
        .execute()
    )

    if not membership.data:
        raise PermissionError("This account is not a workspace member.")

    if membership.data[0]["role"] not in ("owner", "admin"):
        raise PermissionError("Workspace admin permission is required.")

    payload = {
        "client_id": WORKSPACE_ID,
        "workbook_name": data["workbook_name"],
        "workbook_data": data,
    }

    result = (
        supabase.table("client_configurations")
        .upsert(payload, on_conflict="client_id")
        .execute()
    )

    if not result.data:
        raise RuntimeError("Upload returned no confirmation.")

    print("\nConfiguration snapshot uploaded successfully.")
    print("Workspace:", WORKSPACE_NAME)
    print("Sheets preserved:", len(data["sheets"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload the workbook snapshot to Supabase.",
    )
    args = parser.parse_args()

    try:
        data = load_workbook()
        print_report(data)

        if args.apply:
            confirm = input(
                "\nUpload this snapshot to Supabase? Type YES: "
            ).strip()

            if confirm != "YES":
                print("Upload cancelled.")
                return

            upload_snapshot(data)
        else:
            print("\nDry run only.")
            print("To upload, run with --apply.")

    except Exception as exc:
        print(f"\nMigration error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
