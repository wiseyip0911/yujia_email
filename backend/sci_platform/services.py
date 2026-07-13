import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import EXPORT_DIR
from .mailbox_testing import FetchedEmail, mask_email, provider_for_email, test_imap_xoauth2
from .microsoft_oauth import (
    MICROSOFT_CLIENT_ID,
    MICROSOFT_REDIRECT_URI,
    OAuthConfigError,
    account_hint_from_claims,
    build_authorization_url,
    decode_jwt_payload,
    exchange_code_for_token,
    make_pkce_pair,
    microsoft_oauth_configured,
    new_state,
    scope_string,
    token_expires_at,
)


STATUS_RULE_VERSION = "status-rules-2026-07-06"
PROMPT_VERSION = "mail-extraction-2026-07-06"
MODEL_NAME = "mock-ai-extraction-v1"


def now_batch(prefix: str) -> str:
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def fetch_dashboard(conn) -> dict:
    pending = conn.execute("SELECT COUNT(*) AS count FROM review_tasks WHERE status = 'pending'").fetchone()["count"]
    manuscripts = conn.execute("SELECT COUNT(*) AS count FROM manuscripts").fetchone()["count"]
    reminders = conn.execute("SELECT COUNT(*) AS count FROM reminders WHERE status = 'open'").fetchone()["count"]
    failed_syncs = conn.execute("SELECT COUNT(*) AS count FROM kingdee_sync_jobs WHERE result != 'success'").fetchone()["count"]
    recent_events = conn.execute(
        """
        SELECT e.event_id, e.next_status, e.confirmed_at, m.title, m.manuscript_no
        FROM manuscript_events e
        JOIN manuscripts m ON m.manuscript_id = e.manuscript_id
        ORDER BY e.confirmed_at DESC
        LIMIT 6
        """
    ).fetchall()
    pending_tasks = conn.execute(
        """
        SELECT t.task_id, t.status, t.task_type, e.subject, x.category, x.confidence, x.evidence
        FROM review_tasks t
        JOIN emails e ON e.email_id = t.email_id
        JOIN ai_extractions x ON x.extraction_id = t.extraction_id
        WHERE t.status = 'pending'
        ORDER BY t.created_at DESC
        LIMIT 6
        """
    ).fetchall()
    for task in pending_tasks:
        task["evidence"] = redact_sensitive_text(task.get("evidence") or "")
    return {
        "cards": {
            "pending_review": pending,
            "manuscripts": manuscripts,
            "open_reminders": reminders,
            "failed_syncs": failed_syncs,
        },
        "recent_events": recent_events,
        "pending_tasks": pending_tasks,
    }


def extract_manuscript_no(text: str) -> Optional[str]:
    patterns = [
        r"\b[A-Z]{2,10}-\d{4}-\d{2,5}\b",
        r"\bJ[A-Z]+-\d{4}-\d{3,6}\b",
        r"\b[A-Z]{2,10}-\d{3,6}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def extract_due_date(text: str) -> Optional[str]:
    match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text)
    return match.group(0) if match else None


def extract_title(text: str, manuscript_no: Optional[str]) -> str:
    titled = re.search(r"titled\s+(.+?)(?:\s+requires|\s+has been|[.,])", text, re.IGNORECASE)
    if titled:
        return titled.group(1).strip()
    if manuscript_no:
        return f"Manuscript {manuscript_no}"
    return "Unmatched Manuscript"


def classify_email(subject: str, body: str) -> tuple[str, str, float]:
    text = f"{subject}\n{body}".lower()
    if "accepted" in text or "acceptance" in text:
        return "Accepted", "accepted", 0.94
    if "reject" in text:
        return "Rejected", "rejected", 0.91
    if "major revision" in text or "minor revision" in text or "revision" in text or "revised" in text:
        return "Revision Requested", "revision", 0.9
    if "proof" in text:
        return "Proof", "proof", 0.88
    if "apc" in text or "payment" in text:
        return "APC Payment", "payment", 0.87
    if "under review" in text:
        return "Under Review", "under_review", 0.84
    if "new submission" in text or "submitted" in text:
        return "New Submission", "submission", 0.82
    return "Non-submission", "other", 0.45


def mock_extract_email(email: dict) -> dict:
    text = f"{email['subject']}\n{email['body_text']}"
    next_status, category, confidence = classify_email(email["subject"], email["body_text"])
    manuscript_no = extract_manuscript_no(text)
    due_date = extract_due_date(text)
    title = extract_title(text, manuscript_no)
    journal = "Unknown Journal"
    if "SCI Medicine" in text:
        journal = "SCI Medicine"
    elif "Journal A" in text or "journal-a" in email["sender"]:
        journal = "Journal A"
    elif "open-journal" in email["sender"]:
        journal = "Open Journal"

    fields = {
        "category": category,
        "journal": journal,
        "title": title,
        "manuscript_no": manuscript_no,
        "next_status": next_status,
        "due_date": due_date,
        "next_action": infer_next_action(next_status),
    }
    evidence = build_evidence(email["body_text"], manuscript_no, next_status, due_date)
    return {
        "model_name": MODEL_NAME,
        "prompt_version": PROMPT_VERSION,
        "category": category,
        "confidence": confidence,
        "extracted": fields,
        "evidence": evidence,
        "raw_output": json.dumps(fields, ensure_ascii=False),
    }


def infer_next_action(status: str) -> str:
    if status == "Revision Requested":
        return "准备返修材料并跟进截止日期"
    if status == "Accepted":
        return "确认接收状态并准备后续出版流程"
    if status == "APC Payment":
        return "确认费用事项并安排缴费/金蝶同步"
    if status == "Proof":
        return "检查校样并确认回传期限"
    if status == "Rejected":
        return "确认拒稿并通知业务负责人"
    return "人工确认邮件含义"


def build_evidence(body: str, manuscript_no: Optional[str], status: str, due_date: Optional[str]) -> str:
    snippets = []
    if manuscript_no and manuscript_no in body:
        snippets.append(manuscript_no)
    for keyword in [status, "Major Revision", "Accepted", "APC Payment", "Proof"]:
        if keyword.lower() in body.lower():
            snippets.append(keyword)
            break
    if due_date:
        snippets.append(due_date)
    if snippets:
        return redact_sensitive_text(" / ".join(snippets))
    return redact_sensitive_text(body[:160])


def redact_sensitive_text(value: str) -> str:
    if not value:
        return value
    patterns = [
        r"(?i)(password\s*[:：]\s*)([^\s,;，；。]+)",
        r"((?:登录)?密码\s*[:：]\s*)([^\s,;，；。]+)",
        r"((?:邮箱)?授权码\s*[:：]\s*)([^\s,;，；。]+)",
        r"((?:验证|校验)码\s*[:：]\s*)([^\s,;，；。]+)",
    ]
    redacted = value
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[已隐藏]", redacted)
    return redacted


EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def mask_email_addresses(value: str) -> str:
    if not value:
        return value
    return EMAIL_PATTERN.sub(lambda match: mask_email(match.group(0)), value)


def is_english_subject(subject: str) -> bool:
    value = normalized_value(subject)
    if not value or CJK_PATTERN.search(value):
        return False
    letters = [char for char in value if char.isalpha()]
    if len(letters) < 4:
        return False
    ascii_letters = [char for char in letters if "a" <= char.lower() <= "z"]
    return len(ascii_letters) / max(len(letters), 1) >= 0.85


def translated_subject(subject: str, fields: dict) -> str:
    manuscript_no = normalized_value(fields.get("manuscript_no")) or extract_manuscript_no(subject) or "相关稿件"
    due_date = normalized_value(fields.get("due_date")) or extract_due_date(subject)
    lower = subject.lower()
    if "major revision" in lower:
        return f"稿件 {manuscript_no} 决定通知：需要大修{f'，截止 {due_date}' if due_date else ''}"
    if "minor revision" in lower or "revision" in lower or "revised" in lower:
        return f"稿件 {manuscript_no} 返修通知{f'，截止 {due_date}' if due_date else ''}"
    if "acceptance" in lower or "accepted" in lower:
        return f"稿件 {manuscript_no} 接收通知"
    if "apc" in lower or "payment" in lower:
        return f"稿件 {manuscript_no} 的版面费缴费请求"
    if "proof" in lower:
        return f"稿件 {manuscript_no} 校样通知"
    if "reject" in lower:
        return f"稿件 {manuscript_no} 拒稿通知"
    if "decision" in lower:
        return f"稿件 {manuscript_no} 审稿决定通知"
    if "submission" in lower or "submitted" in lower:
        return f"稿件 {manuscript_no} 投稿提交通知"
    return f"英文邮件：{subject}"


def translated_body_excerpt(row: dict, fields: dict) -> str:
    manuscript_no = normalized_value(fields.get("manuscript_no")) or extract_manuscript_no(row_text(row)) or "相关稿件"
    due_date = normalized_value(fields.get("due_date")) or extract_due_date(row_text(row))
    title = normalized_value(fields.get("title"))
    title_part = "" if not title or title.startswith("Manuscript ") or title == "Unmatched Manuscript" else f"《{title}》"
    journal = normalized_value(fields.get("journal"))
    status = normalized_value(fields.get("next_status"))
    if is_account_notice(row):
        return "这是一封投稿系统账号通知，请按原文确认是否需要激活账号或记录登录信息。"
    if is_mailbox_system_notice(row):
        return "这是一封邮箱系统通知，通常只需要归档，不进入稿件状态判断。"
    if status == "Revision Requested":
        return f"编辑部通知：稿件 {manuscript_no}{title_part} 需要返修。{f'请在 {due_date} 前提交修订稿。' if due_date else '请查看原文确认返修要求和截止日期。'}"
    if status == "Accepted":
        return f"编辑部通知：稿件 {manuscript_no}{title_part} 已被{journal if journal and journal != 'Unknown Journal' else '期刊'}接收发表。"
    if status == "APC Payment":
        return f"费用通知：稿件 {manuscript_no} 已进入版面费缴费阶段。{f'请在 {due_date} 前安排付款。' if due_date else '请查看原文确认付款要求。'}"
    if status == "Proof":
        return f"校样通知：稿件 {manuscript_no} 已进入校样阶段，请查看原文确认校样和回传要求。"
    if status == "Rejected":
        return f"编辑部通知：稿件 {manuscript_no} 被拒稿，请查看原文确认拒稿原因和后续处理。"
    if status == "Under Review":
        return f"状态通知：稿件 {manuscript_no} 正在审稿中。"
    if status == "New Submission":
        return f"投稿通知：稿件 {manuscript_no} 已提交，请查看原文确认投稿编号和期刊信息。"
    return "这是一封英文邮件，系统已保留原文，请结合原文确认具体含义。"


def safe_json_object(value: Optional[str]) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_revision_instructions(body: str) -> str:
    clean = redact_sensitive_text(mask_email_addresses(body or ""))
    lines = [line.strip() for line in re.split(r"[\r\n]+", clean) if line.strip()]
    keywords = [
        "revision",
        "revise",
        "reviewer",
        "comment",
        "response",
        "major revision",
        "minor revision",
        "修改",
        "返修",
        "审稿人",
        "意见",
        "回复",
    ]
    selected = [line for line in lines if any(keyword in line.lower() for keyword in keywords)]
    if selected:
        return "\n".join(selected[:10])[:1600]
    return search_snippet(clean, "", width=1200)


