import imaplib
import socket
import unittest

from backend.sci_platform.mailbox_testing import classify_error, mask_email, parse_raw_email, provider_for_email, xoauth2_b64


class MailboxTestingTests(unittest.TestCase):
    def test_provider_for_known_domains(self):
        self.assertEqual(provider_for_email("User@163.com").imap_host, "imap.163.com")
        self.assertEqual(provider_for_email("person@qq.com").auth_method, "app_password")
        self.assertEqual(provider_for_email("name@outlook.com").skip_reason, "needs_microsoft_oauth2")

    def test_provider_for_custom_domain_requires_config(self):
        config = provider_for_email("author@hospital.example")
        self.assertEqual(config.provider, "custom_domain")
        self.assertEqual(config.skip_reason, "needs_manual_imap_config")

    def test_mask_email(self):
        self.assertEqual(mask_email("ab@example.com"), "a*@example.com")
        self.assertEqual(mask_email("abcdef@example.com"), "ab***f@example.com")

    def test_classify_errors(self):
        self.assertEqual(classify_error(socket.timeout("timed out"))[0], "network_timeout")
        self.assertEqual(classify_error(imaplib.IMAP4.error("LOGIN failed"))[0], "auth_failed")
        self.assertEqual(classify_error(UnicodeEncodeError("ascii", "测试", 0, 1, "bad"))[0], "credential_encoding_error")

    def test_xoauth2_payload(self):
        import base64

        encoded = xoauth2_b64("user@outlook.com", "token-value")
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, "user=user@outlook.com\x01auth=Bearer token-value\x01\x01")

    def test_parse_raw_email_extracts_plain_text(self):
        raw = (
            b"Message-ID: <sample@example.com>\r\n"
            b"Subject: =?utf-8?b?U0NJIOa1i+ivlQ==?=\r\n"
            b"From: Editor <editor@example.com>\r\n"
            b"Date: Fri, 10 Jul 2026 12:00:00 +0800\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            + "Manuscript JABC-2026-014 has been Accepted.".encode("utf-8")
        )
        parsed = parse_raw_email(raw, "<fallback@example.com>")
        self.assertEqual(parsed.message_id, "<sample@example.com>")
        self.assertEqual(parsed.subject, "SCI 测试")
        self.assertIn("Accepted", parsed.body_text)
        self.assertEqual(parsed.received_at, "2026-07-10 04:00:00")


if __name__ == "__main__":
    unittest.main()
