"""MySQL storage for college, schedule portal, and academic term configuration."""

from __future__ import annotations

import json
import os
import streamlit as st
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _connection():
    def get_setting(key, default=None):
        try:
            value = st.secrets.get(key)
            if value:
                return value
        except Exception:
            pass

        return os.getenv(key, default)

    required = ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
    settings = {key: get_setting(key) for key in required}
    missing = [key for key, value in settings.items() if not value]

    if missing:
        raise RuntimeError(
            "Missing MySQL settings: " + ", ".join(missing)
        )

    return mysql.connector.connect(
        host=get_setting("MYSQL_HOST", "127.0.0.1"),
        port=int(get_setting("MYSQL_PORT", "3306")),
        database=settings["MYSQL_DATABASE"],
        user=settings["MYSQL_USER"],
        password=settings["MYSQL_PASSWORD"],
        connection_timeout=20,
    )
def _decode_metadata(row):
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    return metadata if isinstance(metadata, dict) else {}


def load_local_config() -> dict:
    """Load college configuration from MySQL."""
    connection = _connection()
    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT id, client_id, name, website_url FROM colleges ORDER BY name"
        )
        colleges = cursor.fetchall()

        cursor.execute(
            "SELECT id, client_id, college_id, portal_name, schedule_url, "
            "platform, status, metadata FROM college_portals"
        )
        portals = cursor.fetchall()

        cursor.execute(
            "SELECT id, client_id, college_id, term_name, term_code, metadata FROM terms"
        )
        terms = cursor.fetchall()

        for row in portals:
            row.update(_decode_metadata(row))
            row.pop("metadata", None)

        for row in terms:
            row.update(_decode_metadata(row))
            row.pop("metadata", None)

        cursor.close()

        return {
            "colleges": colleges,
            "college_portals": portals,
            "terms": terms,
        }
    finally:
        connection.close()


def save_local_config(config: dict) -> None:
    """Save college configuration to MySQL in one transaction."""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a dictionary.")

    normalized = {
        key: config.get(key, [])
        for key in ("colleges", "college_portals", "terms")
    }

    for key, rows in normalized.items():
        if not isinstance(rows, list):
            raise ValueError(f"Configuration field '{key}' must be a list.")

    connection = _connection()
    try:
        cursor = connection.cursor()

        cursor.execute("DELETE FROM college_portals")
        cursor.execute("DELETE FROM terms")
        cursor.execute("DELETE FROM colleges")

        for row in normalized["colleges"]:
            cursor.execute(
                "INSERT INTO colleges (id, client_id, name, website_url) "
                "VALUES (%s, %s, %s, %s)",
                (
                    str(row["id"]),
                    str(row.get("client_id", "local")),
                    row["name"],
                    row.get("website_url"),
                ),
            )

        for row in normalized["college_portals"]:
            known = {
                "id", "client_id", "college_id", "portal_name",
                "schedule_url", "platform", "status", "metadata",
            }
            metadata = dict(row.get("metadata") or {})
            metadata.update({k: v for k, v in row.items() if k not in known})

            cursor.execute(
                "INSERT INTO college_portals "
                "(id, client_id, college_id, portal_name, schedule_url, "
                "platform, status, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(row["id"]),
                    str(row.get("client_id", "local")),
                    str(row["college_id"]),
                    row["portal_name"],
                    row["schedule_url"],
                    row.get("platform"),
                    row.get("status", "active"),
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ),
            )

        for row in normalized["terms"]:
            known = {
                "id", "client_id", "college_id", "term_name", "term_code", "metadata",
            }
            metadata = dict(row.get("metadata") or {})
            metadata.update({k: v for k, v in row.items() if k not in known})

            cursor.execute(
                "INSERT INTO terms "
                "(id, client_id, college_id, term_name, term_code, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    str(row["id"]),
                    str(row.get("client_id", "local")),
                    str(row["college_id"]),
                    row["term_name"],
                    row.get("term_code"),
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ),
            )

        connection.commit()
        cursor.close()

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_client_colleges(config: dict, client_id: str = "local") -> list:
    rows = [
        row for row in config.get("colleges", [])
        if str(row.get("client_id", "local")) == str(client_id)
    ]
    return sorted(rows, key=lambda row: str(row.get("name", "")).casefold())


def get_client_portals(config: dict, client_id: str = "local") -> list:
    return [
        row for row in config.get("college_portals", [])
        if str(row.get("client_id", "local")) == str(client_id)
    ]


def get_client_portals_for_college(
    config: dict, client_id: str, college_id: str
) -> list:
    return [
        row for row in get_client_portals(config, client_id)
        if str(row.get("college_id")) == str(college_id)
    ]


def get_client_terms(
    config: dict, client_id: str = "local", college_id: str | None = None
) -> list:
    rows = [
        row for row in config.get("terms", [])
        if str(row.get("client_id", "local")) == str(client_id)
    ]

    if college_id is not None:
        rows = [
            row for row in rows
            if str(row.get("college_id")) == str(college_id)
        ]

    return sorted(rows, key=lambda row: str(row.get("term_name", "")).casefold())

def search_college_master(search_text: str, limit: int = 50) -> list:
    """Search imported college_master records by college name."""
    query = (search_text or "").strip()
    if len(query) < 2:
        return []

    safe_limit = max(1, min(int(limit), 100))
    connection = _connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT id, college_name, control_type
            FROM college_master
            WHERE college_name LIKE %s
            ORDER BY
                CASE
                    WHEN LOWER(college_name) = LOWER(%s) THEN 0
                    WHEN LOWER(college_name) LIKE LOWER(%s) THEN 1
                    ELSE 2
                END,
                college_name
            LIMIT {safe_limit}
            """,
            (f"%{query}%", query, f"{query}%"),
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        connection.close()



def get_college_master() -> list:
    """Return all imported colleges for the searchable configuration dropdown."""
    connection = _connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, college_name, control_type "
            "FROM college_master ORDER BY college_name"
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        connection.close()
