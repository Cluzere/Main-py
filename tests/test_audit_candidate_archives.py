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


    def test_attachment_semaphore_waits_instead_of_dropping_upload(self):
        import asyncio
        from types import SimpleNamespace

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.attachment_semaphore = asyncio.Semaphore(1)
        active = 0
        maximum_active = 0

        async def fake_encode(_attachment):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return None, [], [], "image"

        bot._encode_attachment_impl = fake_encode

        async def run_batch():
            attachments = [SimpleNamespace(filename=f"image-{i}.png") for i in range(5)]
            return await asyncio.gather(*(bot.encode_attachment(a) for a in attachments))

        results = asyncio.run(run_batch())
        self.assertEqual(len(results), 5)
        self.assertTrue(all(result[3] == "image" for result in results))
        self.assertEqual(maximum_active, 1)

    def test_retry_once_for_explicit_gateway_failure_before_stream(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        class Budget:
            def acquire(self, _category):
                return True, 0.0

        bot.global_budget = Budget()
        bot.ai_semaphore = asyncio.Semaphore(1)
        attempts = 0

        async def fake_impl(*args, _diag_timing=None, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Responses HTTP 502: Unable to forward upstream stream")
            return "jawaban pulih", []

        bot._run_responses_ai_impl = fake_impl
        result = asyncio.run(bot.run_responses_ai([], None))
        self.assertEqual(result[0], "jawaban pulih")
        self.assertEqual(attempts, 2)

    def test_no_retry_after_stream_event_has_started(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        class Budget:
            def acquire(self, _category):
                return True, 0.0

        bot.global_budget = Budget()
        bot.ai_semaphore = asyncio.Semaphore(1)
        attempts = 0

        async def fake_impl(*args, _diag_timing=None, **kwargs):
            nonlocal attempts
            attempts += 1
            _diag_timing["first_event_seconds"] = 0.01
            raise RuntimeError("Responses HTTP 502: ambiguous failure after stream event")

        bot._run_responses_ai_impl = fake_impl
        with self.assertRaisesRegex(RuntimeError, "HTTP 502"):
            asyncio.run(bot.run_responses_ai([], None))
        self.assertEqual(attempts, 1)


    def test_docx_header_and_footer_are_extracted(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph("BODY TEXT")
        doc.sections[0].header.paragraphs[0].text = "HEADER AUDIT TEXT"
        doc.sections[0].footer.paragraphs[0].text = "FOOTER AUDIT TEXT"
        out = io.BytesIO()
        doc.save(out)
        text, _ = self.bot.extract_docx_content(out.getvalue(), "header-footer.docx")
        self.assertIn("HEADER AUDIT TEXT", text)
        self.assertIn("FOOTER AUDIT TEXT", text)

    def test_pptx_speaker_notes_and_chart_data_are_extracted(self):
        from pptx import Presentation
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.util import Inches

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.notes_slide.notes_text_frame.text = "SPEAKER NOTES AUDIT"
        data = CategoryChartData()
        data.categories = ["Q1", "Q2"]
        data.add_series("Revenue", (10, 20))
        chart = slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(1), Inches(1), Inches(5), Inches(3), data
        ).chart
        chart.has_title = True
        chart.chart_title.text_frame.text = "Quarterly Revenue"
        out = io.BytesIO()
        prs.save(out)

        text, _ = self.bot.extract_pptx_content(out.getvalue(), "notes-chart.pptx")
        self.assertIn("SPEAKER NOTES AUDIT", text)
        self.assertIn("Quarterly Revenue", text)
        self.assertIn("Revenue", text)
        self.assertIn("10", text)


    @staticmethod
    def make_7z(entries):
        import py7zr
        buffer = io.BytesIO()
        with py7zr.SevenZipFile(buffer, mode="w") as archive:
            for name, value in entries:
                archive.writestr(value, name)
        return buffer.getvalue()

    def test_reads_text_member_from_7z_in_memory(self):
        raw = self.make_7z([("hello.txt", "7Z ARCHIVE CONTENT")])
        text, images = self.bot.extract_7z_content(raw, "sample.7z")
        self.assertIn("7Z ARCHIVE CONTENT", text)
        self.assertEqual(images, [])

    def test_reads_docx_member_from_7z(self):
        raw = self.make_7z([("inside.docx", self.make_docx("7Z NESTED DOCX CONTENT"))])
        text, _ = self.bot.extract_7z_content(raw, "docs.7z")
        self.assertIn("7Z NESTED DOCX CONTENT", text)

    def test_rejects_7z_decompression_ratio_bomb(self):
        raw = self.make_7z([("repeat.txt", "A" * (2 * 1024 * 1024))])
        text, _ = self.bot.extract_7z_content(raw, "ratio-bomb.7z")
        self.assertIn("gagal dibaca atau ditolak", text)
        self.assertNotIn("AAAA", text)


    def test_replied_documents_obey_remaining_byte_and_character_budgets(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeAttachment:
            def __init__(self, filename, size):
                self.filename = filename
                self.size = size

        class FakeMessage:
            def __init__(self):
                self.attachments = [
                    FakeAttachment("first.docx", 3),
                    FakeAttachment("second.docx", 3),
                ]

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        async def fake_encode(attachment):
            return ("CONTENT-" + attachment.filename, [], [], "docx")

        bot.encode_attachment = fake_encode
        source = FakeMessage()
        message = SimpleNamespace(
            reference=SimpleNamespace(message_id=123, resolved=source),
            channel=SimpleNamespace(),
        )
        with patch.object(module.discord, "Message", FakeMessage):
            result = asyncio.run(bot.resolve_replied_files(
                message, remaining_bytes=4, remaining_chars=10
            ))
        self.assertIn("first.docx", result)
        self.assertIn("dipotong", result.lower())
        self.assertNotIn("CONTENT-second.docx", result)

    def test_default_attachment_limit_is_ten(self):
        self.assertEqual(module.MAX_ATTACHMENTS_PER_MESSAGE, 10)


if __name__ == "__main__":
    unittest.main()
