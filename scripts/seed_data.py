from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "generated" / "employees.db"


EMPLOYEES = [
    {
        "email": "alex.chen@company.test",
        "name": "Alex Chen",
        "department": "Sales",
        "role": "Account Executive",
        "location": "Chicago",
        "manager": "maria.gomez@company.test",
        "okta_status": "active",
        "account_locked": 0,
        "mfa_status": "enrolled",
        "risk_flag": "none",
    },
    {
        "email": "priya.narayan@company.test",
        "name": "Priya Narayan",
        "department": "Data Engineering",
        "role": "Data Engineer",
        "location": "Remote-US-East",
        "manager": "nolan.reed@company.test",
        "okta_status": "active",
        "account_locked": 0,
        "mfa_status": "enrolled",
        "risk_flag": "none",
    },
    {
        "email": "jordan.lee@company.test",
        "name": "Jordan Lee",
        "department": "Finance",
        "role": "Analyst",
        "location": "New York",
        "manager": "samir.patel@company.test",
        "okta_status": "active",
        "account_locked": 1,
        "mfa_status": "enrolled",
        "risk_flag": "none",
    },
    {
        "email": "taylor.morgan@company.test",
        "name": "Taylor Morgan",
        "department": "People",
        "role": "HR Business Partner",
        "location": "Remote-US-West",
        "manager": "lee.wong@company.test",
        "okta_status": "active",
        "account_locked": 0,
        "mfa_status": "not_enrolled",
        "risk_flag": "none",
    },
]

DEVICES = [
    ("alex.chen@company.test", "MBP-4482", "macOS 14.4", "6.4", "compliant"),
    ("priya.narayan@company.test", "LNX-1029", "Ubuntu 22.04", "6.2", "compliant"),
    ("jordan.lee@company.test", "WIN-7731", "Windows 11", "6.4", "compliant"),
    ("taylor.morgan@company.test", "MBP-1290", "macOS 13.6", "6.4", "needs_review"),
]

ACCESS = [
    ("alex.chen@company.test", "okta-users"),
    ("alex.chen@company.test", "salesforce-standard"),
    ("alex.chen@company.test", "vpn-users"),
    ("priya.narayan@company.test", "okta-users"),
    ("priya.narayan@company.test", "vpn-users"),
    ("priya.narayan@company.test", "jenkins-readonly"),
    ("priya.narayan@company.test", "grafana-viewer"),
    ("jordan.lee@company.test", "okta-users"),
    ("jordan.lee@company.test", "vpn-users"),
    ("jordan.lee@company.test", "finance-app-users"),
    ("taylor.morgan@company.test", "okta-users"),
]


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE employees (
              email TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              department TEXT NOT NULL,
              role TEXT NOT NULL,
              location TEXT NOT NULL,
              manager TEXT NOT NULL,
              okta_status TEXT NOT NULL,
              account_locked INTEGER NOT NULL,
              mfa_status TEXT NOT NULL,
              risk_flag TEXT NOT NULL
            );
            CREATE TABLE devices (
              employee_email TEXT PRIMARY KEY,
              asset_tag TEXT NOT NULL,
              os TEXT NOT NULL,
              vpn_client_version TEXT NOT NULL,
              security_posture TEXT NOT NULL,
              FOREIGN KEY(employee_email) REFERENCES employees(email)
            );
            CREATE TABLE employee_access (
              employee_email TEXT NOT NULL,
              access_group TEXT NOT NULL,
              FOREIGN KEY(employee_email) REFERENCES employees(email)
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO employees (
              email, name, department, role, location, manager, okta_status,
              account_locked, mfa_status, risk_flag
            ) VALUES (
              :email, :name, :department, :role, :location, :manager,
              :okta_status, :account_locked, :mfa_status, :risk_flag
            )
            """,
            EMPLOYEES,
        )
        conn.executemany(
            """
            INSERT INTO devices (
              employee_email, asset_tag, os, vpn_client_version, security_posture
            ) VALUES (?, ?, ?, ?, ?)
            """,
            DEVICES,
        )
        conn.executemany(
            "INSERT INTO employee_access (employee_email, access_group) VALUES (?, ?)",
            ACCESS,
        )
    print(f"Seeded {DB_PATH}")


if __name__ == "__main__":
    main()
