import tempfile
import unittest
from pathlib import Path

from backend.sci_platform.mailbox_testing import FetchedEmail
from backend.sci_platform.db import get_connection, initialize_database
from backend.sci_platform.services import (
    confirm_review_task,
    create_kingdee_csv,
    daily_report,
    ensure_extractions_for_unprocessed_emails,
    fetch_dashboard,
    mark_revision_manual_required,
    list_email_contexts,
    list_audit_logs,
    list_manuscripts,
    list_review_tasks,
    list_revision_jobs,
    list_sync_issues,
    request_review_task_revision,
    search_workspace,
    store_fetched_emails,
)


class BackendFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        initialize_database(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_email_to_manuscript_event_flow(self):
        with get_connection(self.db_path) as conn:
            created = ensure_extractions_for_unprocessed_emails(conn)
            self.assertEqual(created, 3)

            dashboard = fetch_dashboard(conn)
            self.assertEqual(dashboard["cards"]["pending_review"], 3)

            tasks = list_review_tasks(conn)
            self.assertEqual(len(tasks), 3)
            self.assertEqual(tasks[0]["status"], "pending")
            manuscript_numbers = {task["extracted"]["manuscript_no"] for task in tasks}
            self.assertIn("JABC-2026-014", manuscript_numbers)
            task_to_confirm = next(task for task in tasks if task["category"] != "revision")

            manuscript = confirm_review_task(
                conn,
                task_to_confirm["task_id"],
                {"confirmed_by": "测试审核员", "note": "单元测试确认"},
            )
            self.assertIn(manuscript["current_status"], {"Accepted", "APC Payment"})

            manuscripts = list_manuscripts(conn)
            self.assertEqual(len(manuscripts), 1)
            self.assertEqual(len(manuscripts[0]["events"]), 1)

            report = daily_report(conn)
            self.assertEqual(len(report["events"]), 1)

            export = create_kingdee_csv(conn, "测试审核员")
            self.assertTrue(Path(export["exported_file"]).exists())
            self.assertEqual(export["rows"], 1)

            logs = list_audit_logs(conn)
            self.assertGreaterEqual(len(logs), 3)

    def test_extraction_is_idempotent(self):
        with get_connection(self.db_path) as conn:
            self.assertEqual(ensure_extractions_for_unprocessed_emails(conn), 3)
            self.assertEqual(ensure_extractions_for_unprocessed_emails(conn), 0)

    def test_request_revision_does_not_create_manuscript_event(self):
        with get_connection(self.db_path) as conn:
            ensure_extractions_for_unprocessed_emails(conn)
            task = list_review_tasks(conn)[0]
            result = request_review_task_revision(
                conn,
                task["task_id"],
                {"reviewed_by": "测试审核员", "reason": "选择待调整处理"},
            )
            self.assertEqual(result["status"], "needs_revision")
            self.assertEqual(len(list_manuscripts(conn)), 0)
            updated = conn.execute("SELECT status FROM review_tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
            self.assertEqual(updated["status"], "needs_revision")

    def test_confirm_non_submission_does_not_create_manuscript_event(self):
        with get_connection(self.db_path) as conn:
            mailbox = conn.execute("SELECT mailbox_id FROM mailboxes ORDER BY mailbox_id LIMIT 1").fetchone()
            message = FetchedEmail(
                message_id="<notice-only-001@example.com>",
                thread_id="<notice-only-001@example.com>",
                subject="新设备登录提醒",
                sender="safety@service.netease.com",
                received_at="2026-07-10 06:00:00",
                body_text="检测到新的登录设备，请确认是否本人操作。",
            )
            store_fetched_emails(conn, mailbox["mailbox_id"], [message], "notice-only")
            ensure_extractions_for_unprocessed_emails(conn)
            task = next(item for item in list_review_tasks(conn) if item["subject"] == "新设备登录提醒")

            result = confirm_review_task(conn, task["task_id"], {"confirmed_by": "测试审核员"})

            self.assertEqual(result["result"], "no_action")
            self.assertEqual(len(list_manuscripts(conn)), 0)
            events = conn.execute("SELECT COUNT(*) AS count FROM manuscript_events").fetchone()["count"]
            self.assertEqual(events, 0)
            updated = conn.execute("SELECT status, manuscript_id FROM review_tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
            self.assertEqual(updated["status"], "confirmed")
            self.assertIsNone(updated["manuscript_id"])

    def test_list_sync_issues_returns_failed_jobs_only(self):
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO kingdee_sync_jobs
                    (batch_no, sync_method, mapping_version, result, failure_reason, operated_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("failed-unit-test", "csv", "kingdee-v1", "failed", "单元测试失败", "测试审核员"),
            )
            conn.execute(
                """
                INSERT INTO kingdee_sync_jobs
                    (batch_no, sync_method, mapping_version, result, operated_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("success-unit-test", "csv", "kingdee-v1", "success", "测试审核员"),
            )

            issues = list_sync_issues(conn)

            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["batch_no"], "failed-unit-test")
            self.assertEqual(issues[0]["failure_reason"], "单元测试失败")

    def test_store_fetched_emails_is_idempotent(self):
        with get_connection(self.db_path) as conn:
            mailbox = conn.execute("SELECT mailbox_id FROM mailboxes ORDER BY mailbox_id LIMIT 1").fetchone()
            message = FetchedEmail(
                message_id="<real-001@example.com>",
                thread_id="<real-001@example.com>",
                subject="Decision on manuscript JABC-2026-014",
                sender="editor@example.com",
                received_at="2026-07-10 04:00:00",
                body_text="Manuscript JABC-2026-014 has been Accepted.",
            )
            self.assertEqual(store_fetched_emails(conn, mailbox["mailbox_id"], [message], "test-batch")["inserted"], 1)
            self.assertEqual(store_fetched_emails(conn, mailbox["mailbox_id"], [message], "test-batch")["duplicates"], 1)

    def test_contexts_and_search_use_email_extractions(self):
        with get_connection(self.db_path) as conn:
            ensure_extractions_for_unprocessed_emails(conn)

            contexts = list_email_contexts(conn)
            self.assertEqual(len(contexts), 3)
            apc_context = next(item for item in contexts if item["manuscript_no"] == "APC-5099")
            self.assertEqual(apc_context["context_type"], "manuscript")
            self.assertEqual(apc_context["email_count"], 1)
            self.assertEqual(apc_context["pending_count"], 1)
            self.assertEqual(apc_context["current_status"], "APC Payment")
            apc_email = apc_context["timeline"][0]
            self.assertTrue(apc_email["is_english_subject"])
            self.assertIn("版面费缴费请求", apc_email["subject_translated"])
            self.assertIn("版面费缴费阶段", apc_email["snippet_translated"])
            self.assertEqual(apc_email["subject_original"], "APC payment request for manuscript APC-5099")
            self.assertIn("Your article APC-5099", apc_email["snippet_original"])

            results = search_workspace(conn, "APC-5099")
            self.assertTrue(any(item["manuscript_no"] == "APC-5099" for item in results["contexts"]))
            self.assertTrue(any("APC-5099" in item["subject"] for item in results["emails"]))

    def test_revision_jobs_expose_manual_processing_message(self):
        with get_connection(self.db_path) as conn:
            ensure_extractions_for_unprocessed_emails(conn)

            jobs = list_revision_jobs(conn)

            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job["category"], "revision")
            self.assertIn("Major Revision", job["revision_instructions"])
            self.assertEqual(job["manual_message"], "功能尚未完成，需手动处理")
            self.assertEqual(job["manuscript_no"], "JABC-2026-014")

    def test_revision_task_must_be_marked_manual_not_confirmed(self):
        with get_connection(self.db_path) as conn:
            ensure_extractions_for_unprocessed_emails(conn)
            task = next(item for item in list_review_tasks(conn) if item["category"] == "revision")

            with self.assertRaises(ValueError):
                confirm_review_task(conn, task["task_id"], {"confirmed_by": "测试审核员"})

            result = mark_revision_manual_required(
                conn,
                task["task_id"],
                {"reviewed_by": "测试审核员"},
            )

            self.assertEqual(result["status"], "revision_manual_required")
            self.assertEqual(result["message"], "功能尚未完成，需手动处理")
            self.assertEqual(len(list_manuscripts(conn)), 0)
            events = conn.execute("SELECT COUNT(*) AS count FROM manuscript_events").fetchone()["count"]
            self.assertEqual(events, 0)
            updated = conn.execute("SELECT status FROM review_tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
            self.assertEqual(updated["status"], "revision_manual_required")

    def test_context_groups_same_project_across_mailboxes(self):
        with get_connection(self.db_path) as conn:
            customer = conn.execute("SELECT customer_id FROM customers LIMIT 1").fetchone()
            first_mailbox = conn.execute("SELECT mailbox_id FROM mailboxes ORDER BY mailbox_id LIMIT 1").fetchone()
            conn.execute(
                """
                INSERT INTO mailboxes (customer_id, email_address, mailbox_type, auth_method, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (customer["customer_id"], "author.alt@example.com", "IMAP", "app_password", "active"),
            )
            second_mailbox_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            for mailbox_id, source_row in [(first_mailbox["mailbox_id"], 101), (second_mailbox_id, 102)]:
                conn.execute(
                    """
                    INSERT INTO mailbox_project_links
                        (mailbox_id, project_code, customer_name, author_name, source_file, source_row, import_batch)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (mailbox_id, "P-100", "张医生", "李医生", "unit-test.xlsx", source_row, "unit-test"),
                )

            messages = [
                FetchedEmail(
                    message_id="<project-001@example.com>",
                    thread_id="<project-001@example.com>",
                    subject="请问投稿材料是否已准备",
                    sender="author@example.com",
                    received_at="2026-07-10 08:00:00",
                    body_text="请问这个项目的投稿材料是否已经准备好。",
                ),
                FetchedEmail(
                    message_id="<project-002@example.com>",
                    thread_id="<project-002@example.com>",
                    subject="补充作者单位信息",
                    sender="author.alt@example.com",
                    received_at="2026-07-10 09:00:00",
                    body_text="补充一下作者单位信息，麻烦继续处理。",
                ),
                FetchedEmail(
                    message_id="<notice-001@example.com>",
                    thread_id="<notice-001@example.com>",
                    subject="新设备登录提醒",
                    sender="网易邮箱账号安全 <safety@service.netease.com>",
                    received_at="2026-07-10 10:00:00",
                    body_text="网易邮箱检测到新的登录设备。",
                ),
                FetchedEmail(
                    message_id="<account-001@example.com>",
                    thread_id="<account-001@example.com>",
                    subject="作者注册成功通知",
                    sender="journal@example.com",
                    received_at="2026-07-10 11:00:00",
                    body_text="尊敬的作者，您的用户名已注册成功，请点击 activate 链接激活投稿账号。密码：secret",
                ),
            ]
            store_fetched_emails(conn, first_mailbox["mailbox_id"], [messages[0]], "project-context")
            store_fetched_emails(conn, second_mailbox_id, [messages[1]], "project-context")
            store_fetched_emails(conn, first_mailbox["mailbox_id"], [messages[2]], "project-context")
            store_fetched_emails(conn, first_mailbox["mailbox_id"], [messages[3]], "project-context")
            ensure_extractions_for_unprocessed_emails(conn)

            contexts = list_email_contexts(conn)
            project_context = next(
                item for item in contexts if item["project_code"] == "P-100" and item["context_type"] == "project"
            )
            self.assertEqual(project_context["context_type"], "project")
            self.assertEqual(project_context["author_name"], "李医生")
            self.assertEqual(project_context["email_count"], 2)
            mailbox_notice = next(item for item in contexts if item["context_type"] == "mailbox_notice")
            self.assertEqual(mailbox_notice["project_code"], None)
            self.assertEqual(mailbox_notice["email_count"], 1)
            self.assertEqual(mailbox_notice["current_status"], "邮箱系统通知")
            account_notice = next(item for item in contexts if item["context_type"] == "account_notice")
            self.assertEqual(account_notice["project_code"], "P-100")
            self.assertEqual(account_notice["current_status"], "投稿账号通知")


if __name__ == "__main__":
    unittest.main()
