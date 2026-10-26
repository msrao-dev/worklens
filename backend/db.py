import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
import bcrypt

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("USER_DB_PATH", "./backend/users.db")


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def init_db_and_seed_hr():
    hr_email = os.getenv("HR_EMAIL", "").strip()
    hr_initial_pw = os.getenv("HR_INITIAL_PW", "").strip()
    jwt_secret = os.getenv("JWT_SECRET", "").strip()

    missing_vars = []
    if not hr_email:
        missing_vars.append("HR_EMAIL")
    if not hr_initial_pw:
        missing_vars.append("HR_INITIAL_PW")
    if not jwt_secret:
        missing_vars.append("JWT_SECRET")

    if missing_vars:
        msg = (
            f"CRITICAL: Server startup aborted. Missing required environment variables: "
            f"{', '.join(missing_vars)}."
        )
        logger.critical(msg)
        print(msg, file=sys.stderr)
        raise RuntimeError(msg)

    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    pw_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_on TEXT NOT NULL
                );
                """
            )

            cursor = conn.execute("SELECT id FROM users WHERE email = ?", (hr_email,))
            existing_hr = cursor.fetchone()

            if not existing_hr:
                hashed = hash_password(hr_initial_pw)
                now_iso = datetime.now(timezone.utc).isoformat().split("+")[0]
                conn.execute(
                    """
                    INSERT INTO users (email, pw_hash, role, is_active, created_on)
                    VALUES (?, ?, 'HR', 1, ?)
                    """,
                    (hr_email, hashed, now_iso),
                )
                logger.info("Successfully initialized and seeded default HR user: %s", hr_email)
            else:
                logger.info("Default HR user (%s) already exists in database.", hr_email)
    finally:
        conn.close()