def list_email_contexts(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH ranked_links AS (
            SELECT
                l.*,
                ROW_NUMBER() OVER (
                    PARTITION BY l.mailbox_id
                    ORDER BY l.source_row ASC, l.link_id ASC
                ) AS rank
            FROM mailbox_project_links l
        ),
        link_one AS (
            SELECT *
            FROM ranked_links
            WHERE rank = 1
        )
        SELECT
            e.email_id,
            e.mailbox_id,
            e.message_id,
            e.thread_id,
            e.subject,
            e.sender,
            e.received_at,
            e.body_text,
            e.fetch_batch,
            m.email_address,
            m.status AS mailbox_status,
            c.customer_id,
            c.name AS customer_name,
            link_one.project_code,
            link_one.author_name,
            x.extraction_id,
            x.category,
            x.confidence,
            x.extracted_json,
            x.evidence,
            t.task_id,
            t.status AS task_status,
            t.task_type
        FROM emails e
        JOIN mailboxes m ON m.mailbox_id = e.mailbox_id
        JOIN customers c ON c.customer_id = m.customer_id
        LEFT JOIN link_one ON link_one.mailbox_id = m.mailbox_id
        LEFT JOIN ai_extractions x ON x.email_id = e.email_id
        LEFT JOIN review_tasks t ON t.email_id = e.email_id AND t.extraction_id = x.extraction_id
        ORDER BY e.received_at DESC, e.email_id DESC
        """
    ).fetchall()
    contexts: dict[str, dict] = {}
    for row in rows:
        fields = safe_json_object(row.get("extracted_json"))
        key, context_type = email_context_key(row, fields)
        context = contexts.setdefault(key, new_email_context(key, context_type, row, fields))
        add_email_to_context(context, row, fields)

    result = []
    for context in contexts.values():
        finalize_email_context(context)
        result.append(context)
    result.sort(key=lambda item: item["latest_received_at"] or "", reverse=True)
    result.sort(key=context_sort_rank)
    return result


def email_context_key(row: dict, fields: dict) -> tuple[str, str]:
    if is_mailbox_system_notice(row):
        return f"mailbox_notice:{row['mailbox_id']}", "mailbox_notice"
    manuscript_no = normalized_value(fields.get("manuscript_no"))
    if manuscript_no:
        return f"manuscript:{row['customer_id']}:{manuscript_no}", "manuscript"
    if is_account_notice(row):
        project_code = normalized_value(row.get("project_code"))
        if project_code:
            return f"account_notice:{row['customer_id']}:{project_code}", "account_notice"
        return f"account_notice:{row['mailbox_id']}", "account_notice"
    project_code = normalized_value(row.get("project_code"))
    if project_code:
        return f"project:{row['customer_id']}:{project_code}", "project"
    author_name = normalized_value(row.get("author_name"))
    if author_name:
        return f"author:{row['customer_id']}:{author_name}", "author"
    thread_id = normalized_value(row.get("thread_id"))
    if thread_id:
        return f"thread:{row['mailbox_id']}:{thread_id}", "thread"
    return f"mailbox:{row['mailbox_id']}", "mailbox"


def normalized_value(value: object) -> str:
    return str(value or "").strip()


def row_text(row: dict) -> str:
    return f"{row.get('subject') or ''}\n{row.get('sender') or ''}\n{row.get('body_text') or ''}"


def is_mailbox_system_notice(row: dict) -> bool:
    text = row_text(row).lower()
    sender = normalized_value(row.get("sender")).lower()
    subject = normalized_value(row.get("subject")).lower()
    if "service.netease.com" in sender:
        return True
    notice_keywords = [
        "新设备登录提醒",
        "登录提醒",
        "安全管家",
        "账号安全",
        "邮箱账号安全",
        "mailbox security",
        "new device login",
    ]
    provider_keywords = ["网易邮箱", "netease", "163.com", "126.com"]
    return any(keyword.lower() in subject for keyword in notice_keywords) and any(
        keyword.lower() in text for keyword in provider_keywords
    )


def is_account_notice(row: dict) -> bool:
    text = row_text(row).lower()
    account_keywords = ["注册成功", "激活", "activate", "用户名", "用户账号", "密码", "account activation"]
    submission_keywords = ["作者", "投稿", "manuscript", "journal", "submission"]
    return any(keyword.lower() in text for keyword in account_keywords) and any(
        keyword.lower() in text for keyword in submission_keywords
    )


def context_sort_rank(context: dict) -> int:
    if context.get("pending_count") and context.get("context_type") not in {"mailbox_notice"}:
        return 0
    if context.get("context_type") in {"manuscript", "project", "author", "thread"}:
        return 1
    if context.get("context_type") == "account_notice":
        return 2
    if context.get("context_type") == "mailbox_notice":
        return 4
    return 3


def new_email_context(context_id: str, context_type: str, row: dict, fields: dict) -> dict:
    manuscript_no = normalized_value(fields.get("manuscript_no"))
    title = context_title(context_type, row, fields)
    project_code = None if context_type == "mailbox_notice" else row.get("project_code")
    author_name = None if context_type == "mailbox_notice" else row.get("author_name")
    return {
        "context_id": context_id,
        "context_type": context_type,
        "title": title,
        "customer_name": row.get("customer_name"),
        "author_name": author_name,
        "project_code": project_code,
        "manuscript_no": manuscript_no or None,
        "masked_email": mask_email(row.get("email_address") or ""),
        "mailbox_status": row.get("mailbox_status"),
        "email_count": 0,
        "pending_count": 0,
        "categories": {},
        "latest_received_at": None,
        "current_status": None,
        "suggested_action": None,
        "email_ids": [],
        "pending_task_ids": [],
        "timeline": [],
    }


def context_title(context_type: str, row: dict, fields: dict) -> str:
    manuscript_no = normalized_value(fields.get("manuscript_no"))
    title = normalized_value(fields.get("title"))
    if context_type == "manuscript" and manuscript_no:
        if title and title != "Unmatched Manuscript" and not title.startswith("Manuscript "):
            return f"{manuscript_no} · {title}"
        return manuscript_no
    if context_type == "project":
        author = normalized_value(row.get("author_name"))
        return f"{row.get('project_code')}{' · ' + author if author else ''}"
    if context_type == "account_notice":
        project_code = normalized_value(row.get("project_code"))
        author = normalized_value(row.get("author_name"))
        suffix = " · ".join(item for item in [project_code, author] if item)
        return f"投稿账号通知{' · ' + suffix if suffix else ''}"
    if context_type == "mailbox_notice":
        return f"邮箱系统通知 · {mask_email(row.get('email_address') or '')}"
    if context_type == "author":
        return normalized_value(row.get("author_name")) or normalized_value(row.get("customer_name")) or "未命名作者"
    if context_type == "thread":
        return normalized_value(row.get("subject")) or "邮件会话"
    return mask_email(row.get("email_address") or "")


def add_email_to_context(context: dict, row: dict, fields: dict) -> None:
    category = normalized_value(row.get("category")) or fields.get("category") or "unclassified"
    next_status = normalized_value(fields.get("next_status"))
    english_subject = is_english_subject(row.get("subject") or "")
    snippet = search_snippet(row.get("body_text") or "", "", width=140)
    evidence = search_snippet(row.get("evidence") or row.get("body_text") or "", "", width=120)
    context["email_count"] += 1
    context["email_ids"].append(row["email_id"])
    context["latest_received_at"] = max(context["latest_received_at"] or "", row.get("received_at") or "")
    context["categories"][category] = context["categories"].get(category, 0) + 1

    if row.get("task_status") == "pending":
        context["pending_count"] += 1
        context["pending_task_ids"].append(row["task_id"])

    if next_status and next_status != "Non-submission" and not context.get("_meaningful_status"):
        context["_meaningful_status"] = next_status
    if next_status and not context.get("_latest_status"):
        context["_latest_status"] = next_status

    context["timeline"].append(
        {
            "email_id": row["email_id"],
            "task_id": row.get("task_id"),
            "task_status": row.get("task_status"),
            "subject": row.get("subject"),
            "subject_original": row.get("subject"),
            "subject_translated": translated_subject(row.get("subject") or "", fields) if english_subject else None,
            "sender": mask_email_addresses(row.get("sender") or ""),
            "received_at": row.get("received_at"),
            "category": category,
            "confidence": row.get("confidence"),
            "next_status": next_status or None,
            "next_action": fields.get("next_action"),
            "evidence": evidence,
            "snippet": snippet,
            "snippet_original": snippet,
            "snippet_translated": translated_body_excerpt(row, fields) if english_subject else None,
            "is_english_subject": english_subject,
        }
    )


def finalize_email_context(context: dict) -> None:
    context["timeline"] = sorted(
        context["timeline"],
        key=lambda item: (item.get("received_at") or "", item.get("email_id") or 0),
        reverse=True,
    )[:5]
    meaningful_status = context.pop("_meaningful_status", None)
    latest_status = context.pop("_latest_status", None)
    context["current_status"] = meaningful_status or latest_status or "未识别"
    if context.get("context_type") == "account_notice" and context["current_status"] == "Non-submission":
        context["current_status"] = "投稿账号通知"
    if context.get("context_type") == "mailbox_notice":
        context["current_status"] = "邮箱系统通知"
    context["suggested_action"] = infer_context_action(context)


def infer_context_action(context: dict) -> str:
    categories = context.get("categories") or {}
    has_business_mail = any(key not in {"other", "unclassified"} for key in categories)
    status = context.get("current_status")
    if context.get("context_type") == "account_notice":
        return "检查是否需要记录投稿系统账号/激活信息，不更新稿件状态"
    if context.get("context_type") == "mailbox_notice":
        return "归档为邮箱系统通知，不进入项目判断"
    if context.get("pending_count") and has_business_mail:
        return "先确认状态类邮件，再更新稿件状态"
    if context.get("pending_count"):
        return "确认是否无需处理，避免占用主队列"
    if status in {"Revision Requested", "APC Payment", "Proof"}:
        return "按提醒继续跟进截止事项"
    if status == "Non-submission":
        return "保持归档，暂不进入稿件流程"
    return "持续观察，等待下一封相关邮件"


def search_workspace(conn, query: str) -> dict:
    term = normalized_value(query)[:80]
    empty = {"query": term, "contexts": [], "emails": [], "manuscripts": [], "mailboxes": []}
    if not term:
        return empty

    contexts = [
        context
        for context in list_email_contexts(conn)
        if context_matches_query(context, term)
    ][:8]
    emails = search_emails(conn, term)
    manuscripts = search_manuscripts(conn, term)
    mailboxes = search_mailboxes(conn, term)
    return {
        "query": term,
        "contexts": contexts,
        "emails": emails,
        "manuscripts": manuscripts,
        "mailboxes": mailboxes,
    }


def context_matches_query(context: dict, term: str) -> bool:
    haystacks = [
        context.get("title"),
        context.get("customer_name"),
        context.get("author_name"),
        context.get("project_code"),
        context.get("manuscript_no"),
        context.get("masked_email"),
        context.get("current_status"),
        context.get("suggested_action"),
    ]
    for item in context.get("timeline") or []:
        haystacks.extend([item.get("subject"), item.get("sender"), item.get("evidence"), item.get("snippet")])
    value = "\n".join(str(item or "") for item in haystacks).lower()
    return term.lower() in value


def like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_emails(conn, term: str) -> list[dict]:
    pattern = like_pattern(term)
    rows = conn.execute(
        """
        SELECT
            e.email_id,
            e.subject,
            e.sender,
            e.received_at,
            e.body_text,
            m.email_address,
            c.name AS customer_name,
            x.category,
            x.confidence,
            x.extracted_json,
            t.task_id,
            t.status AS task_status
        FROM emails e
        JOIN mailboxes m ON m.mailbox_id = e.mailbox_id
        JOIN customers c ON c.customer_id = m.customer_id
        LEFT JOIN ai_extractions x ON x.email_id = e.email_id
        LEFT JOIN review_tasks t ON t.email_id = e.email_id AND t.extraction_id = x.extraction_id
        WHERE
            e.subject LIKE ? ESCAPE '\\'
            OR e.sender LIKE ? ESCAPE '\\'
            OR e.body_text LIKE ? ESCAPE '\\'
            OR e.message_id LIKE ? ESCAPE '\\'
            OR e.thread_id LIKE ? ESCAPE '\\'
        ORDER BY e.received_at DESC, e.email_id DESC
        LIMIT 8
        """,
        (pattern, pattern, pattern, pattern, pattern),
    ).fetchall()
    results = []
    for row in rows:
        fields = safe_json_object(row.get("extracted_json"))
        results.append(
            {
                "email_id": row["email_id"],
                "subject": row["subject"],
                "sender": mask_email_addresses(row["sender"]),
                "received_at": row["received_at"],
                "customer_name": row["customer_name"],
                "masked_email": mask_email(row["email_address"]),
                "category": row.get("category"),
                "confidence": row.get("confidence"),
                "next_status": fields.get("next_status"),
                "task_id": row.get("task_id"),
                "task_status": row.get("task_status"),
                "snippet": search_snippet(row.get("body_text") or "", term),
            }
        )
    return results


def search_manuscripts(conn, term: str) -> list[dict]:
    pattern = like_pattern(term)
    return conn.execute(
        """
        SELECT
            m.manuscript_id,
            m.title,
            m.manuscript_no,
            m.journal,
            m.current_status,
            m.owner,
            m.due_date,
            c.name AS customer_name
        FROM manuscripts m
        JOIN customers c ON c.customer_id = m.customer_id
        WHERE
            m.title LIKE ? ESCAPE '\\'
            OR m.manuscript_no LIKE ? ESCAPE '\\'
            OR m.journal LIKE ? ESCAPE '\\'
            OR m.current_status LIKE ? ESCAPE '\\'
            OR c.name LIKE ? ESCAPE '\\'
        ORDER BY m.updated_at DESC
        LIMIT 8
        """,
        (pattern, pattern, pattern, pattern, pattern),
    ).fetchall()


def search_mailboxes(conn, term: str) -> list[dict]:
    pattern = like_pattern(term)
    rows = conn.execute(
        """
        SELECT
            m.mailbox_id,
            m.email_address,
            m.status,
            m.auth_method,
            c.name AS customer_name,
            COUNT(DISTINCT l.link_id) AS project_count
        FROM mailboxes m
        JOIN customers c ON c.customer_id = m.customer_id
        LEFT JOIN mailbox_project_links l ON l.mailbox_id = m.mailbox_id
        WHERE
            m.email_address LIKE ? ESCAPE '\\'
            OR m.status LIKE ? ESCAPE '\\'
            OR m.auth_method LIKE ? ESCAPE '\\'
            OR c.name LIKE ? ESCAPE '\\'
            OR l.project_code LIKE ? ESCAPE '\\'
            OR l.author_name LIKE ? ESCAPE '\\'
        GROUP BY m.mailbox_id
        ORDER BY m.email_address
        LIMIT 8
        """,
        (pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall()
    for row in rows:
        row["masked_email"] = mask_email(row.pop("email_address"))
    return rows


def search_snippet(text: str, term: str, width: int = 120) -> str:
    clean = redact_sensitive_text(mask_email_addresses(text or "")).replace("\n", " ").strip()
    if not clean:
        return ""
    if not term:
        return clean[:width]
    index = clean.lower().find(term.lower())
    if index < 0:
        return clean[:width]
    start = max(0, index - width // 3)
    end = min(len(clean), start + width)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return f"{prefix}{clean[start:end]}{suffix}"


def ensure_extractions_for_unprocessed_emails(conn) -> int:
    emails = conn.execute(
        """
        SELECT e.*
        FROM emails e
        LEFT JOIN ai_extractions x ON x.email_id = e.email_id
        WHERE x.extraction_id IS NULL
        ORDER BY e.received_at ASC
        """
    ).fetchall()
    created = 0
    for email in emails:
        extraction = mock_extract_email(email)
        conn.execute(
            """
            INSERT INTO ai_extractions
                (email_id, model_name, prompt_version, category, confidence, extracted_json, evidence, raw_output)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email["email_id"],
                extraction["model_name"],
                extraction["prompt_version"],
                extraction["category"],
                extraction["confidence"],
                json.dumps(extraction["extracted"], ensure_ascii=False),
                extraction["evidence"],
                extraction["raw_output"],
            ),
        )
        extraction_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        task_type = "exception_review" if extraction["confidence"] < 0.75 else "status_review"
        conn.execute(
            """
            INSERT INTO review_tasks (email_id, extraction_id, task_type, assigned_to)
            VALUES (?, ?, ?, ?)
            """,
            (email["email_id"], extraction_id, task_type, "运营一组"),
        )
        log_audit(conn, "system", "email", str(email["email_id"]), "ai_extract", None, extraction["raw_output"])
        created += 1
    return created


