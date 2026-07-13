#!/usr/bin/env python3
import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.sci_platform.db import get_connection, initialize_database
from backend.sci_platform.mailbox_testing import (
    fetch_recent_imap_emails,
    mask_email,
    provider_for_email,
    test_imap_login,
)
from backend.sci_platform.services import (
    confirm_review_task,
    create_kingdee_csv,
    daily_report,
    ensure_extractions_for_unprocessed_emails,
    store_fetched_emails,
)
from tools.mailbox_import_and_test import import_metadata, load_rows, save_test_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real NetEase mailbox-to-business-flow smoke test.")
    parser.add_argument("--xlsx", required=True, help="Workbook with 邮箱/密码 columns.")
    parser.add_argument("--email", action="append", default=[], help="Email to test. Can be passed multiple times.")
    parser.add_argument("--max-messages", type=int, default=3, help="Recent INBOX messages to fetch per mailbox.")
    parser.add_argument("--timeout", type=int, default=25, help="Per IMAP operation timeout seconds.")
    parser.add_argument("--confirm-first", action="store_true", help="Confirm one newly created review task to exercise event/report/export flow.")
    parser.add_argument(
        "--credential-json-stdin",
        action="store_true",
        help="Read a JSON object from stdin mapping email addresses to temporary credentials.",
    )
    args = parser.parse_args()

    source_path = Path(args.xlsx).expanduser().resolve()
    requested = {email.strip().lower() for email in args.email if email.strip()}
    if not requested:
        raise SystemExit("Pass at least one --email for this controlled full-flow test.")

    rows = load_rows(source_path)
    selected_rows = [row for row in rows if row.email in requested]
    missing = sorted(requested - {row.email for row in selected_rows})
    if missing:
        raise SystemExit("Missing credentials in workbook for: " + ", ".join(mask_email(email) for email in missing))

    if args.credential_json_stdin:
        overrides = {key.strip().lower(): value for key, value in json.loads(sys.stdin.readline()).items()}
        selected_rows = [replace(row, password=overrides.get(row.email, row.password)) for row in selected_rows]

    unsupported = [row.email for row in selected_rows if provider_for_email(row.email).provider != "netease"]
    if unsupported:
        raise SystemExit("This script only tests 163/126 NetEase accounts: " + ", ".join(mask_email(email) for email in unsupported))

    batch = "netease-full-flow-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    initialize_database()

    with get_connection() as conn:
        import_summary = import_metadata(conn, selected_rows, source_path.name, batch)
        mailbox_ids = import_summary["mailbox_ids"]
        mailbox_reports = []

        for row in selected_rows:
            config = provider_for_email(row.email)
            result = test_imap_login(row.email, row.password, config, timeout_seconds=args.timeout)
            mailbox_id = mailbox_ids[row.email]
            save_test_result(conn, batch, mailbox_id, config.provider, config.imap_host, config.imap_port, "xlsx", result)

            fetch_summary = {"seen": 0, "inserted": 0, "duplicates": 0}
            if result.result == "success":
                messages = fetch_recent_imap_emails(
                    row.email,
                    row.password,
                    config,
                    max_messages=args.max_messages,
                    timeout_seconds=args.timeout,
                )
                fetch_summary = store_fetched_emails(conn, mailbox_id, messages, batch)

            mailbox_reports.append(
                {
                    "masked_email": mask_email(row.email),
                    "provider": config.provider,
                    "connection_result": result.result,
                    "error_type": result.error_type,
                    "inbox_message_count": result.inbox_message_count,
                    "fetch": fetch_summary,
                }
            )

        created_review_tasks = ensure_extractions_for_unprocessed_emails(conn)
        batch_tasks = conn.execute(
            """
            SELECT t.task_id, t.status, x.category, x.confidence
            FROM review_tasks t
            JOIN emails e ON e.email_id = t.email_id
            JOIN ai_extractions x ON x.extraction_id = t.extraction_id
            WHERE e.fetch_batch = ?
            ORDER BY t.task_id ASC
            """,
            (batch,),
        ).fetchall()

        confirmed = None
        report = None
        export = None
        if args.confirm_first and batch_tasks:
            task_id = batch_tasks[0]["task_id"]
            manuscript = confirm_review_task(
                conn,
                task_id,
                {"confirmed_by": "接入测试", "note": f"{batch} 自动确认一条任务用于全链路验证"},
            )
            confirmed = {
                "task_id": task_id,
                "manuscript_id": manuscript["manuscript_id"],
                "current_status": manuscript["current_status"],
            }
            report = daily_report(conn)
            export = create_kingdee_csv(conn, "接入测试")

        print(
            json.dumps(
                {
                    "batch": batch,
                    "mailboxes": mailbox_reports,
                    "created_review_tasks_total": created_review_tasks,
                    "batch_review_tasks": [
                        {
                            "task_id": task["task_id"],
                            "status": task["status"],
                            "category": task["category"],
                            "confidence": task["confidence"],
                        }
                        for task in batch_tasks
                    ],
                    "confirmed": confirmed,
                    "daily_report_event_count": len(report["events"]) if report else None,
                    "kingdee_export": export,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
