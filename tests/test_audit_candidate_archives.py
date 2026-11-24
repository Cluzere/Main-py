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


    @staticmethod
    def make_docx(text):
        from docx import Document
        doc = Document()
        doc.add_paragraph(text)
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    @staticmethod
    def make_xlsx(value):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active["A1"] = value
        out = io.BytesIO()
        wb.save(out)
        wb.close()
        return out.getvalue()

    def test_reads_docx_nested_inside_zip(self):
        raw = self.make_zip([("docs/inside.docx", self.make_docx("NESTED DOCX CONTENT"))])
        text, images = self.bot.extract_generic_zip_content(raw, "bundle.zip")
        self.assertIn("NESTED DOCX CONTENT", text)
        self.assertEqual(images, [])

    def test_reads_xlsx_nested_inside_zip(self):
        raw = self.make_zip([("sheets/data.xlsx", self.make_xlsx("NESTED XLSX CONTENT"))])
        text, _ = self.bot.extract_generic_zip_content(raw, "bundle.zip")
        self.assertIn("NESTED XLSX CONTENT", text)

    def test_reads_nested_zip_one_level(self):
        inner = self.make_zip([("inside.txt", "NESTED ZIP CONTENT")])
        outer = self.make_zip([("nested/inner.zip", inner)])
        text, _ = self.bot.extract_generic_zip_content(outer, "outer.zip")
        self.assertIn("NESTED ZIP CONTENT", text)

    def test_rejects_deeper_nested_zip(self):
        level3 = self.make_zip([("deep.txt", "TOO DEEP")])
        level2 = self.make_zip([("level3.zip", level3)])
        level1 = self.make_zip([("level2.zip", level2)])
        text, _ = self.bot.extract_generic_zip_content(level1, "outer.zip")
        self.assertIn("kedalaman maksimum", text)
        self.assertNotIn("TOO DEEP", text)

    def test_skips_symlink_entries(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo("link.txt")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, "not a real file")
        text, _ = self.bot.extract_generic_zip_content(buffer.getvalue(), "symlink.zip")
        self.assertIn("symlink dilewati", text)
        self.assertNotIn("not a real file", text)

    def test_default_attachment_limit_is_ten(self):
        self.assertEqual(module.MAX_ATTACHMENTS_PER_MESSAGE, 10)


if __name__ == "__main__":
    unittest.main()
