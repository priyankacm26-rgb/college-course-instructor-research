
import argparse
import getpass
import json
import sys
import tomllib
from pathlib import Path

import pandas as pd
from supabase import create_client


ROOT = Path(__file__).resolve().parents[1]

WORKBOOK = ROOT / "data" / "processed" / "college_master.xlsx"
SECRETS = ROOT / ".streamlit" / "secrets.toml"

CLIENT_ID = "45345850-d963-472e-977e-01215af9862d"
SHEET_NAME = "Selected 100"

REQUIRED_COLUMNS = {
    "UNITID",
    "INSTNM",
    "WEBADDR",
    "ADDR",
    "CITY",
    "STABBR",
    "ZIP",
}


def clean(value):
    """Convert Excel values into JSON-safe values."""
    if pd.isna(value):
        return None

    if isinstance(value, (int,)):
        return str(value)

    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)

    return str(value).strip() or None


def clean_unitid(value):
    """Keep UNITID as a clean identifier without decimal suffixes."""
    result = clean(value)

    if result is None:
        return None

    if result.endswith(".0"):
        result = result[:-2]

    return result


def load_master():
    if not WORKBOOK.exists():
        raise FileNotFoundError(
            f"College master workbook not found: {WORKBOOK}"
        )

    df = pd.read_excel(
        WORKBOOK,
        sheet_name=SHEET_NAME,
        dtype={"UNITID": "string"},
    )

    missing = REQUIRED_COLUMNS.difference(df.columns)

    if missing:
        raise ValueError(
            "Missing required Excel columns: "
            + ", ".join(sorted(missing))
        )

    df = df.dropna(subset=["UNITID", "INSTNM"]).copy()

    records = []

    for _, row in df.iterrows():
        unitid = clean_unitid(row["UNITID"])
        name = clean(row["INSTNM"])

        if not unitid or not name:
            continue

        metadata = {
            str(column): clean(value)
            for column, value in row.to_dict().items()
        }

        records.append({
            "unitid": unitid,
            "account_id": f"UNITID_{unitid}",
            "name": name,
            "homepage_url": clean(row["WEBADDR"]),
            "metadata": metadata,
        })

    unitids = [r["unitid"] for r in records]

    if len(unitids) != len(set(unitids)):
        raise ValueError("Duplicate UNITIDs found in the Excel workbook.")

    account_ids = [r["account_id"] for r in records]

    if len(account_ids) != len(set(account_ids)):
        raise ValueError("Duplicate generated Account IDs found.")

    if not records:
        raise ValueError("No valid college records found.")

    return records


def connect_and_authenticate():
    if not SECRETS.exists():
        raise FileNotFoundError(
            f"Supabase secrets file not found: {SECRETS}"
        )

    with open(SECRETS, "rb") as file:
        secrets = tomllib.load(file)

    supabase = create_client(
        secrets["SUPABASE_URL"],
        secrets["SUPABASE_KEY"],
    )

    print("\nSupabase authentication")
    email = input("Supabase admin email: ").strip()
    password = getpass.getpass("Supabase password: ")

    auth = supabase.auth.sign_in_with_password({
        "email": email,
        "password": password,
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

    if not membership.data:
        raise PermissionError(
            "This user is not a member of the target workspace."
        )

    role = membership.data[0]["role"]

    if role not in ("owner", "admin"):
        raise PermissionError(
            "Workspace owner/admin permissions are required."
        )

    print("Authentication successful.")
    print("Workspace admin permission verified.")

    return supabase


def get_existing_colleges(supabase):
    response = (
        supabase.table("colleges")
        .select("id, account_id, unitid, name")
        .eq("client_id", CLIENT_ID)
        .execute()
    )

    return response.data or []


def prepare_sync(records, existing):
    existing_by_unitid = {
        str(row["unitid"]): row
        for row in existing
        if row.get("unitid") is not None
    }

    existing_by_account = {
        str(row["account_id"]): row
        for row in existing
        if row.get("account_id") is not None
    }

    new_records = []
    matched_records = []
    conflicts = []

    for record in records:
        unitid = record["unitid"]
        account_id = record["account_id"]

        # A matching UNITID already exists.
        if unitid in existing_by_unitid:
            matched_records.append({
                "unitid": unitid,
                "name": record["name"],
                "reason": "UNITID already exists",
            })
            continue

        # A generated Account ID is already used.
        if account_id in existing_by_account:
            old = existing_by_account[account_id]

            conflicts.append({
                "unitid": unitid,
                "account_id": account_id,
                "existing_name": old.get("name"),
                "reason": "Account ID already exists with no matching UNITID",
            })
            continue

        new_records.append({
            "client_id": CLIENT_ID,
            "unitid": unitid,
            "account_id": account_id,
            "name": record["name"],
            "homepage_url": record["homepage_url"],
            "metadata": record["metadata"],
        })

    return new_records, matched_records, conflicts


def main():
    parser = argparse.ArgumentParser(
        description="Synchronize the 100-college master list to Supabase."
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Insert missing colleges into Supabase.",
    )

    args = parser.parse_args()

    print("\nCOLLEGE MASTER SYNCHRONIZATION")
    print("=" * 55)

    records = load_master()

    print("Workbook:", WORKBOOK.name)
    print("Sheet:", SHEET_NAME)
    print("Valid college records:", len(records))

    print("\nConnecting to Supabase for comparison...")
    supabase = connect_and_authenticate()

    existing = get_existing_colleges(supabase)

    new_records, matched, conflicts = prepare_sync(
        records,
        existing,
    )

    print("\nSYNC PREVIEW")
    print("=" * 55)
    print("Existing workspace colleges:", len(existing))
    print("Master-list colleges:", len(records))
    print("New colleges to insert:", len(new_records))
    print("Already present / matched:", len(matched))
    print("Conflicts:", len(conflicts))

    if conflicts:
        print("\nCONFLICT DETAILS:")
        print(json.dumps(conflicts, indent=2))

        raise RuntimeError(
            "Conflicts found. Resolve them before importing."
        )

    print("\nNEW COLLEGES:")
    for record in new_records:
        print(
            record["account_id"],
            "|",
            record["name"],
            "|",
            record["homepage_url"],
        )

    if not args.apply:
        print("\nDRY RUN COMPLETE.")
        print("No database records were changed.")
        print("Review the preview before running with --apply.")
        return

    if not new_records:
        print("\nNothing to import. All records already exist.")
        return

    confirmation = input(
        f"\nType IMPORT {len(new_records)} to insert these colleges: "
    ).strip()

    if confirmation != f"IMPORT {len(new_records)}":
        print("Import cancelled. No changes made.")
        return

    # Insert only. Never upsert or overwrite existing colleges.
    # Chunking keeps requests manageable.
    inserted = 0

    for start in range(0, len(new_records), 50):
        batch = new_records[start:start + 50]

        response = (
            supabase.table("colleges")
            .insert(batch)
            .execute()
        )

        inserted += len(response.data or [])

        print(
            f"Inserted {inserted} / {len(new_records)} colleges."
        )

    print("\nSYNCHRONIZATION COMPLETED")
    print("=" * 55)
    print("Inserted colleges:", inserted)
    print("Already existing:", len(matched))
    print("Conflicts:", len(conflicts))
    print("Existing colleges were not updated or deleted.")
    print("Research history was not modified.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSYNCHRONIZATION ERROR: {exc}")
        sys.exit(1)
