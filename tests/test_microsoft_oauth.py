import base64
import json
import unittest

from backend.sci_platform.microsoft_oauth import account_hint_from_claims, decode_jwt_payload, make_pkce_pair, scope_string


class MicrosoftOAuthTests(unittest.TestCase):
    def test_pkce_pair_shape(self):
        verifier, challenge = make_pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertGreaterEqual(len(challenge), 43)
        self.assertNotIn("=", challenge)

    def test_scope_contains_imap_and_offline_access(self):
        scopes = scope_string().split()
        self.assertIn("offline_access", scopes)
        self.assertIn("https://outlook.office.com/IMAP.AccessAsUser.All", scopes)

    def test_decode_jwt_payload_and_hint(self):
        payload = {"preferred_username": "Person@Outlook.com"}
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
        token = f"header.{raw}.signature"
        claims = decode_jwt_payload(token)
        self.assertEqual(claims["preferred_username"], "Person@Outlook.com")
        self.assertEqual(account_hint_from_claims(claims, "fallback@example.com"), "person@outlook.com")


if __name__ == "__main__":
    unittest.main()