def store_fetched_emails(conn, mailbox_id: int, messages: list[FetchedEmail], fetch_batch: str) -> dict:
    inserted = 0
    duplicates = 0
    for message in messages:
        dedupe_key = f"{mailbox_id}:{message.message_id}"
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO emails
                (mailbox_id, message_id, thread_id, subject, sender, received_at, body_text, dedupe_key, fetch_batch)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mailbox_id,
                message.message_id,
                message.thread_id,
                message.subject,
                message.sender,
                message.received_at,
                message.body_text,
                dedupe_key,
                fetch_batch,
            ),
        )
        if cursor.rowcount:
            inserted += 1
        else:
            duplicates += 1
    return {"inserted": inserted, "duplicates": duplicates, "seen": len(messages)}


def list_review_tasks(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            t.task_id, t.status, t.task_type, t.created_at, t.reviewed_by, t.reviewed_at,
            e.email_id, e.subject, e.sender, e.received_at, e.body_text,
            x.extraction_id, x.category, x.confidence, x.extracted_json, x.evidence
        FROM review_tasks t
        JOIN emails e ON e.email_id = t.email_id
        JOIN ai_extractions x ON x.extraction_id = t.extraction_id
        ORDER BY CASE t.status WHEN 'pending' THEN 0 ELSE 1 END, t.created_at DESC
        """
    ).fetchall()
    for row in rows:
        row["extracted"] = json.loads(row.pop("extracted_json"))
        row["body_text"] = redact_sensitive_text(row.get("body_text") or "")
        row["evidence"] = redact_sensitive_text(row.get("evidence") or "")
        english_subject = is_english_subject(row.get("subject") or "")
        row["is_english_subject"] = english_subject
        row["subject_original"] = row.get("subject")
        row["subject_translated"] = translated_subject(row.get("subject") or "", row["extracted"]) if english_subject else None
        row["snippet_original"] = search_snippet(row.get("body_text") or "", "", width=180)
        row["snippet_translated"] = translated_body_excerpt(row, row["extracted"]) if english_subject else None
    return rows


def list_revision_jobs(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH ranked_links AS (
            SELECT
                l.*,
                ROW_NUMBER() OVER (
                    PARTITION BY l.mailbox_id
                    ORDER BY l.source_row ASC, l.link_id ASC
                ) AS rank
            FROM mailbox_project_links l
        ),
        link_one AS (
            SELECT *
            FROM ranked_links
            WHERE rank = 1
        )
        SELECT
            t.task_id,
            t.status AS task_status,
            t.created_at AS task_created_at,
            t.reviewed_by,
            t.reviewed_at,
            e.email_id,
            e.mailbox_id,
            e.subject,
            e.sender,
            e.received_at,
            e.body_text,
            e.thread_id,
            m.email_address,
            c.customer_id,
            c.name AS customer_name,
            link_one.project_code,
            link_one.author_name,
            x.extraction_id,
            x.category,
            x.confidence,
            x.extracted_json,
            x.evidence
        FROM ai_extractions x
        JOIN emails e ON e.email_id = x.email_id
        JOIN mailboxes m ON m.mailbox_id = e.mailbox_id
        JOIN customers c ON c.customer_id = m.customer_id
        LEFT JOIN link_one ON link_one.mailbox_id = m.mailbox_id
        LEFT JOIN review_tasks t ON t.email_id = e.email_id AND t.extraction_id = x.extraction_id
        WHERE x.category = 'revision' OR x.extracted_json LIKE '%Revision Requested%'
        ORDER BY e.received_at DESC, e.email_id DESC
        LIMIT 100
        """
    ).fetchall()
    jobs = []
    for row in rows:
        fields = safe_json_object(row.get("extracted_json"))
        english_subject = is_english_subject(row.get("subject") or "")
        body_text = redact_sensitive_text(mask_email_addresses(row.get("body_text") or ""))
        instruction = extract_revision_instructions(row.get("body_text") or "")
        context_key, context_type = email_context_key(row, fields)
        subject_translated = translated_subject(row.get("subject") or "", fields) if english_subject else None
        body_translated_excerpt = translated_body_excerpt(row, fields) if english_subject else None
        jobs.append(
            {
                "job_id": row.get("task_id") or row.get("email_id"),
                "task_id": row.get("task_id"),
                "task_status": row.get("task_status") or "unreviewed",
                "email_id": row["email_id"],
                "context_id": context_key,
                "context_type": context_type,
                "customer_name": row.get("customer_name"),
                "author_name": row.get("author_name"),
                "project_code": row.get("project_code"),
                "manuscript_no": fields.get("manuscript_no"),
                "title": fields.get("title"),
                "journal": fields.get("journal"),
                "due_date": fields.get("due_date"),
                "category": row.get("category"),
                "confidence": row.get("confidence"),
                "subject": row.get("subject"),
                "subject_translated": subject_translated,
                "sender": mask_email_addresses(row.get("sender") or ""),
                "received_at": row.get("received_at"),
                "revision_instructions": instruction,
                "body_excerpt": search_snippet(body_text, "", width=380),
                "body_translated_excerpt": body_translated_excerpt,
                "manual_message": "功能尚未完成，需手动处理",
            }
        )
    return jobs

