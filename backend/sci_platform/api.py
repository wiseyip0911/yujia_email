import html
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .auth import SESSION_COOKIE, cookie_value, credentials_valid, make_session_cookie, verify_session_cookie
from .config import FRONTEND_DIR
from .db import get_connection, initialize_database
from .services import (
    confirm_review_task,
    complete_microsoft_oauth,
    create_kingdee_csv,
    daily_report,
    ensure_extractions_for_unprocessed_emails,
    fetch_dashboard,
    first_query_value,
    handoff_revision_task,
    mark_revision_manual_required,
    list_email_contexts,
    list_audit_logs,
    list_manuscripts,
    list_mailboxes,
    list_revision_jobs,
    list_review_tasks,
    list_sync_issues,
    microsoft_oauth_status,
    request_review_task_revision,
    search_workspace,
    start_microsoft_oauth,
)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class SciPlatformHandler(BaseHTTPRequestHandler):
    server_version = "SCIPlatform/0.1"

    def do_GET(self) -> None:
        self.route("GET")

    def do_POST(self) -> None:
        self.route("POST")

    def route(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                self.handle_api(method, path, parse_qs(parsed.query))
            else:
                self.serve_static(path, authenticated=bool(self.session_username()))
        except ApiError as exc:
            self.send_json({"error": exc.message}, status=exc.status)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_api(self, method: str, path: str, query: dict) -> None:
        initialize_database()
        if method == "GET" and path == "/api/health":
            self.send_json({"ok": True, "service": "sci-platform"})
            return
        if method == "GET" and path == "/api/auth/me":
            username = self.session_username()
            self.send_json({"authenticated": bool(username), "username": username})
            return
        if method == "POST" and path == "/api/auth/login":
            payload = self.read_json_body()
            username = str(payload.get("username") or "").strip()
            password = str(payload.get("password") or "")
            if not credentials_valid(username, password):
                raise ApiError(HTTPStatus.UNAUTHORIZED, "用户名或密码不正确")
            token = make_session_cookie(username)
            self.send_json(
                {"ok": True, "username": username},
                headers={"Set-Cookie": self.session_cookie_header(token)},
            )
            return
        if method == "POST" and path == "/api/auth/logout":
            self.send_json(
                {"ok": True},
                headers={"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"},
            )
            return
        if not self.is_public_api(method, path) and not self.session_username():
            raise ApiError(HTTPStatus.UNAUTHORIZED, "请先登录")

        with get_connection() as conn:
            if method == "GET" and path == "/api/dashboard":
                self.send_json(fetch_dashboard(conn))
            elif method == "POST" and path == "/api/jobs/fetch-emails":
                created = ensure_extractions_for_unprocessed_emails(conn)
                self.send_json({"created_review_tasks": created})
            elif method == "GET" and path == "/api/review-tasks":
                self.send_json({"items": list_review_tasks(conn)})
            elif method == "GET" and path == "/api/revision-jobs":
                self.send_json({"items": list_revision_jobs(conn)})
            elif method == "POST" and path.startswith("/api/revision-jobs/") and path.endswith("/handoff"):
                task_id = self.extract_id(path, prefix="/api/revision-jobs/", suffix="/handoff")
                self.send_json(handoff_revision_task(conn, task_id, self.actor_payload(self.read_json_body(), "reviewed_by")))
            elif method == "POST" and path.startswith("/api/revision-jobs/") and path.endswith("/manual-required"):
                task_id = self.extract_id(path, prefix="/api/revision-jobs/", suffix="/manual-required")
                self.send_json(mark_revision_manual_required(conn, task_id, self.actor_payload(self.read_json_body(), "reviewed_by")))
            elif method == "POST" and path.startswith("/api/review-tasks/") and path.endswith("/confirm"):
                task_id = self.extract_id(path, prefix="/api/review-tasks/", suffix="/confirm")
                self.send_json(confirm_review_task(conn, task_id, self.actor_payload(self.read_json_body(), "confirmed_by")))
            elif method == "POST" and path.startswith("/api/review-tasks/") and path.endswith("/revise"):
                task_id = self.extract_id(path, prefix="/api/review-tasks/", suffix="/revise")
                self.send_json(request_review_task_revision(conn, task_id, self.actor_payload(self.read_json_body(), "reviewed_by")))
            elif method == "GET" and path == "/api/contexts":
                self.send_json({"items": list_email_contexts(conn)})
            elif method == "GET" and path == "/api/search":
                self.send_json(search_workspace(conn, first_query_value(query, "q") or ""))
            elif method == "GET" and path == "/api/manuscripts":
                self.send_json({"items": list_manuscripts(conn)})
            elif method == "GET" and path == "/api/mailboxes":
                self.send_json({"items": list_mailboxes(conn)})
            elif method == "GET" and path == "/api/oauth/microsoft/status":
                self.send_json(microsoft_oauth_status(conn))
            elif method == "POST" and path == "/api/oauth/microsoft/start":
                payload = self.read_json_body()
                mailbox_id = int(payload.get("mailbox_id") or 0)
                self.send_json(start_microsoft_oauth(conn, mailbox_id))
            elif method == "GET" and path == "/api/oauth/microsoft/callback":
                result = complete_microsoft_oauth(conn, query)
                self.send_html(self.oauth_result_html(result))
            elif method == "GET" and path == "/api/reports/daily":
                self.send_json(daily_report(conn))
            elif method == "GET" and path == "/api/sync-issues":
                self.send_json({"items": list_sync_issues(conn)})
            elif method == "POST" and path == "/api/exports/kingdee-csv":
                payload = self.read_json_body(optional=True)
                actor = self.session_username() or payload.get("operated_by") or "系统用户"
                self.send_json(create_kingdee_csv(conn, actor))
            elif method == "GET" and path == "/api/audit-logs":
                self.send_json({"items": list_audit_logs(conn)})
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, f"route not found: {method} {path}")

    def serve_static(self, path: str, authenticated: bool = False) -> None:
        if path in {"", "/", "/index.html"}:
            file_path = FRONTEND_DIR / ("index.html" if authenticated else "login.html")
        elif path == "/login":
            file_path = FRONTEND_DIR / "login.html"
        else:
            clean = path.lstrip("/")
            file_path = (FRONTEND_DIR / clean).resolve()
            if not str(file_path).startswith(str(FRONTEND_DIR.resolve())):
                raise ApiError(HTTPStatus.FORBIDDEN, "invalid static path")
        if not file_path.exists() or not file_path.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "static file not found")
        content_type, _ = mimetypes.guess_type(str(file_path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def read_json_body(self, optional: bool = False) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {} if optional else {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)

    def actor_payload(self, payload: dict, actor_field: str) -> dict:
        data = dict(payload or {})
        username = self.session_username()
        if username:
            data[actor_field] = username
        return data

    def send_json(self, payload: dict, status: int = HTTPStatus.OK, headers: Optional[dict[str, str]] = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str, status: int = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def oauth_result_html(self, result: dict) -> str:
        title = "Microsoft 邮箱授权完成" if result.get("result") == "success" else "Microsoft 邮箱授权需处理"
        detail = result.get("error_message") or "收件箱已通过 XOAUTH2 验证。"
        escaped_title = html.escape(title)
        escaped_email = html.escape(str(result.get("masked_email") or ""))
        escaped_result = html.escape(str(result.get("result") or ""))
        escaped_count = html.escape(str(result.get("inbox_message_count") if result.get("inbox_message_count") is not None else "未取得"))
        escaped_detail = html.escape(str(detail))
        return f"""<!doctype html>
<html lang=\"zh-CN\">
  <head><meta charset=\"utf-8\"><title>{escaped_title}</title></head>
  <body style=\"font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;line-height:1.6;padding:32px;\">
    <h1>{escaped_title}</h1>
    <p>邮箱：{escaped_email}</p>
    <p>结果：{escaped_result}</p>
    <p>收件箱邮件数：{escaped_count}</p>
    <p>说明：{escaped_detail}</p>
    <p>可以关闭此页面，回到 SCI 投稿管理平台刷新邮箱列表。</p>
  </body>
</html>"""

    def extract_id(self, path: str, prefix: str, suffix: str) -> int:
        value = path.removeprefix(prefix).removesuffix(suffix)
        if not value.isdigit():
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid id")
        return int(value)

    def session_username(self) -> Optional[str]:
        return verify_session_cookie(cookie_value(self.headers.get("Cookie")))

    def session_cookie_header(self, token: str) -> str:
        return f"{SESSION_COOKIE}={token}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax"

    def is_public_api(self, method: str, path: str) -> bool:
        return method == "GET" and path == "/api/oauth/microsoft/callback"

    def log_message(self, format: str, *args) -> None:
        return
