import importlib.util
import io
import os
import zipfile
import unittest

CANDIDATE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "audit-recovery", "review-round-2", "revisi_audit_final_main.py",
)
spec = importlib.util.spec_from_file_location("clouzer_audit_candidate", CANDIDATE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArchiveReaderTests(unittest.TestCase):
    def setUp(self):
        self.bot = module.ClouzerBot.__new__(module.ClouzerBot)

    @staticmethod
    def make_zip(entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries:
                archive.writestr(name, value)
        return buffer.getvalue()

    def test_reads_text_member(self):
        raw = self.make_zip([("folder/hello.txt", "halo clouzer")])
        text, images = self.bot.extract_generic_zip_content(raw, "sample.zip")
        self.assertIn("halo clouzer", text)
        self.assertEqual(images, [])

    def test_rejects_path_traversal_member(self):
        raw = self.make_zip([("../outside.txt", "must not be extracted")])
        text, _ = self.bot.extract_generic_zip_content(raw, "unsafe.zip")
        self.assertIn("path traversal", text)
        self.assertNotIn("must not be extracted", text)

    def test_rejects_corrupt_archive(self):
        with self.assertRaises(Exception):
            self.bot._validate_zip_archive(b"not a zip archive")

    def test_enforces_entry_count_limit(self):
        raw = self.make_zip([(f"{i}.txt", "x") for i in range(module.ZIP_MAX_ENTRIES + 1)])
        with self.assertRaises(ValueError):
            self.bot._validate_zip_archive(raw)

    def test_default_attachment_limit_is_ten(self):
        self.assertEqual(module.MAX_ATTACHMENTS_PER_MESSAGE, 10)


if __name__ == "__main__":
    unittest.main()
