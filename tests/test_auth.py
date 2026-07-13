import tempfile
import unittest
from pathlib import Path

from backend.sci_platform import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_secret_path = auth.SESSION_SECRET_PATH
        auth.SESSION_SECRET_PATH = Path(self.tmp.name) / "session_secret"

    def tearDown(self):
        auth.SESSION_SECRET_PATH = self.original_secret_path
        self.tmp.cleanup()

    def test_credentials(self):
        self.assertTrue(auth.credentials_valid("liaojunhua", "666666"))
        self.assertFalse(auth.credentials_valid("liaojunhua", "bad-password"))
        self.assertFalse(auth.credentials_valid("other", "666666"))

    def test_signed_session_cookie(self):
        token = auth.make_session_cookie("liaojunhua", now=100)

        self.assertEqual(auth.verify_session_cookie(token, now=120), "liaojunhua")
        self.assertIsNone(auth.verify_session_cookie(token.replace(".", "x.", 1), now=120))
        self.assertIsNone(auth.verify_session_cookie(token, now=100 + auth.SESSION_TTL_SECONDS + 1))

    def test_cookie_value(self):
        token = auth.make_session_cookie("liaojunhua", now=100)
        header = f"theme=dark; {auth.SESSION_COOKIE}={token}; other=value"

        self.assertEqual(auth.cookie_value(header), token)


if __name__ == "__main__":
    unittest.main()
