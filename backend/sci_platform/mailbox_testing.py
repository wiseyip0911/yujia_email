import base64
import email
import html
import imaplib
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Optional


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    imap_host: Optional[str]
    imap_port: Optional[int]
    auth_method: str
    skip_reason: Optional[str] = None


@dataclass(frozen=True)
class ConnectionResult:
    result: str
    error_type: Optional[str]
    error_message: Optional[str]
    inbox_message_count: Optional[int]


@dataclass(frozen=True)
class FetchedEmail:
    message_id: str
    thread_id: Optional[str]
    subject: str
    sender: str
    received_at: str
    body_text: str


PROVIDERS = {
    "163.com": ProviderConfig("netease", "imap.163.com", 993, "app_password"),
    "126.com": ProviderConfig("netease", "imap.126.com", 993, "app_password"),
    "qq.com": ProviderConfig("qq", "imap.qq.com", 993, "app_password"),
    "sina.com": ProviderConfig("sina", "imap.sina.com", 993, "password_or_app_password"),
    "139.com": ProviderConfig("139", "imap.139.com", 993, "password_or_app_password"),
    "gmail.com": ProviderConfig(
        "gmail",
        "imap.gmail.com",
        993,
        "oauth_or_app_password",
        "needs_google_oauth_or_app_password",
    ),
    "outlook.com": ProviderConfig(
        "microsoft",
        "outlook.office365.com",
        993,
        "oauth2",
        "needs_microsoft_oauth2",
    ),
    "hotmail.com": ProviderConfig(
        "microsoft",
        "outlook.office365.com",
        993,
        "oauth2",
        "needs_microsoft_oauth2",
    ),
}


def normalize_email(value: str) -> str:
    return value.strip().lower()


def email_domain(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized:
        return ""
    return normalized.rsplit("@", 1)[1]


def mask_email(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized:
        return "(invalid)"
    local, domain = normalized.split("@", 1)
    if not local:
        masked_local = "*"
    elif len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = f"{local[:2]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def provider_for_email(email: str) -> ProviderConfig:
    domain = email_domain(email)
    if domain in PROVIDERS:
        return PROVIDERS[domain]
    return ProviderConfig("custom_domain", None, None, "unknown", "needs_manual_imap_config")


def sanitize_error(message: object, email: str = "") -> str:
    text = str(message).replace("\r", " ").replace("\n", " ").strip()
    if email:
        text = text.replace(email, mask_email(email))
    if len(text) > 240:
        text = text[:237] + "..."
    return text


def classify_error(exc: BaseException, email: str = "") -> tuple[str, str]:
    text = sanitize_error(exc, email)
    lowered = text.lower()
    if "ascii" in lowered and "can't encode" in lowered:
        return "credential_encoding_error", text
    if isinstance(exc, socket.timeout) or "timed out" in lowered or "timeout" in lowered:
        return "network_timeout", text
    if "unsafe login" in lowered:
        return "security_blocked", text
    if isinstance(exc, socket.gaierror) or "name or service not known" in lowered:
        return "dns_error", text
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, OSError)) and not isinstance(exc, imaplib.IMAP4.error):
        return "network_error", text
    if "authentication" in lowered or "login" in lowered or "password" in lowered or "auth" in lowered:
        return "auth_failed", text
    return "imap_error", text


def test_imap_login(email: str, password: str, config: ProviderConfig, timeout_seconds: int = 15) -> ConnectionResult:
    if config.skip_reason:
        return ConnectionResult("skipped", config.skip_reason, config.skip_reason, None)
    if not config.imap_host or not config.imap_port:
        return ConnectionResult("skipped", "needs_manual_imap_config", "needs_manual_imap_config", None)
    try:
        password.encode("ascii")
    except UnicodeEncodeError:
        return ConnectionResult("failed", "credential_encoding_error", "credential contains non-ASCII characters", None)

    client = None
    try:
        client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port, timeout=timeout_seconds)
        client.login(email, password)
        if config.provider == "netease":
            send_client_id(client)
        status, data = client.select("INBOX", readonly=True)
        if status != "OK":
            error_message = sanitize_error(data, email)
            error_type = "security_blocked" if "unsafe login" in error_message.lower() else "inbox_select_failed"
            return ConnectionResult("failed", error_type, error_message, None)
        message_count = None
        if data and data[0] is not None:
            try:
                message_count = int(data[0])
            except (TypeError, ValueError):
                message_count = None
        return ConnectionResult("success", None, None, message_count)
    except BaseException as exc:
        error_type, error_message = classify_error(exc, email)
        return ConnectionResult("failed", error_type, error_message, None)
    finally:
        if client is not None:
            try:
                client.logout()
            except BaseException:
                pass


