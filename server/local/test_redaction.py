import unittest

from common.redaction import redact_sensitive_text


class RedactionTest(unittest.TestCase):
    def test_installation_and_signed_query_tokens_are_removed(self):
        text = (
            'https://api.example/media/' + 'a' * 64 +
            '/aitravel/api/v1/file/download/user/photo.jpg'
            '?access=signed-token&X-Tos-Signature=signature'
        )
        safe = redact_sensitive_text(text)
        self.assertNotIn('a' * 64, safe)
        self.assertNotIn('signed-token', safe)
        self.assertNotIn('signature', safe)
        self.assertIn('/media/<redacted>/aitravel/', safe)
        self.assertIn('access=<redacted>', safe)

    def test_normal_paths_and_bearer_tokens(self):
        safe = redact_sensitive_text(
            '/media/ready and Authorization: Bearer abc.def.ghi')
        self.assertIn('/media/ready', safe)
        self.assertNotIn('abc.def.ghi', safe)


if __name__ == '__main__':
    unittest.main()
