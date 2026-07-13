import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import DATA_DIR, DB_PATH, EXPORT_DIR


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    organization TEXT,
    contact_name TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mailboxes (
    mailbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    email_address TEXT NOT NULL UNIQUE,
    mailbox_type TEXT NOT NULL,
    auth_method TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_sync_at TEXT,
    error_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mailbox_project_links (
    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL REFERENCES mailboxes(mailbox_id),
    project_code TEXT NOT NULL,
    customer_name TEXT,
    author_name TEXT,
    source_file TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    import_batch TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_file, source_row)
);

CREATE TABLE IF NOT EXISTS mailbox_connection_tests (
    test_id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_batch TEXT NOT NULL DEFAULT 'legacy',
    mailbox_id INTEGER NOT NULL REFERENCES mailboxes(mailbox_id),
    provider TEXT NOT NULL,
    imap_host TEXT,
    imap_port INTEGER,
    credential_variant TEXT NOT NULL,
    result TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    inbox_message_count INTEGER,
    tested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS microsoft_oauth_states (
    state TEXT PRIMARY KEY,
    mailbox_id INTEGER NOT NULL REFERENCES mailboxes(mailbox_id),
    code_verifier TEXT NOT NULL,
    redirect_after TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS mailbox_oauth_tokens (
    token_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL UNIQUE REFERENCES mailboxes(mailbox_id),
    provider TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_type TEXT,
    scope TEXT,
    expires_at INTEGER,
    account_hint TEXT,
    id_token_claims_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS emails (
    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL REFERENCES mailboxes(mailbox_id),
    message_id TEXT NOT NULL,
    thread_id TEXT,
    subject TEXT NOT NULL,
    sender TEXT NOT NULL,
    received_at TEXT NOT NULL,
    body_text TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    fetch_batch TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_extractions (
    extraction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL REFERENCES emails(email_id),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    extracted_json TEXT NOT NULL,
    evidence TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manuscripts (
    manuscript_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    journal TEXT,
    title TEXT NOT NULL,
    manuscript_no TEXT,
    corresponding_author TEXT,
    current_status TEXT NOT NULL,
    owner TEXT,
    due_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manuscript_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manuscript_id INTEGER NOT NULL REFERENCES manuscripts(manuscript_id),
    event_type TEXT NOT NULL,
    previous_status TEXT,
    next_status TEXT NOT NULL,
    source_email_id INTEGER REFERENCES emails(email_id),
    extraction_id INTEGER REFERENCES ai_extractions(extraction_id),
    confirmed_by TEXT NOT NULL,
    confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rule_version TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS review_tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL REFERENCES emails(email_id),
    extraction_id INTEGER NOT NULL REFERENCES ai_extractions(extraction_id),
    manuscript_id INTEGER REFERENCES manuscripts(manuscript_id),
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    assigned_to TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manuscript_id INTEGER NOT NULL REFERENCES manuscripts(manuscript_id),
    source_event_id INTEGER REFERENCES manuscript_events(event_id),
    reminder_type TEXT NOT NULL,
    due_date TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kingdee_mappings (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    mapping_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kingdee_sync_jobs (
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL UNIQUE,
    sync_method TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    result TEXT NOT NULL,
    failure_reason TEXT,
    exported_file TEXT,
    operated_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(db_path: Path = DB_PATH) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        run_migrations(conn)
        seed_database(conn)


def run_migrations(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "mailbox_connection_tests", "test_batch", "TEXT NOT NULL DEFAULT 'legacy'")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(item["name"] == column for item in columns):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def seed_database(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
    if existing:
        return

    conn.execute(
        """
        INSERT INTO customers (name, organization, contact_name, owner)
        VALUES (?, ?, ?, ?)
        """,
        ("张医生", "华东医学中心", "张医生", "运营一组"),
    )
    customer_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    conn.execute(
        """
        INSERT INTO mailboxes (customer_id, email_address, mailbox_type, auth_method, status, last_sync_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (customer_id, "zhang.research@example.com", "IMAP", "app_password", "active"),
    )
    mailbox_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    samples = [
        (
            "<sample-001@sci.local>",
            "Decision on manuscript JABC-2026-014: Major Revision due 2026-07-20",
            "editorial@journal-a.example",
            "2026-07-06 09:10:00",
            "Dear Author, your manuscript JABC-2026-014 titled Deep Learning in Oncology requires Major Revision. Please submit the revised manuscript by 2026-07-20.",
        ),
        (
            "<sample-002@sci.local>",
            "Acceptance notification for manuscript SCIMED-7721",
            "system@scimed.example",
            "2026-07-06 10:05:00",
            "Congratulations. Manuscript SCIMED-7721, Clinical Study of Biomarkers, has been Accepted for publication in SCI Medicine.",
        ),
        (
            "<sample-003@sci.local>",
            "APC payment request for manuscript APC-5099",
            "billing@open-journal.example",
            "2026-07-06 11:20:00",
            "Your article APC-5099 has reached APC Payment stage. Please arrange payment before 2026-07-12.",
        ),
    ]
    for idx, (message_id, subject, sender, received_at, body_text) in enumerate(samples, start=1):
        conn.execute(
            """
            INSERT INTO emails (mailbox_id, message_id, thread_id, subject, sender, received_at, body_text, dedupe_key, fetch_batch)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mailbox_id,
                message_id,
                f"thread-{idx}",
                subject,
                sender,
                received_at,
                body_text,
                f"{mailbox_id}:{message_id}",
                "seed-20260706",
            ),
        )

    conn.execute(
        """
        INSERT INTO kingdee_mappings (version, mapping_json, is_active)
        VALUES (?, ?, ?)
        """,
        (
            "kingdee-v1",
            '{"customer":"客户","journal":"期刊","title":"题名","manuscript_no":"稿件编号","status":"状态","due_date":"截止日期","owner":"负责人"}',
            1,
        ),
    )