def confirm_review_task(conn, task_id: int, payload: dict[str, Any]) -> dict:
    task = conn.execute(
        """
        SELECT t.*, x.extracted_json, x.extraction_id, e.email_id
        FROM review_tasks t
        JOIN ai_extractions x ON x.extraction_id = t.extraction_id
        JOIN emails e ON e.email_id = t.email_id
        WHERE t.task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if not task:
        raise ValueError("review task not found")
    if task["status"] != "pending":
        raise ValueError("review task has already been processed")

    extracted = json.loads(task["extracted_json"])
    confirmed = {**extracted, **(payload.get("fields") or {})}
    actor = payload.get("confirmed_by") or "业务审核员"
    next_status = confirmed.get("next_status")
    category = str(confirmed.get("category") or "").lower()
    if category == "revision" or next_status == "Revision Requested":
        raise ValueError("返修类任务不能按普通状态确认，请标记为需手动处理")
    if category == "other" or next_status == "Non-submission":
        conn.execute(
            """
            UPDATE review_tasks
            SET status = 'confirmed', reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
            """,
            (actor, task_id),
        )
        log_audit(
            conn,
            actor,
            "review_task",
            str(task_id),
            "confirm_no_action",
            task["extracted_json"],
            json.dumps(confirmed, ensure_ascii=False),
        )
        return {"task_id": task_id, "result": "no_action", "current_status": "无需处理"}

    customer_id = resolve_customer_for_email(conn, task["email_id"])
    manuscript = find_or_create_manuscript(conn, customer_id, confirmed)
    previous_status = manuscript["current_status"]
    next_status = next_status or previous_status

    conn.execute(
        """
        UPDATE manuscripts
        SET journal = ?, title = ?, manuscript_no = ?, current_status = ?, owner = ?, due_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE manuscript_id = ?
        """,
        (
            confirmed.get("journal"),
            confirmed.get("title") or manuscript["title"],
            confirmed.get("manuscript_no"),
            next_status,
            payload.get("owner") or manuscript["owner"] or "运营一组",
            confirmed.get("due_date"),
            manuscript["manuscript_id"],
        ),
    )
    conn.execute(
        """
        INSERT INTO manuscript_events
            (manuscript_id, event_type, previous_status, next_status, source_email_id, extraction_id, confirmed_by, rule_version, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            manuscript["manuscript_id"],
            "status_change",
            previous_status,
            next_status,
            task["email_id"],
            task["extraction_id"],
            actor,
            STATUS_RULE_VERSION,
            payload.get("note"),
        ),
    )
    event_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    maybe_create_reminder(conn, manuscript["manuscript_id"], event_id, confirmed, payload.get("owner") or manuscript["owner"])

    conn.execute(
        """
        UPDATE review_tasks
        SET status = 'confirmed', manuscript_id = ?, reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE task_id = ?
        """,
        (manuscript["manuscript_id"], actor, task_id),
    )
    log_audit(conn, actor, "review_task", str(task_id), "confirm", task["extracted_json"], json.dumps(confirmed, ensure_ascii=False))
    return get_manuscript(conn, manuscript["manuscript_id"])