def send_client_id(client: imaplib.IMAP4_SSL) -> None:
    imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
    client._simple_command("ID", '("name" "Thunderbird" "version" "128" "vendor" "Mozilla")')


def xoauth2_b64(email: str, access_token: str) -> str:
    payload = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def test_imap_xoauth2(email: str, access_token: str, timeout_seconds: int = 15) -> ConnectionResult:
    client = None
    try:
        client = imaplib.IMAP4_SSL("outlook.office365.com", 993, timeout=timeout_seconds)
        status, data = client._simple_command("AUTHENTICATE", "XOAUTH2", xoauth2_b64(email, access_token))
        if status != "OK":
            return ConnectionResult("failed", "oauth_auth_failed", sanitize_error(data, email), None)
        status, data = client.select("INBOX", readonly=True)
        if status != "OK":
            return ConnectionResult("failed", "inbox_select_failed", sanitize_error(data, email), None)
        message_count = None
        if data and data[0] is not None:
            try:
                message_count = int(data[0])
            except (TypeError, ValueError):
                message_count = None
        return ConnectionResult("success", None, None, message_count)
    except BaseException as exc:
        error_type, error_message = classify_error(exc, email)
        return ConnectionResult("failed", error_type, error_message, None)
    finally:
        if client is not None:
            try:
                client.logout()
            except BaseException:
                pass


def fetch_recent_imap_emails(
    email_address: str,
    password: str,
    config: ProviderConfig,
    max_messages: int = 5,
    timeout_seconds: int = 20,
) -> list[FetchedEmail]:
    if config.skip_reason:
        raise ValueError(config.skip_reason)
    if not config.imap_host or not config.imap_port:
        raise ValueError("needs_manual_imap_config")
    if max_messages < 1:
        return []

    client = None
    try:
        client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port, timeout=timeout_seconds)
        client.login(email_address, password)
        if config.provider == "netease":
            send_client_id(client)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error("INBOX select failed")

        status, data = client.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []

        uids = data[0].split()
        selected_uids = list(reversed(uids[-max_messages:]))
        messages: list[FetchedEmail] = []
        for uid in selected_uids:
            status, fetched = client.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not fetched:
                continue
            raw = first_rfc822_payload(fetched)
            if not raw:
                continue
            messages.append(parse_raw_email(raw, fallback_id=f"<imap-{email_address}-{uid.decode('ascii', 'ignore')}@local>"))
        return messages
    finally:
        if client is not None:
            try:
                client.logout()
            except BaseException:
                pass


def first_rfc822_payload(fetch_data: list) -> Optional[bytes]:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def parse_raw_email(raw: bytes, fallback_id: str) -> FetchedEmail:
    message = email.message_from_bytes(raw)
    message_id = clean_header(message.get("Message-ID")) or fallback_id
    subject = clean_header(message.get("Subject")) or "(无主题)"
    sender = clean_header(message.get("From")) or "(未知发件人)"
    received_at = parse_received_at(message.get("Date"))
    body_text = extract_text_body(message)
    references = clean_header(message.get("References")) or clean_header(message.get("In-Reply-To"))
    thread_id = references.split()[0] if references else message_id
    return FetchedEmail(
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender=sender,
        received_at=received_at,
        body_text=body_text or "(空正文)",
    )


def clean_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def parse_received_at(value: Optional[str]) -> str:
    if value:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def extract_text_body(message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition", "").lower().startswith("attachment"):
                continue
            append_decoded_part(part, plain_parts, html_parts)
    else:
        append_decoded_part(message, plain_parts, html_parts)

    text = "\n".join(part for part in plain_parts if part.strip()).strip()
    if not text:
        text = "\n".join(html_to_text(part) for part in html_parts if part.strip()).strip()
    return normalize_body_text(text)


def append_decoded_part(part, plain_parts: list[str], html_parts: list[str]) -> None:
    content_type = part.get_content_type()
    if content_type not in {"text/plain", "text/html"}:
        return
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        payload_text = raw if isinstance(raw, str) else ""
    else:
        charset = part.get_content_charset() or "utf-8"
        try:
            payload_text = payload.decode(charset, errors="replace")
        except LookupError:
            payload_text = payload.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        plain_parts.append(payload_text)
    else:
        html_parts.append(payload_text)


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return html.unescape(value)


def normalize_body_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.replace("\r", "\n").split("\n")]
    compact = "\n".join(line for line in lines if line)
    if len(compact) > 12000:
        return compact[:11997] + "..."
    return compact