def mark_revision_manual_required(conn, task_id: int, payload: dict[str, Any]) -> dict:
    task = conn.execute(
        """
        SELECT t.*, x.extracted_json, x.category
        FROM review_tasks t
        JOIN ai_extractions x ON x.extraction_id = t.extraction_id
        WHERE t.task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if not task:
        raise ValueError("review task not found")
    if task["status"] != "pending":
        raise ValueError("review task has already been processed")

    fields = safe_json_object(task["extracted_json"])
    if task["category"] != "revision" and fields.get("next_status") != "Revision Requested":
        raise ValueError("only revision tasks can be handed off")

    actor = payload.get("reviewed_by") or "业务审核员"
    conn.execute(
        """
        UPDATE review_tasks
        SET status = 'revision_manual_required', reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE task_id = ?
        """,
        (actor, task_id),
    )
    after = {
        "status": "revision_manual_required",
        "note": payload.get("note") or "功能尚未完成，需手动处理",
    }
    log_audit(
        conn,
        actor,
        "review_task",
        str(task_id),
        "mark_revision_manual_required",
        task["extracted_json"],
        json.dumps(after, ensure_ascii=False),
    )
    return {"task_id": task_id, "status": "revision_manual_required", "message": after["note"]}


def handoff_revision_task(conn, task_id: int, payload: dict[str, Any]) -> dict:
    return mark_revision_manual_required(conn, task_id, payload)


def request_review_task_revision(conn, task_id: int, payload: dict[str, Any]) -> dict:
    task = conn.execute(
        """
        SELECT t.*, x.extracted_json
        FROM review_tasks t
        JOIN ai_extractions x ON x.extraction_id = t.extraction_id
        WHERE t.task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if not task:
        raise ValueError("review task not found")
    if task["status"] != "pending":
        raise ValueError("review task has already been processed")

    actor = payload.get("reviewed_by") or "业务审核员"
    reason = payload.get("reason") or "选择待调整处理，需人工复核"
    conn.execute(
        """
        UPDATE review_tasks
        SET status = 'needs_revision', reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE task_id = ?
        """,
        (actor, task_id),
    )
    log_audit(
        conn,
        actor,
        "review_task",
        str(task_id),
        "request_revision",
        task["extracted_json"],
        json.dumps({"reason": reason}, ensure_ascii=False),
    )
    return {"task_id": task_id, "status": "needs_revision", "reason": reason}


def resolve_customer_for_email(conn, email_id: int) -> int:
    row = conn.execute(
        """
        SELECT m.customer_id
        FROM emails e
        JOIN mailboxes m ON m.mailbox_id = e.mailbox_id
        WHERE e.email_id = ?
        """,
        (email_id,),
    ).fetchone()
    return row["customer_id"]


def find_or_create_manuscript(conn, customer_id: int, fields: dict) -> dict:
    manuscript_no = fields.get("manuscript_no")
    existing = None
    if manuscript_no:
        existing = conn.execute(
            "SELECT * FROM manuscripts WHERE customer_id = ? AND manuscript_no = ?",
            (customer_id, manuscript_no),
        ).fetchone()
    if not existing:
        title = fields.get("title") or "Unmatched Manuscript"
        existing = conn.execute(
            "SELECT * FROM manuscripts WHERE customer_id = ? AND title = ?",
            (customer_id, title),
        ).fetchone()
    if existing:
        return existing

    conn.execute(
        """
        INSERT INTO manuscripts (customer_id, journal, title, manuscript_no, current_status, owner, due_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            fields.get("journal"),
            fields.get("title") or "Unmatched Manuscript",
            manuscript_no,
            "New",
            "运营一组",
            fields.get("due_date"),
        ),
    )
    manuscript_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return get_manuscript(conn, manuscript_id)


def get_manuscript(conn, manuscript_id: int) -> dict:
    row = conn.execute("SELECT * FROM manuscripts WHERE manuscript_id = ?", (manuscript_id,)).fetchone()
    if not row:
        raise ValueError("manuscript not found")
    return row


def maybe_create_reminder(conn, manuscript_id: int, event_id: int, fields: dict, owner: Optional[str]) -> None:
    status = fields.get("next_status")
    due_date = fields.get("due_date")
    if status in {"Revision Requested", "APC Payment", "Proof"} or due_date:
        conn.execute(
            """
            INSERT INTO reminders (manuscript_id, source_event_id, reminder_type, due_date, owner)
            VALUES (?, ?, ?, ?, ?)
            """,
            (manuscript_id, event_id, status or "Follow Up", due_date, owner or "运营一组"),
        )


def list_manuscripts(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.*, c.name AS customer_name
        FROM manuscripts m
        JOIN customers c ON c.customer_id = m.customer_id
        ORDER BY m.updated_at DESC
        """
    ).fetchall()
    for row in rows:
        row["events"] = conn.execute(
            """
            SELECT event_id, previous_status, next_status, confirmed_by, confirmed_at, note
            FROM manuscript_events
            WHERE manuscript_id = ?
            ORDER BY confirmed_at DESC
            """,
            (row["manuscript_id"],),
        ).fetchall()
    return rows


def daily_report(conn) -> dict:
    events = conn.execute(
        """
        SELECT e.event_id, e.next_status, e.confirmed_at, m.title, m.manuscript_no, m.journal, c.name AS customer_name
        FROM manuscript_events e
        JOIN manuscripts m ON m.manuscript_id = e.manuscript_id
        JOIN customers c ON c.customer_id = m.customer_id
        WHERE date(e.confirmed_at) = date('now')
        ORDER BY e.confirmed_at DESC
        """
    ).fetchall()
    reminders = conn.execute(
        """
        SELECT r.*, m.title, m.manuscript_no
        FROM reminders r
        JOIN manuscripts m ON m.manuscript_id = r.manuscript_id
        WHERE r.status = 'open'
        ORDER BY r.due_date ASC
        """
    ).fetchall()
    summary = {}
    for event in events:
        summary[event["next_status"]] = summary.get(event["next_status"], 0) + 1
    return {"summary": summary, "events": events, "open_reminders": reminders}


def list_sync_issues(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT sync_id, batch_no, sync_method, mapping_version, result, failure_reason, exported_file, operated_by, created_at
        FROM kingdee_sync_jobs
        WHERE result != 'success'
        ORDER BY created_at DESC, sync_id DESC
        LIMIT 100
        """
    ).fetchall()


def create_kingdee_csv(conn, actor: str = "业务审核员") -> dict:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    mapping = conn.execute("SELECT * FROM kingdee_mappings WHERE is_active = 1 ORDER BY mapping_id DESC LIMIT 1").fetchone()
    batch_no = now_batch("kingdee")
    path = EXPORT_DIR / f"{batch_no}.csv"
    manuscripts = list_manuscripts(conn)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["客户", "期刊", "题名", "稿件编号", "状态", "截止日期", "负责人"],
        )
        writer.writeheader()
        for manuscript in manuscripts:
            writer.writerow(
                {
                    "客户": manuscript["customer_name"],
                    "期刊": manuscript["journal"],
                    "题名": manuscript["title"],
                    "稿件编号": manuscript["manuscript_no"],
                    "状态": manuscript["current_status"],
                    "截止日期": manuscript["due_date"],
                    "负责人": manuscript["owner"],
                }
            )
    conn.execute(
        """
        INSERT INTO kingdee_sync_jobs (batch_no, sync_method, mapping_version, result, exported_file, operated_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (batch_no, "csv", mapping["version"] if mapping else "kingdee-v1", "success", str(path), actor),
    )
    log_audit(conn, actor, "kingdee_sync", batch_no, "export_csv", None, str(path))
    return {"batch_no": batch_no, "exported_file": str(path), "rows": len(manuscripts)}


def list_mailboxes(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH ranked_tests AS (
            SELECT
                t.*,
                ROW_NUMBER() OVER (
                    PARTITION BY t.mailbox_id
                    ORDER BY t.test_id DESC
                ) AS rank
            FROM mailbox_connection_tests t
        ),
        latest_tests AS (
            SELECT *
            FROM ranked_tests
            WHERE rank = 1
        )
        SELECT
            m.mailbox_id,
            m.email_address,
            m.mailbox_type,
            m.auth_method,
            m.status,
            m.last_sync_at,
            m.error_reason,
            m.created_at,
            c.name AS customer_name,
            COUNT(DISTINCT l.link_id) AS project_count,
            lt.provider AS last_provider,
            lt.result AS last_test_result,
            lt.error_type AS last_error_type,
            lt.error_message AS last_error_message,
            lt.inbox_message_count AS inbox_message_count,
            lt.tested_at AS last_tested_at,
            ot.expires_at AS oauth_expires_at,
            ot.account_hint AS oauth_account_hint,
            ot.updated_at AS oauth_updated_at
        FROM mailboxes m
        JOIN customers c ON c.customer_id = m.customer_id
        LEFT JOIN mailbox_project_links l ON l.mailbox_id = m.mailbox_id
        LEFT JOIN latest_tests lt ON lt.mailbox_id = m.mailbox_id
        LEFT JOIN mailbox_oauth_tokens ot ON ot.mailbox_id = m.mailbox_id
        GROUP BY m.mailbox_id
        ORDER BY
            CASE m.status
                WHEN 'active' THEN 0
                WHEN 'auth_failed' THEN 1
                WHEN 'needs_oauth' THEN 2
                WHEN 'config_required' THEN 3
                ELSE 4
            END,
            m.email_address
        """
    ).fetchall()
    for row in rows:
        row["masked_email"] = mask_email(row["email_address"])
        row["oauth_linked"] = bool(row.get("oauth_updated_at"))
        row.pop("email_address", None)
    return rows


def microsoft_oauth_status(conn) -> dict:
    linked = conn.execute("SELECT COUNT(*) AS count FROM mailbox_oauth_tokens WHERE provider = 'microsoft'").fetchone()["count"]
    pending = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM mailboxes
        WHERE auth_method = 'oauth2' AND status IN ('needs_oauth', 'pending_test', 'test_failed')
        """
    ).fetchone()["count"]
    total = conn.execute("SELECT COUNT(*) AS count FROM mailboxes WHERE auth_method = 'oauth2'").fetchone()["count"]
    return {
        "configured": microsoft_oauth_configured(),
        "client_id_configured": bool(MICROSOFT_CLIENT_ID),
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "scopes": scope_string(),
        "linked_mailboxes": linked,
        "pending_mailboxes": pending,
        "total_microsoft_mailboxes": total,
    }


def start_microsoft_oauth(conn, mailbox_id: int) -> dict:
    if not microsoft_oauth_configured():
        raise OAuthConfigError("Microsoft OAuth is not configured")
    mailbox = conn.execute("SELECT * FROM mailboxes WHERE mailbox_id = ?", (mailbox_id,)).fetchone()
    if not mailbox:
        raise ValueError("mailbox not found")
    if provider_for_email(mailbox["email_address"]).provider != "microsoft":
        raise ValueError("mailbox is not a Microsoft mailbox")

    code_verifier, code_challenge = make_pkce_pair()
    state = new_state()
    conn.execute(
        """
        INSERT INTO microsoft_oauth_states (state, mailbox_id, code_verifier, redirect_after)
        VALUES (?, ?, ?, ?)
        """,
        (state, mailbox_id, code_verifier, "/"),
    )
    return {
        "auth_url": build_authorization_url(state, code_challenge, mailbox["email_address"]),
        "state": state,
        "redirect_uri": MICROSOFT_REDIRECT_URI,
    }


def complete_microsoft_oauth(conn, query: dict) -> dict:
    if query.get("error"):
        error = first_query_value(query, "error")
        description = first_query_value(query, "error_description")
        raise ValueError(f"{error}: {description}")
    code = first_query_value(query, "code")
    state = first_query_value(query, "state")
    if not code or not state:
        raise ValueError("missing OAuth code or state")

    state_row = conn.execute(
        "SELECT * FROM microsoft_oauth_states WHERE state = ? AND consumed_at IS NULL",
        (state,),
    ).fetchone()
    if not state_row:
        raise ValueError("invalid or consumed OAuth state")

    mailbox = conn.execute("SELECT * FROM mailboxes WHERE mailbox_id = ?", (state_row["mailbox_id"],)).fetchone()
    if not mailbox:
        raise ValueError("mailbox not found")

    token_payload = exchange_code_for_token(code, state_row["code_verifier"])
    claims = decode_jwt_payload(token_payload.get("id_token"))
    account_hint = account_hint_from_claims(claims, mailbox["email_address"])
    expires_at = token_expires_at(token_payload)
    save_microsoft_token(conn, mailbox["mailbox_id"], token_payload, claims, account_hint, expires_at)
    conn.execute(
        "UPDATE microsoft_oauth_states SET consumed_at = CURRENT_TIMESTAMP WHERE state = ?",
        (state,),
    )

    test_result = test_imap_xoauth2(mailbox["email_address"], token_payload["access_token"])
    record_connection_test(
        conn,
        batch="oauth-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        mailbox_id=mailbox["mailbox_id"],
        provider="microsoft",
        imap_host="outlook.office365.com",
        imap_port=993,
        credential_variant="oauth2",
        result=test_result,
    )
    return {
        "masked_email": mask_email(mailbox["email_address"]),
        "account_hint": mask_email(account_hint) if "@" in account_hint else account_hint,
        "result": test_result.result,
        "error_type": test_result.error_type,
        "error_message": test_result.error_message,
        "inbox_message_count": test_result.inbox_message_count,
    }


def save_microsoft_token(conn, mailbox_id: int, token_payload: dict, claims: dict, account_hint: str, expires_at: int) -> None:
    conn.execute(
        """
        INSERT INTO mailbox_oauth_tokens
            (mailbox_id, provider, access_token, refresh_token, token_type, scope, expires_at, account_hint, id_token_claims_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mailbox_id) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = COALESCE(excluded.refresh_token, mailbox_oauth_tokens.refresh_token),
            token_type = excluded.token_type,
            scope = excluded.scope,
            expires_at = excluded.expires_at,
            account_hint = excluded.account_hint,
            id_token_claims_json = excluded.id_token_claims_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            mailbox_id,
            "microsoft",
            token_payload["access_token"],
            token_payload.get("refresh_token"),
            token_payload.get("token_type"),
            token_payload.get("scope"),
            expires_at,
            account_hint,
            json.dumps(claims, ensure_ascii=False),
        ),
    )


def record_connection_test(
    conn,
    batch: str,
    mailbox_id: int,
    provider: str,
    imap_host: Optional[str],
    imap_port: Optional[int],
    credential_variant: str,
    result,
) -> None:
    conn.execute(
        """
        INSERT INTO mailbox_connection_tests
            (test_batch, mailbox_id, provider, imap_host, imap_port, credential_variant, result, error_type, error_message, inbox_message_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch,
            mailbox_id,
            provider,
            imap_host,
            imap_port,
            credential_variant,
            result.result,
            result.error_type,
            result.error_message,
            result.inbox_message_count,
        ),
    )
    status, reason = status_from_connection_result(result)
    if result.result == "success":
        conn.execute(
            """
            UPDATE mailboxes
            SET status = ?, error_reason = NULL, last_sync_at = CURRENT_TIMESTAMP
            WHERE mailbox_id = ?
            """,
            (status, mailbox_id),
        )
    else:
        conn.execute(
            """
            UPDATE mailboxes
            SET status = ?, error_reason = ?
            WHERE mailbox_id = ?
            """,
            (status, reason, mailbox_id),
        )


def status_from_connection_result(result) -> tuple[str, Optional[str]]:
    if result.result == "success":
        return "active", None
    if result.error_type == "oauth_auth_failed":
        return "needs_oauth", result.error_message
    if result.error_type == "auth_failed":
        return "auth_failed", result.error_message
    if result.error_type == "security_blocked":
        return "security_blocked", result.error_message
    return "test_failed", result.error_message


def first_query_value(query: dict, key: str) -> Optional[str]:
    value = query.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def log_audit(conn, actor: str, object_type: str, object_id: str, action: str, before: Optional[str], after: Optional[str]) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (actor, object_type, object_id, action, before_value, after_value)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (actor, object_type, object_id, action, before, after),
    )


def list_audit_logs(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT *
        FROM audit_logs
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()
