# Regression coverage for archive parsing and 7Z inventory diagnostics.
import asyncio
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

    @staticmethod
    def make_pptx(text):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        shape = slide.shapes.add_textbox(0, 0, Inches(6), Inches(1))
        shape.text_frame.text = text
        out = io.BytesIO()
        prs.save(out)
        return out.getvalue()

    @staticmethod
    def make_pdf(text):
        doc = module.fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        raw = doc.tobytes()
        doc.close()
        return raw

    def test_reads_pptx_nested_inside_zip(self):
        raw = self.make_zip([("slides/deck.pptx", self.make_pptx("NESTED PPTX CONTENT"))])
        text, _ = self.bot.extract_generic_zip_content(raw, "bundle.zip")
        self.assertIn("NESTED PPTX CONTENT", text)

    def test_reads_pdf_nested_inside_zip(self):
        raw = self.make_zip([("docs/report.pdf", self.make_pdf("NESTED PDF CONTENT WITH SUFFICIENT LENGTH FOR PARSING"))])
        text, _ = self.bot.extract_generic_zip_content(raw, "bundle.zip")
        self.assertIn("NESTED PDF CONTENT WITH SUFFICIENT LENGTH FOR PARSING", text)

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
            def __init__(self):
                self.calls = 0

            def acquire(self, _category):
                self.calls += 1
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
        self.assertEqual(bot.global_budget.calls, 2)

    def test_retry_once_for_known_http_500_upstream_stream_failure(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        class Budget:
            def __init__(self):
                self.calls = 0

            def acquire(self, _category):
                self.calls += 1
                return True, 0.0

        bot.global_budget = Budget()
        bot.ai_semaphore = asyncio.Semaphore(1)
        attempts = 0

        async def fake_impl(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Responses HTTP 500: Unable to forward upstream stream")
            return "pulih setelah retry", []

        bot._run_responses_ai_impl = fake_impl
        result = asyncio.run(bot.run_responses_ai([], None))
        self.assertEqual(result[0], "pulih setelah retry")
        self.assertEqual(attempts, 2)
        self.assertEqual(bot.global_budget.calls, 2)

    def test_no_retry_after_stream_event_has_started(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        class Budget:
            def __init__(self):
                self.calls = 0

            def acquire(self, _category):
                self.calls += 1
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
        self.assertEqual(bot.global_budget.calls, 1)

    def test_gateway_retry_is_suppressed_if_global_budget_is_exhausted(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        class Budget:
            def __init__(self):
                self.calls = 0

            def acquire(self, _category):
                self.calls += 1
                return (self.calls == 1), 0.0

        bot.global_budget = Budget()
        bot.ai_semaphore = asyncio.Semaphore(1)
        attempts = 0

        async def fake_impl(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("Responses HTTP 502: upstream unavailable")

        bot._run_responses_ai_impl = fake_impl
        with self.assertRaisesRegex(RuntimeError, "retry Responses dibatalkan"):
            asyncio.run(bot.run_responses_ai([], None))
        self.assertEqual(attempts, 1)
        self.assertEqual(bot.global_budget.calls, 2)


    def test_retry_policy_covers_503_and_504_gateway_failures(self):
        import asyncio

        for status in (503, 504):
            bot = module.ClouzerBot.__new__(module.ClouzerBot)

            class Budget:
                def __init__(self):
                    self.calls = 0

                def acquire(self, _category):
                    self.calls += 1
                    return True, 0.0

            bot.global_budget = Budget()
            bot.ai_semaphore = asyncio.Semaphore(1)
            attempts = 0

            async def fake_impl(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("Responses HTTP %d: gateway unavailable" % status)
                return "recovered", []

            bot._run_responses_ai_impl = fake_impl
            result = asyncio.run(bot.run_responses_ai([], None))
            self.assertEqual(result[0], "recovered")
            self.assertEqual(attempts, 2)
            self.assertEqual(bot.global_budget.calls, 2)


    def test_http500_upstream_forwarding_error_is_retried_once(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        class Budget:
            def __init__(self):
                self.calls = 0

            def acquire(self, _category):
                self.calls += 1
                return True, 0.0

        bot.global_budget = Budget()
        bot.ai_semaphore = asyncio.Semaphore(1)
        attempts = 0

        async def fake_impl(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Responses HTTP 500: Unable to forward upstream stream")
            return "recovered from upstream forwarding error", []

        bot._run_responses_ai_impl = fake_impl
        result = asyncio.run(bot.run_responses_ai([], None))
        self.assertEqual(result[0], "recovered from upstream forwarding error")
        self.assertEqual(attempts, 2)
        self.assertEqual(bot.global_budget.calls, 2)

    def test_retry_policy_does_not_retry_http_429_or_500(self):
        import asyncio

        for status in (429, 500):
            bot = module.ClouzerBot.__new__(module.ClouzerBot)

            class Budget:
                def __init__(self):
                    self.calls = 0

                def acquire(self, _category):
                    self.calls += 1
                    return True, 0.0

            bot.global_budget = Budget()
            bot.ai_semaphore = asyncio.Semaphore(1)
            attempts = 0

            async def fake_impl(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                raise RuntimeError("Responses HTTP %d: do not retry" % status)

            bot._run_responses_ai_impl = fake_impl
            with self.assertRaisesRegex(RuntimeError, "HTTP %d" % status):
                asyncio.run(bot.run_responses_ai([], None))
            self.assertEqual(attempts, 1)
            self.assertEqual(bot.global_budget.calls, 1)

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

    def test_7z_directory_entries_do_not_count_as_skipped_files(self):
        import tempfile
        from pathlib import Path
        import py7zr

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "catatan").mkdir()
            (root / "data").mkdir()
            (root / "README.txt").write_text(
                "PAKET UJI NOMOR 13 - ARSIP 7Z\\nTEST-13-7Z-ROOT\\n",
                encoding="utf-8",
            )
            (root / "catatan" / "observasi.md").write_text(
                "# Catatan Observasi\\nTotal tanaman: 35.\\n",
                encoding="utf-8",
            )
            (root / "data" / "tanaman.csv").write_text(
                "lokasi,jenis,jumlah\\nBlok Utara,Cabai,12\\n"
                "Blok Selatan,Tomat,8\\nBlok Timur,Kangkung,15\\n",
                encoding="utf-8",
            )
            archive_buffer = io.BytesIO()
            with py7zr.SevenZipFile(archive_buffer, mode="w") as archive:
                archive.writeall(str(root), arcname="")

        text, images = self.bot.extract_7z_content(
            archive_buffer.getvalue(), "13_arsip_uji.7z"
        )
        self.assertIn("TEST-13-7Z-ROOT", text)
        self.assertIn("Total tanaman: 35", text)
        self.assertIn("Blok Timur,Kangkung,15", text)
        self.assertIn("3 file; seluruh file dipilih untuk ekstraksi", text)
        self.assertNotIn("dilewati karena batas pemrosesan", text)
        self.assertNotIn("file; 3 dipilih untuk ekstraksi; 2 dilewati", text)
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
                message, remaining_bytes=4, remaining_chars=30
            ))
        self.assertIn("first.docx", result)
        self.assertIn("batas total ukuran", result.lower())
        self.assertNotIn("CONTENT-second.docx", result)


    def test_on_message_processes_ten_mixed_attachments_and_isolates_one_failure(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeRateLimiter:
            def acquire(self, *args, **kwargs):
                return True, 0.0

        class FakeBot:
            user = SimpleNamespace(id=999)
            rate_limiter = FakeRateLimiter()

            def __init__(self):
                self.processed = []
                self.turn = None

            def is_channel_allowed(self, channel, guild):
                return True

            def get_scope_key(self, guild, user_id, channel_id):
                return f"dm_{user_id}_ch_{channel_id}"

            @module.asynccontextmanager
            async def ingress_lock(self, key):
                yield True, None

            async def encode_attachment(self, attachment):
                self.processed.append(attachment.filename)
                if attachment.filename == "03.docx":
                    raise ValueError("simulated parser failure")
                return "[READ %s]" % attachment.filename, [], [], "text"

            async def process_ai_turn(self, **kwargs):
                self.turn = kwargs

        extensions = [".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".zip", ".7z", ".csv"]
        attachments = [
            SimpleNamespace(filename="%02d%s" % (i + 1, ext), size=10, content_type="application/octet-stream")
            for i, ext in enumerate(extensions)
        ]
        author = SimpleNamespace(bot=False, id=42, display_name="tester")
        message = SimpleNamespace(
            author=author,
            guild=None,
            channel=SimpleNamespace(id=7),
            content="Baca semua file ini",
            attachments=attachments,
            reference=None,
        )
        bot = FakeBot()
        with patch.object(module, "bot", bot):
            asyncio.run(module.on_message(message))

        self.assertEqual(len(bot.processed), 10)
        self.assertEqual(len(set(bot.processed)), 10)
        self.assertIsNotNone(bot.turn)
        self.assertIn("READ 10.csv", bot.turn["additional_file_text"])
        self.assertIn("03.docx gagal diproses", bot.turn["additional_file_text"])
        self.assertIn("simulated parser failure", bot.turn["additional_file_text"])

    def test_on_message_caps_eleven_attachments_at_ten_with_notice(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeRateLimiter:
            def acquire(self, *args, **kwargs):
                return True, 0.0

        class FakeBot:
            user = SimpleNamespace(id=999)
            rate_limiter = FakeRateLimiter()

            def __init__(self):
                self.processed = []
                self.turn = None

            def is_channel_allowed(self, channel, guild):
                return True

            def get_scope_key(self, guild, user_id, channel_id):
                return f"dm_{user_id}_ch_{channel_id}"

            @module.asynccontextmanager
            async def ingress_lock(self, key):
                yield True, None

            async def encode_attachment(self, attachment):
                self.processed.append(attachment.filename)
                return "[READ %s]" % attachment.filename, [], [], "text"

            async def process_ai_turn(self, **kwargs):
                self.turn = kwargs

        class FakeMessage:
            async def reply(self, content, **kwargs):
                self.notice = content

        bot = FakeBot()
        message = FakeMessage()
        message.author = SimpleNamespace(bot=False, id=42, display_name="tester")
        message.guild = None
        message.channel = SimpleNamespace(id=7)
        message.content = "Baca semua"
        message.reference = None
        message.attachments = [
            SimpleNamespace(filename="file-%02d.txt" % i, size=10, content_type="text/plain")
            for i in range(11)
        ]
        with patch.object(module, "bot", bot):
            asyncio.run(module.on_message(message))

        self.assertEqual(len(bot.processed), 10)
        self.assertIn("1 lampiran dilewati", message.notice)


    def test_reply_image_failure_does_not_drop_other_replied_images(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeAttachment:
            def __init__(self, filename):
                self.filename = filename

        class FakeMessage:
            attachments = [FakeAttachment("broken.png"), FakeAttachment("good.png")]
            embeds = []

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        async def fake_encode(attachment):
            if attachment.filename == "broken.png":
                raise ValueError("simulated image parser failure")
            return None, ["data:image/jpeg;base64,GOOD"], ["https://cdn.discordapp.com/good.png"], "image"

        bot.encode_attachment = fake_encode
        message = SimpleNamespace(
            reference=SimpleNamespace(message_id=1, resolved=FakeMessage()),
            channel=SimpleNamespace(),
        )
        with patch.object(module.discord, "Message", FakeMessage):
            local, remote = asyncio.run(bot.resolve_replied_images(message))
        self.assertEqual(local, ["data:image/jpeg;base64,GOOD"])
        self.assertEqual(remote, ["https://cdn.discordapp.com/good.png"])

    def test_replied_documents_are_joined_with_real_newlines(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeAttachment:
            def __init__(self, filename):
                self.filename = filename
                self.size = 1

        class FakeMessage:
            attachments = [FakeAttachment("a.txt"), FakeAttachment("b.txt")]

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        async def fake_encode(attachment):
            return "CONTENT-" + attachment.filename, [], [], "text"

        bot.encode_attachment = fake_encode
        message = SimpleNamespace(
            reference=SimpleNamespace(message_id=1, resolved=FakeMessage()),
            channel=SimpleNamespace(),
        )
        with patch.object(module.discord, "Message", FakeMessage):
            result = asyncio.run(bot.resolve_replied_files(
                message, remaining_bytes=100, remaining_chars=100
            ))
        self.assertIn("\n\n", result)
        self.assertNotIn("\\n\\n", result)

    def test_replied_document_context_is_explicitly_truncated(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeAttachment:
            filename = "large.txt"
            size = 1

        class FakeMessage:
            attachments = [FakeAttachment()]

        bot = module.ClouzerBot.__new__(module.ClouzerBot)

        async def fake_encode(attachment):
            return "0123456789", [], [], "text"

        bot.encode_attachment = fake_encode
        message = SimpleNamespace(
            reference=SimpleNamespace(message_id=1, resolved=FakeMessage()),
            channel=SimpleNamespace(),
        )
        with patch.object(module.discord, "Message", FakeMessage):
            result = asyncio.run(bot.resolve_replied_files(
                message, remaining_bytes=100, remaining_chars=5
            ))
        self.assertIn("01234", result)
        self.assertIn("dipotong", result.lower())


    def test_xlsx_chart_title_and_series_ranges_are_extracted(self):
        import openpyxl
        from openpyxl.chart import BarChart, Reference

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sales"
        ws.append(["Quarter", "Revenue"])
        ws.append(["Q1", 10])
        ws.append(["Q2", 20])
        chart = BarChart()
        chart.title = "Quarterly Revenue Audit"
        data = Reference(ws, min_col=2, min_row=1, max_row=3)
        categories = Reference(ws, min_col=1, min_row=2, max_row=3)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(categories)
        ws.add_chart(chart, "D2")
        out = io.BytesIO()
        wb.save(out)
        wb.close()

        text, _ = self.bot.extract_xlsx_content(out.getvalue(), "chart.xlsx")
        self.assertIn("Quarterly Revenue Audit", text)
        self.assertIn("xl/charts/chart1.xml", text)
        self.assertIn("Sales", text)


    def test_aggregate_attachment_context_cap_is_disclosed_per_file(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeRateLimiter:
            def acquire(self, *args, **kwargs):
                return True, 0.0

        class FakeBot:
            user = SimpleNamespace(id=999)
            rate_limiter = FakeRateLimiter()

            def __init__(self):
                self.turn = None

            def is_channel_allowed(self, channel, guild):
                return True

            def get_scope_key(self, guild, user_id, channel_id):
                return f"dm_{user_id}_ch_{channel_id}"

            @module.asynccontextmanager
            async def ingress_lock(self, key):
                yield True, None

            async def encode_attachment(self, attachment):
                return ("X" * 120, [], [], "text")

            async def process_ai_turn(self, **kwargs):
                self.turn = kwargs

        class FakeMessage:
            async def reply(self, content, **kwargs):
                self.notice = content

        bot = FakeBot()
        message = FakeMessage()
        message.author = SimpleNamespace(bot=False, id=42, display_name="tester")
        message.guild = None
        message.channel = SimpleNamespace(id=7)
        message.content = "Baca semua"
        message.reference = None
        message.attachments = [
            SimpleNamespace(filename="first.txt", size=10, content_type="text/plain"),
            SimpleNamespace(filename="second.txt", size=10, content_type="text/plain"),
        ]
        with patch.object(module, "bot", bot), patch.object(module, "MAX_ATTACHMENT_CONTEXT_CHARS", 100):
            asyncio.run(module.on_message(message))

        context = bot.turn["additional_file_text"]
        self.assertIn("batas gabungan seluruh file", context)
        self.assertIn("first.txt", context)
        self.assertIn("second.txt", context)
        self.assertGreaterEqual(context.count("KONTEN TIDAK SEPENUHNYA MASUK KONTEKS AI"), 2)


    def test_same_user_queue_lock_is_fifo_and_cleans_up_after_cancellation(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.guild_locks = {}
        bot.guild_lock_refs = {}

        async def run():
            order = []
            entered = asyncio.Event()
            release = asyncio.Event()

            async def first():
                async with bot.guild_lock("same-user-scope"):
                    order.append("first")
                    entered.set()
                    await release.wait()

            async def second():
                await entered.wait()
                async with bot.guild_lock("same-user-scope"):
                    order.append("second")

            first_task = asyncio.create_task(first())
            await entered.wait()
            second_task = asyncio.create_task(second())
            await asyncio.sleep(0)
            second_task.cancel()
            try:
                await second_task
            except asyncio.CancelledError:
                pass
            release.set()
            await first_task
            return order

        order = asyncio.run(run())
        self.assertEqual(order, ["first"])
        self.assertEqual(bot.guild_lock_refs, {})
        self.assertEqual(bot.guild_locks, {})

    def test_conversation_scope_is_separated_by_user(self):
        from types import SimpleNamespace

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        guild = SimpleNamespace(id=123)
        first = bot.get_scope_key(guild, user_id=10, channel_id=20)
        second = bot.get_scope_key(guild, user_id=11, channel_id=20)
        self.assertNotEqual(first, second)


    def test_failed_turn_history_keeps_user_request_and_removes_partial_assistant(self):
        prior_user = {"role": "user", "content": "previous request"}
        prior_assistant = {"role": "assistant", "content": "previous answer"}
        current_user = {"role": "user", "content": "important current request"}
        partial_assistant = {"role": "assistant", "content": "partial draft"}
        history = [prior_user, prior_assistant, current_user, partial_assistant]

        module.preserve_failed_turn_history(history, current_user, "⚠️ Stream terputus")

        self.assertEqual(history[0], prior_user)
        self.assertEqual(history[1], prior_assistant)
        self.assertEqual(history[2], current_user)
        self.assertEqual(history[3]["role"], "assistant")
        self.assertIn("Stream terputus", history[3]["content"])
        self.assertNotIn("partial draft", str(history))

    def test_stream_interruption_carries_partial_text(self):
        exc = module.ResponsesStreamInterrupted(
            "stream disconnected", partial_text="jawaban parsial"
        )
        self.assertEqual(str(exc), "stream disconnected")
        self.assertEqual(exc.partial_text, "jawaban parsial")


    def test_real_responses_stream_parser_preserves_partial_text_on_eof(self):
        import asyncio

        class FakeStream:
            def __init__(self, chunks):
                self.chunks = list(chunks)

            async def read(self, _size):
                return self.chunks.pop(0) if self.chunks else b""

        class FakeResponse:
            status = 200

            def __init__(self, stream):
                self.content = stream

        class FakeRequest:
            def __init__(self, response):
                self.response = response

            async def __aenter__(self):
                return self.response

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSession:
            def post(self, *args, **kwargs):
                event = b'data: {"type":"response.output_text.delta","delta":"PARTIAL ANSWER"}\n'
                return FakeRequest(FakeResponse(FakeStream([event, b""])))

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.http_session = FakeSession()
        bot.to_responses_input = lambda messages: ("", [])

        async def run():
            with self.assertRaises(module.ResponsesStreamInterrupted) as caught:
                await bot._run_responses_ai_impl([], None, _diag_timing={})
            self.assertEqual(caught.exception.partial_text, "PARTIAL ANSWER")

        asyncio.run(run())


    def test_reply_source_fetch_failure_is_explained(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeMessage:
            pass

        class FakeChannel:
            async def fetch_message(self, message_id):
                raise LookupError("message unavailable")

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        message = SimpleNamespace(
            reference=SimpleNamespace(message_id=555, resolved=None),
            channel=FakeChannel(),
        )
        with patch.object(module.discord, "Message", FakeMessage):
            result = asyncio.run(bot.resolve_replied_files(message))
        self.assertIn("lampiran asli tidak bisa diperiksa", result)
        self.assertIn("Kirim ulang file sumber", result)


    def test_mismatched_pdf_office_and_7z_signatures_are_rejected(self):
        self.assertIn("bukan PDF", module.check_file_signature(".pdf", b"not a pdf"))
        self.assertIn("bukan arsip", module.check_file_signature(".docx", b"not a zip"))
        self.assertIn("bukan 7Z", module.check_file_signature(".7z", b"not a 7z"))
        self.assertIsNone(
            module.check_file_signature(".7z", bytes((55, 122, 188, 175, 39, 28)) + b"payload")
        )


    def test_rejects_encrypted_zip_flag_before_reading_members(self):
        raw = bytearray(self.make_zip([("secret.txt", "encrypted payload")]))
        local = raw.find(b"PK\x03\x04")
        central = raw.find(b"PK\x01\x02")
        self.assertGreaterEqual(local, 0)
        self.assertGreaterEqual(central, 0)
        raw[local + 6] |= 0x01
        raw[central + 8] |= 0x01
        with self.assertRaisesRegex(ValueError, "terenkripsi"):
            self.bot._validate_zip_archive(bytes(raw))

    def test_7z_password_protection_is_rejected(self):
        from unittest.mock import patch

        class FakeArchive:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def needs_password(self):
                return True

        with patch.object(module.py7zr, "SevenZipFile", return_value=FakeArchive()):
            text, images = self.bot.extract_7z_content(b"valid test bytes", "encrypted.7z")
        self.assertIn("terenkripsi", text)
        self.assertEqual(images, [])


    def test_same_scope_lock_processes_waiters_in_fifo_order(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.guild_locks = {}
        bot.guild_lock_refs = {}

        async def run():
            order = []

            async def worker(number):
                async with bot.guild_lock("fifo-scope"):
                    order.append(number)
                    await asyncio.sleep(0.01)

            await asyncio.gather(*(worker(number) for number in range(5)))
            return order

        order = asyncio.run(run())
        self.assertEqual(order, [0, 1, 2, 3, 4])
        self.assertEqual(bot.guild_lock_refs, {})
        self.assertEqual(bot.guild_locks, {})

    def test_different_user_scopes_can_run_concurrently(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.guild_locks = {}
        bot.guild_lock_refs = {}

        async def run():
            active = 0
            maximum = 0
            both_entered = asyncio.Event()
            release = asyncio.Event()

            async def worker(scope):
                nonlocal active, maximum
                async with bot.guild_lock(scope):
                    active += 1
                    maximum = max(maximum, active)
                    if active == 2:
                        both_entered.set()
                    await release.wait()
                    active -= 1

            tasks = [
                asyncio.create_task(worker("guild:1:channel:2:user:10")),
                asyncio.create_task(worker("guild:1:channel:2:user:11")),
            ]
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            release.set()
            await asyncio.gather(*tasks)
            return maximum

        maximum = asyncio.run(run())
        self.assertEqual(maximum, 2)
        self.assertEqual(bot.guild_lock_refs, {})
        self.assertEqual(bot.guild_locks, {})


    def test_7z_path_traversal_member_is_skipped_without_extract(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeArchive:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def needs_password(self):
                return False

            def list(self):
                return [SimpleNamespace(
                    filename="../outside.txt",
                    is_directory=False,
                    is_file=True,
                    is_symlink=False,
                    uncompressed=10,
                    compressed=10,
                )]

            def extract(self, **kwargs):
                raise AssertionError("unsafe member must never be extracted")

        with patch.object(module.py7zr, "SevenZipFile", return_value=FakeArchive()):
            text, images = self.bot.extract_7z_content(b"archive bytes", "unsafe.7z")
        self.assertIn("path tidak aman", text)
        self.assertEqual(images, [])


    def test_audio_container_signatures_are_checked(self):
        valid_samples = {
            ".wav": b"RIFF" + b"\x00" * 4 + b"WAVE" + b"data",
            ".ogg": b"OggS" + b"payload",
            ".opus": b"OggS" + b"payload",
            ".flac": b"fLaC" + b"payload",
            ".webm": bytes((0x1A, 0x45, 0xDF, 0xA3)) + b"payload",
            ".m4a": bytes((0, 0, 0, 24)) + b"ftypM4A " + b"payload",
            ".aac": bytes((0xFF, 0xF1)) + b"payload",
            ".mp3": b"ID3" + b"payload",
        }
        for ext, sample in valid_samples.items():
            with self.subTest(ext=ext):
                self.assertIsNone(module.check_file_signature(ext, sample))
        self.assertIn(
            "format audio",
            module.check_file_signature(".wav", b"not audio"),
        )


    def test_replied_image_failure_does_not_skip_later_images(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeAttachment:
            def __init__(self, filename):
                self.filename = filename

        class FakeMessage:
            attachments = [FakeAttachment("bad.png"), FakeAttachment("good.png")]

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        calls = []

        async def fake_encode(attachment):
            calls.append(attachment.filename)
            if attachment.filename == "bad.png":
                raise ValueError("bad image")
            return None, ["data:image/png;base64,AAAA"], [], "image"

        bot.encode_attachment = fake_encode
        message = SimpleNamespace(
            reference=SimpleNamespace(message_id=9, resolved=FakeMessage()),
            channel=SimpleNamespace(),
        )
        with patch.object(module.discord, "Message", FakeMessage):
            local, remote = asyncio.run(bot.resolve_replied_images(message))
        self.assertEqual(calls, ["bad.png", "good.png"])
        self.assertEqual(local, ["data:image/png;base64,AAAA"])
        self.assertEqual(remote, [])


    def test_new_images_and_replied_images_are_both_forwarded(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeRateLimiter:
            def acquire(self, *args, **kwargs):
                return True, 0.0

        class FakeBot:
            user = SimpleNamespace(id=999)
            rate_limiter = FakeRateLimiter()

            def __init__(self):
                self.turn = None

            def is_channel_allowed(self, channel, guild):
                return True

            def get_scope_key(self, guild, user_id, channel_id):
                return f"dm_{user_id}_ch_{channel_id}"

            @module.asynccontextmanager
            async def ingress_lock(self, key):
                yield True, None

            async def encode_attachment(self, attachment):
                return None, ["data:image/png;base64,NEW"], [], "image"

            async def resolve_replied_files(self, message, **kwargs):
                return ""

            async def resolve_replied_images(self, message):
                return ["data:image/png;base64,REPLIED"], ["https://cdn.discordapp.com/replied.png"]

            async def process_ai_turn(self, **kwargs):
                self.turn = kwargs

        bot = FakeBot()
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id=42, display_name="tester"),
            guild=None,
            channel=SimpleNamespace(id=7),
            content="Edit gambar ini",
            attachments=[SimpleNamespace(
                filename="new.png", size=10, content_type="image/png"
            )],
            reference=SimpleNamespace(message_id=8),
        )
        with patch.object(module, "bot", bot):
            asyncio.run(module.on_message(message))

        self.assertEqual(
            bot.turn["image_data_urls"],
            ["data:image/png;base64,NEW", "data:image/png;base64,REPLIED"],
        )
        self.assertEqual(
            bot.turn["llm_image_urls"],
            ["https://cdn.discordapp.com/replied.png"],
        )


    def test_environment_overrides_cannot_exceed_safety_caps(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {
            "MAX_ATTACHMENTS_PER_MESSAGE": "99",
            "MAX_VISION_IMAGES_PER_TURN": "99",
            "MAX_ATTACHMENT_TOTAL_MB": "999",
            "MAX_CONCURRENT_ATTACHMENTS": "99",
            "MAX_CONCURRENT_AI_CALLS": "99",
            "MAX_CONCURRENT_IMAGE_CALLS": "99",
            "MAX_CONCURRENT_AUDIO_CALLS": "99",
            "MAX_RESPONSES_OUTPUT_CHARS": "999999",
            "MAX_DOCUMENT_TEXT_CHARS": "999999",
            "MAX_ATTACHMENT_CONTEXT_CHARS": "999999",
            "MAX_TEXT_FILE_CHARS": "999999",
            "PROMPT_HISTORY_MAX_CHARS": "9999999",
        }):
            self.assertEqual(module._env_int_capped(
                "MAX_ATTACHMENTS_PER_MESSAGE", 10, minimum=1, maximum=10
            ), 10)
            self.assertEqual(module._env_int_capped(
                "MAX_VISION_IMAGES_PER_TURN", 10, minimum=1, maximum=10
            ), 10)
            self.assertEqual(module._env_int_capped(
                "MAX_ATTACHMENT_TOTAL_MB", 100, minimum=1, maximum=100
            ), 100)
            self.assertEqual(module._env_int_capped(
                "MAX_CONCURRENT_ATTACHMENTS", 3, minimum=1, maximum=5
            ), 5)
            self.assertEqual(module._env_int_capped(
                "MAX_CONCURRENT_AI_CALLS", 4, minimum=1, maximum=8
            ), 8)
            self.assertEqual(module._env_int_capped(
                "MAX_CONCURRENT_IMAGE_CALLS", 2, minimum=1, maximum=4
            ), 4)
            self.assertEqual(module._env_int_capped(
                "MAX_CONCURRENT_AUDIO_CALLS", 2, minimum=1, maximum=4
            ), 4)
            self.assertEqual(module._env_int_capped(
                "MAX_RESPONSES_OUTPUT_CHARS", 65536, minimum=1024, maximum=100000
            ), 100000)
            self.assertEqual(module._env_int_capped(
                "MAX_DOCUMENT_TEXT_CHARS", 50000, minimum=5000, maximum=100000
            ), 100000)
            self.assertEqual(module._env_int_capped(
                "MAX_ATTACHMENT_CONTEXT_CHARS", 100000, minimum=10000, maximum=200000
            ), 200000)
            self.assertEqual(module._env_int_capped(
                "MAX_TEXT_FILE_CHARS", 60000, minimum=1000, maximum=100000
            ), 100000)
            self.assertEqual(module._env_int_capped(
                "PROMPT_HISTORY_MAX_CHARS", 240000, minimum=20000, maximum=500000
            ), 500000)

    def test_queue_full_rejects_new_turn_and_cleans_lock_registry(self):
        import asyncio
        from unittest.mock import patch

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.guild_locks = {}
        bot.guild_lock_refs = {}

        class FakeStatus:
            content = ""

            async def edit(self, content):
                self.content = content

        async def run():
            entered = asyncio.Event()
            release = asyncio.Event()
            order = []

            async def holder():
                async with bot.guild_lock("bounded-queue"):
                    entered.set()
                    await release.wait()
                    order.append("holder")

            async def waiter():
                status = FakeStatus()
                async with bot.guild_lock("bounded-queue", status_message=status) as acquired:
                    if acquired:
                        order.append("waiter")
                return status

            with patch.object(module, "MAX_QUEUE_PENDING_PER_SCOPE", 1):
                first = asyncio.create_task(holder())
                await entered.wait()
                second_status = FakeStatus()
                async def queued_waiter():
                    async with bot.guild_lock("bounded-queue", status_message=second_status) as acquired:
                        if acquired:
                            order.append("second")
                second = asyncio.create_task(queued_waiter())
                while bot.guild_lock_refs.get("bounded-queue") != 2:
                    await asyncio.sleep(0)
                rejected_status = FakeStatus()
                async with bot.guild_lock("bounded-queue", status_message=rejected_status) as acquired:
                    self.assertFalse(acquired)
                self.assertIn("antrean percakapan penuh", rejected_status.content.lower())
                release.set()
                await asyncio.gather(first, second)
            self.assertEqual(order, ["holder", "second"])
            self.assertEqual(bot.guild_locks, {})
            self.assertEqual(bot.guild_lock_refs, {})

        asyncio.run(run())

    def test_queue_wait_timeout_updates_status_and_cleans_lock_registry(self):
        import asyncio
        from unittest.mock import patch

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.guild_locks = {}
        bot.guild_lock_refs = {}

        class FakeStatus:
            content = ""

            async def edit(self, content):
                self.content = content

        async def run():
            entered = asyncio.Event()
            release = asyncio.Event()

            async def holder():
                async with bot.guild_lock("timeout-queue"):
                    entered.set()
                    await release.wait()

            first = asyncio.create_task(holder())
            await entered.wait()
            status = FakeStatus()
            with patch.object(module, "QUEUE_WAIT_TIMEOUT_SECONDS", 0.01):
                async with bot.guild_lock("timeout-queue", status_message=status) as acquired:
                    self.assertFalse(acquired)
            self.assertIn("waktu tunggu antrean habis", status.content.lower())
            release.set()
            await first
            self.assertEqual(bot.guild_locks, {})
            self.assertEqual(bot.guild_lock_refs, {})

        asyncio.run(run())

    def test_cancelled_queue_waiter_updates_status_and_releases_reference(self):
        import asyncio

        bot = module.ClouzerBot.__new__(module.ClouzerBot)
        bot.guild_locks = {}
        bot.guild_lock_refs = {}

        class FakeStatus:
            content = ""

            async def edit(self, content):
                self.content = content

        async def run():
            entered = asyncio.Event()
            release = asyncio.Event()
            status = FakeStatus()

            async def holder():
                async with bot.guild_lock("cancel-queue"):
                    entered.set()
                    await release.wait()

            async def waiter():
                async with bot.guild_lock("cancel-queue", status_message=status):
                    pass

            first = asyncio.create_task(holder())
            await entered.wait()
            second = asyncio.create_task(waiter())
            while bot.guild_lock_refs.get("cancel-queue") != 2:
                await asyncio.sleep(0)
            second.cancel()
            try:
                await second
            except asyncio.CancelledError:
                pass
            self.assertIn("dibatalkan", status.content.lower())
            release.set()
            await first
            self.assertEqual(bot.guild_locks, {})
            self.assertEqual(bot.guild_lock_refs, {})

        asyncio.run(run())


    def test_safe_xml_parser_allows_plain_doctype_but_rejects_entities(self):
        plain = module.safe_xml_fromstring(b"<!DOCTYPE svg><svg><title>safe</title></svg>")
        self.assertEqual(plain.tag.rsplit("}", 1)[-1], "svg")

        simple = module.safe_xml_fromstring(
            b'<!DOCTYPE svg [<!ENTITY x "boom">]><svg>&x;</svg>'
        )
        self.assertEqual(simple.text, "boom")

        payloads = [
            b'<!DOCTYPE svg [<!ENTITY a "&b;"><!ENTITY b "&a;">]><svg>&a;</svg>',
            b'<!DOCTYPE svg SYSTEM "file:///etc/passwd"><svg/>',
        ]
        for payload in payloads:
            with self.subTest(payload=payload[:60]):
                with self.assertRaises(Exception):
                    module.safe_xml_fromstring(payload)


    def test_safe_xml_parser_bounds_internal_entity_size_and_references(self):
        oversized = (
            b'<!DOCTYPE root [<!ENTITY a "'
            + (b"A" * 600)
            + b'">]><root>&a;</root>'
        )
        with self.assertRaisesRegex(ValueError, "nilai entity terlalu panjang"):
            module.safe_xml_fromstring(oversized)

        repeated = (
            b'<!DOCTYPE root [<!ENTITY a "x">]><root>'
            + (b"&a;" * 257)
            + b"</root>"
        )
        with self.assertRaisesRegex(ValueError, "terlalu banyak referensi entity"):
            module.safe_xml_fromstring(repeated)


    def test_archive_members_with_mismatched_binary_signatures_are_rejected(self):
        zip_raw = self.make_zip([("misnamed.pdf", b"this is not a PDF")])
        zip_text, _ = self.bot.extract_generic_zip_content(zip_raw, "mismatch.zip")
        self.assertIn("signature tidak cocok", zip_text)
        self.assertIn("bukan PDF", zip_text)

        seven_raw = self.make_7z([("misnamed.pdf", b"this is not a PDF")])
        seven_text, _ = self.bot.extract_7z_content(seven_raw, "mismatch.7z")
        self.assertIn("signature tidak cocok", seven_text)
        self.assertIn("bukan PDF", seven_text)

    def test_default_attachment_limit_is_ten(self):
        self.assertEqual(module.MAX_ATTACHMENTS_PER_MESSAGE, 10)

    def test_chat_and_file_requests_enter_fifo_queue_without_cooldown_rejection(self):
        self.assertEqual(module._RATE_DEFAULTS["chat"], (0.0, 10, 60.0))
        self.assertEqual(module._RATE_DEFAULTS["file"], (0.0, 6, 300.0))

    def test_rate_limit_message_does_not_claim_rejected_turn_was_queued(self):
        message = module.rate_limit_text(4)
        self.assertIn("belum masuk antrean", message)
        self.assertIn("4 detik", message)



    def test_vision_selection_prefers_local_image_over_same_attachment_cdn_url(self):
        local = ["data:image/png;base64,LOCAL"]
        remote = ["https://cdn.discordapp.com/attachments/1/same.png"]
        self.assertEqual(module.select_vision_images(local, remote), local)

    def test_vision_selection_uses_remote_images_only_as_fallback(self):
        remote = ["https://cdn.discordapp.com/attachments/1/fallback.png"]
        self.assertEqual(module.select_vision_images([], remote), remote)

    def test_zip_document_image_warning_counts_only_images_actually_skipped(self):
        from unittest.mock import patch

        pdf = self.make_pdf("PDF IMAGE BUDGET TEST")
        two_images = ["data:image/png;base64,ONE", "data:image/png;base64,TWO"]
        with patch.object(self.bot, "extract_pdf_content", return_value=("", two_images)):
            raw = self.make_zip([("images.pdf", pdf)])
            text, images = self.bot.extract_generic_zip_content(
                raw, "images.zip", _image_budget=1
            )
        self.assertEqual(images, [two_images[0]])
        self.assertIn("1 gambar dari images.pdf dilewati", text)

    def test_zip_document_image_warning_is_absent_when_nothing_was_skipped(self):
        from unittest.mock import patch

        pdf = self.make_pdf("PDF IMAGE BUDGET TEST")
        one_image = ["data:image/png;base64,ONE"]
        with patch.object(self.bot, "extract_pdf_content", return_value=("", one_image)):
            raw = self.make_zip([("images.pdf", pdf)])
            text, images = self.bot.extract_generic_zip_content(
                raw, "images.zip", _image_budget=1
            )
        self.assertEqual(images, one_image)
        self.assertNotIn("dilewati karena batas visual per giliran", text)

    def test_nested_zip_truncation_is_explicit(self):
        inner = self.make_zip([("large.txt", "X" * 30000)])
        outer = self.make_zip([("nested/inner.zip", inner)])
        text, _ = self.bot.extract_generic_zip_content(outer, "outer.zip")
        self.assertIn("KONTEN ARSIP BERSARANG DIPOTONG", text)
        self.assertIn("karakter tidak dimasukkan", text)

    def test_7z_nested_zip_is_read_with_explicit_truncation_marker(self):
        inner = self.make_zip([("large.txt", "Y" * 30000)])
        outer = self.make_7z([("nested/inner.zip", inner)])
        text, _ = self.bot.extract_7z_content(outer, "outer.7z")
        self.assertIn("KONTEN ZIP DI DALAM 7Z DIPOTONG", text)
        self.assertIn("karakter tidak dimasukkan", text)

    def test_7z_text_member_truncation_is_explicit(self):
        raw = self.make_7z([("large.txt", "Z" * 30050)])
        text, _ = self.bot.extract_7z_content(raw, "large.7z")
        self.assertIn("KONTEN ANGGOTA 7Z DIPOTONG", text)
        self.assertIn("karakter tidak dimasukkan", text)

    def test_nested_zips_share_one_aggregate_decompression_budget(self):
        inner_one = self.make_zip([("first.txt", "A" * 2000)])
        inner_two = self.make_zip([("second.txt", "SECOND_NESTED_PAYLOAD" + "B" * 1980)])
        outer = self.make_zip([
            ("nested/one.zip", inner_one),
            ("nested/two.zip", inner_two),
        ])
        budget = {"bytes_read": 0, "max_bytes": 2500}
        text, _ = self.bot.extract_generic_zip_content(
            outer, "outer.zip", _budget_state=budget
        )
        self.assertIn("batas total byte gabungan ZIP bersarang", text)
        self.assertIn("first.txt", text)
        self.assertNotIn("SECOND_NESTED_PAYLOAD", text)
        self.assertLessEqual(budget["bytes_read"], budget["max_bytes"])

    def test_status_message_falls_back_when_reply_target_cannot_be_replied_to(self):
        class ReplyTarget:
            async def reply(self, *args, **kwargs):
                raise RuntimeError("simulated deleted reply target")

        class Channel:
            def __init__(self):
                self.sent = []

            async def send(self, content):
                self.sent.append(content)
                return "channel-status"

        async def run():
            channel = Channel()
            result = await module.send_turn_status_message(channel, ReplyTarget())
            self.assertEqual(result, "channel-status")
            self.assertEqual(len(channel.sent), 1)
            self.assertIn("Menunggu giliran", channel.sent[0])

        asyncio.run(run())

    def test_last_user_image_context_respects_vision_cap_and_deduplicates(self):
        self.bot.last_channel_images = module.OrderedDict()
        images = [f"data:image/png;base64,IMAGE_{i}" for i in range(module.MAX_VISION_IMAGES_PER_TURN + 3)]
        images.insert(1, images[0])
        self.bot.set_last_user_image(1, 2, 3, images)
        stored = self.bot.get_last_user_image(1, 2, 3)
        self.assertEqual(len(stored), module.MAX_VISION_IMAGES_PER_TURN)
        self.assertEqual(len(set(stored)), len(stored))

    def test_ingress_lock_preserves_fifo_before_attachment_work(self):
        async def run():
            bot = module.ClouzerBot.__new__(module.ClouzerBot)
            bot.ingress_locks = {}
            bot.ingress_lock_refs = {}
            order = []
            first_started = asyncio.Event()
            release_first = asyncio.Event()

            async def worker(name, hold=False):
                async with bot.ingress_lock("same-scope") as (acquired, reason):
                    self.assertTrue(acquired, reason)
                    order.append(name)
                    if hold:
                        first_started.set()
                        await release_first.wait()

            first = asyncio.create_task(worker("first", hold=True))
            await first_started.wait()
            second = asyncio.create_task(worker("second"))
            await asyncio.sleep(0)
            third = asyncio.create_task(worker("third"))
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.gather(first, second, third)
            self.assertEqual(order, ["first", "second", "third"])
            self.assertEqual(bot.ingress_lock_refs, {})
            self.assertEqual(bot.ingress_locks, {})

        asyncio.run(run())

    def test_ingress_lock_rejects_excess_waiters(self):
        async def run():
            bot = module.ClouzerBot.__new__(module.ClouzerBot)
            bot.ingress_locks = {}
            bot.ingress_lock_refs = {}
            lock = asyncio.Lock()
            await lock.acquire()
            bot.ingress_locks["scope"] = lock
            bot.ingress_lock_refs["scope"] = module.MAX_QUEUE_PENDING_PER_SCOPE + 1
            async with bot.ingress_lock("scope") as (acquired, reason):
                self.assertFalse(acquired)
                self.assertEqual(reason, "full")
            lock.release()

        asyncio.run(run())

    def test_ingress_lock_cancellation_cleans_waiter_and_preserves_next_turn(self):
        async def run():
            bot = module.ClouzerBot.__new__(module.ClouzerBot)
            bot.ingress_locks = {}
            bot.ingress_lock_refs = {}
            lock = asyncio.Lock()
            await lock.acquire()
            bot.ingress_locks["scope"] = lock
            bot.ingress_lock_refs["scope"] = 1

            async def waiter():
                async with bot.ingress_lock("scope"):
                    self.fail("cancelled waiter must not acquire")

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(bot.ingress_lock_refs["scope"], 1)
            lock.release()
            bot.ingress_lock_refs["scope"] = 0
            bot.ingress_locks.pop("scope", None)

            async with bot.ingress_lock("scope") as (acquired, reason):
                self.assertTrue(acquired, reason)
            self.assertEqual(bot.ingress_lock_refs, {})
            self.assertEqual(bot.ingress_locks, {})

        asyncio.run(run())

    def test_on_message_waits_for_ingress_turn_before_parsing_next_attachment(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeRateLimiter:
            def acquire(self, *args, **kwargs):
                return True, 0.0

        class FakeBot:
            user = SimpleNamespace(id=999)
            rate_limiter = FakeRateLimiter()
            ingress_lock = module.ClouzerBot.ingress_lock

            def __init__(self):
                self.ingress_locks = {}
                self.ingress_lock_refs = {}

            def get_scope_key(self, guild, user_id, channel_id):
                return f"dm_{user_id}_ch_{channel_id}"

            def is_channel_allowed(self, channel, guild):
                return True

        async def run():
            bot = FakeBot()
            parsed = []
            first_parse_started = asyncio.Event()
            release_first = asyncio.Event()
            completed = []

            async def encode_attachment(attachment):
                parsed.append(attachment.filename)
                if attachment.filename == "first.txt":
                    first_parse_started.set()
                    await release_first.wait()
                return "CONTENT-" + attachment.filename, [], [], "text"

            async def process_ai_turn(**kwargs):
                completed.append(kwargs["clean_content"])

            bot.encode_attachment = encode_attachment
            bot.process_ai_turn = process_ai_turn
            channel = SimpleNamespace(id=7)

            def make_message(label):
                return SimpleNamespace(
                    author=SimpleNamespace(bot=False, id=42, display_name="tester"),
                    guild=None,
                    channel=channel,
                    content=label,
                    attachments=[SimpleNamespace(
                        filename=label + ".txt", size=10, content_type="text/plain"
                    )],
                    reference=None,
                )

            with patch.object(module, "bot", bot):
                first = asyncio.create_task(module.on_message(make_message("first")))
                await first_parse_started.wait()
                second = asyncio.create_task(module.on_message(make_message("second")))
                await asyncio.sleep(0)
                self.assertEqual(parsed, ["first.txt"])
                release_first.set()
                await asyncio.gather(first, second)

            self.assertEqual(parsed, ["first.txt", "second.txt"])
            self.assertEqual(completed, ["first", "second"])
            self.assertEqual(bot.ingress_lock_refs, {})
            self.assertEqual(bot.ingress_locks, {})
        
        asyncio.run(run())

    def test_container_signatures_require_format_specific_structure(self):
        valid_cases = {
            ".docx": [
                ("[Content_Types].xml", "<Types/>"),
                ("word/document.xml", "<document/>"),
            ],
            ".pptx": [
                ("[Content_Types].xml", "<Types/>"),
                ("ppt/presentation.xml", "<presentation/>"),
            ],
            ".xlsx": [
                ("[Content_Types].xml", "<Types/>"),
                ("xl/workbook.xml", "<workbook/>"),
            ],
        }
        for ext, entries in valid_cases.items():
            with self.subTest(ext=ext):
                self.assertIsNone(module.check_file_signature(ext, self.make_zip(entries)))
                self.assertIsNotNone(
                    module.check_file_signature(ext, self.make_zip([("readme.txt", "just text")]))
                )

    def test_odf_and_epub_signatures_validate_mimetype(self):
        odf_types = {
            ".odt": b"application/vnd.oasis.opendocument.text",
            ".ods": b"application/vnd.oasis.opendocument.spreadsheet",
            ".odp": b"application/vnd.oasis.opendocument.presentation",
        }
        def make_odf(mimetype, compress_mimetype=False):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as archive:
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_DEFLATED if compress_mimetype else zipfile.ZIP_STORED
                archive.writestr(info, mimetype)
                archive.writestr("content.xml", b"<document/>")
            return out.getvalue()

        for ext, mimetype in odf_types.items():
            with self.subTest(ext=ext):
                self.assertIsNone(module.check_file_signature(ext, make_odf(mimetype)))
                wrong = make_odf(b"application/zip")
                self.assertIsNotNone(module.check_file_signature(ext, wrong))
                compressed = make_odf(mimetype, compress_mimetype=True)
                self.assertIsNotNone(module.check_file_signature(ext, compressed))

        epub = io.BytesIO()
        with zipfile.ZipFile(epub, "w") as archive:
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, b"application/epub+zip")
            archive.writestr("META-INF/container.xml", b"<container/>")
        self.assertIsNone(module.check_file_signature(".epub", epub.getvalue()))

        invalid_epub = self.make_zip([("mimetype", "application/epub+zip")])
        self.assertIsNotNone(module.check_file_signature(".epub", invalid_epub))

    def test_signature_checker_rejects_truncated_and_mislabeled_binary_inputs(self):
        cases = [
            (".pdf", b"not a pdf"),
            (".7z", b"not a 7z"),
            (".docx", b"PK\\x03\\x04not-a-container"),
            (".pptx", b"PK\\x03\\x04not-a-container"),
            (".xlsx", b"PK\\x03\\x04not-a-container"),
            (".rtf", b"plain text"),
            (".wav", b"RIFFxxxxNOPE"),
            (".flac", b"not flac"),
            (".ogg", b"not ogg"),
            (".mp3", b"not mp3"),
        ]
        for ext, raw in cases:
            with self.subTest(ext=ext):
                self.assertIsNotNone(module.check_file_signature(ext, raw))

    def test_zip_truncation_and_header_mutation_corpus_is_rejected(self):
        raw = self.make_zip([("payload.txt", "bounded archive mutation fixture")])
        truncated_variants = [raw[:-cut] for cut in (1, 2, 4, 8, 16, 32)]
        for index, candidate in enumerate(truncated_variants):
            with self.subTest(truncated=index):
                with self.assertRaises(Exception):
                    self.bot._validate_zip_archive(candidate)

        # zipfile's central-directory preflight can open archives whose local
        # file header was altered; opening the member must still reject it.
        for offset in range(4):
            with self.subTest(local_header_offset=offset):
                mutated = bytearray(raw)
                mutated[offset] ^= 0xFF
                text, _ = self.bot.extract_generic_zip_content(bytes(mutated), "mutated.zip")
                self.assertIn("Anggota gagal dibaca", text)
                self.assertNotIn("bounded archive mutation fixture", text)

    def test_audio_signature_matrix_accepts_supported_container_headers(self):
        valid = {
            ".mp3": b"ID3" + b"\x00" * 8,
            ".wav": b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 8,
            ".ogg": b"OggS" + b"\x00" * 8,
            ".opus": b"OggS" + b"\x00" * 8,
            ".flac": b"fLaC" + b"\x00" * 8,
            ".webm": bytes((0x1A, 0x45, 0xDF, 0xA3)) + b"\x00" * 8,
            ".m4a": b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8,
            ".aac": b"ADIF" + b"\x00" * 8,
        }
        for ext, raw in valid.items():
            with self.subTest(ext=ext):
                self.assertIsNone(module.check_file_signature(ext, raw))

    def test_vector_design_file_signatures_validate_real_format_markers(self):
        self.assertIsNone(
            module.check_file_signature(".svg", b'<svg xmlns="http://www.w3.org/2000/svg"><title>ok</title></svg>')
        )
        self.assertIsNotNone(module.check_file_signature(".svg", b"<html>not svg</html>"))
        self.assertIsNotNone(module.check_file_signature(".svg", b"<svg><!ENTITY broken></svg>"))
        self.assertIsNone(module.check_file_signature(".eps", b"%!PS-Adobe-3.0 EPSF-3.0\\n%%BoundingBox: 0 0 10 10"))
        self.assertIsNone(module.check_file_signature(".ai", b"%PDF-1.7\\n%Illustrator"))
        self.assertIsNone(module.check_file_signature(".ai", b"%!PS-Adobe-3.0\\n%%Creator: Adobe Illustrator"))
        self.assertIsNotNone(module.check_file_signature(".eps", b"plain text"))
        self.assertIsNotNone(module.check_file_signature(".ai", b"plain text"))

    def test_last_user_image_context_isolated_by_user_and_expires(self):
        import time

        self.bot.last_channel_images = module.OrderedDict()
        first = ["data:image/png;base64,USER_ONE"]
        second = ["data:image/png;base64,USER_TWO"]
        self.bot.set_last_user_image(1, 2, 10, first)
        self.bot.set_last_user_image(1, 2, 20, second)

        self.assertEqual(self.bot.get_last_user_image(1, 2, 10), first)
        self.assertEqual(self.bot.get_last_user_image(1, 2, 20), second)
        self.assertIsNone(self.bot.get_last_user_image(1, 2, 30))

        self.bot.last_channel_images[(1, 2, 10)] = (
            time.monotonic() - module.IMAGE_CONTEXT_TTL_SECONDS - 1,
            first,
        )
        self.assertIsNone(self.bot.get_last_user_image(1, 2, 10))
        self.assertNotIn((1, 2, 10), self.bot.last_channel_images)

    def test_pie_chart_series_are_extracted_from_xlsx_and_pptx(self):
        import openpyxl
        from openpyxl.chart import PieChart, Reference
        from pptx import Presentation
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.util import Inches

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Segment", "Share"])
        ws.append(["Alpha", 30])
        ws.append(["Beta", 70])
        pie = PieChart()
        pie.title = "XLSX Segment Share"
        pie.add_data(Reference(ws, min_col=2, min_row=1, max_row=3), titles_from_data=True)
        pie.set_categories(Reference(ws, min_col=1, min_row=2, max_row=3))
        ws.add_chart(pie, "D2")
        xlsx = io.BytesIO()
        wb.save(xlsx)
        wb.close()
        xlsx_text, _ = self.bot.extract_xlsx_content(xlsx.getvalue(), "pie.xlsx")
        self.assertIn("XLSX Segment Share", xlsx_text)
        self.assertIn("Alpha", xlsx_text)
        self.assertIn("30", xlsx_text)

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        data = CategoryChartData()
        data.categories = ["Alpha", "Beta"]
        data.add_series("Share", (30, 70))
        chart = slide.shapes.add_chart(
            XL_CHART_TYPE.PIE, Inches(1), Inches(1), Inches(5), Inches(3), data
        ).chart
        chart.has_title = True
        chart.chart_title.text_frame.text = "PPTX Segment Share"
        pptx = io.BytesIO()
        prs.save(pptx)
        pptx_text, _ = self.bot.extract_pptx_content(pptx.getvalue(), "pie.pptx")
        self.assertIn("PPTX Segment Share", pptx_text)
        self.assertIn("Share", pptx_text)
        self.assertIn("30", pptx_text)
        self.assertIn("70", pptx_text)

    def test_core_metadata_is_extracted_from_docx_pptx_and_xlsx(self):
        from docx import Document
        from pptx import Presentation
        from pptx.util import Inches
        import openpyxl

        doc = Document()
        doc.add_paragraph("DOCX BODY")
        doc.core_properties.title = "DOCX AUDIT TITLE"
        doc.core_properties.author = "DOCX AUDIT AUTHOR"
        doc.core_properties.subject = "DOCX AUDIT SUBJECT"
        docx_bytes = io.BytesIO()
        doc.save(docx_bytes)
        docx_text, _ = self.bot.extract_docx_content(docx_bytes.getvalue(), "metadata.docx")
        self.assertIn("DOCX AUDIT TITLE", docx_text)
        self.assertIn("DOCX AUDIT AUTHOR", docx_text)
        self.assertIn("DOCX AUDIT SUBJECT", docx_text)

        prs = Presentation()
        prs.slides.add_slide(prs.slide_layouts[6])
        prs.core_properties.title = "PPTX AUDIT TITLE"
        prs.core_properties.author = "PPTX AUDIT AUTHOR"
        pptx_bytes = io.BytesIO()
        prs.save(pptx_bytes)
        pptx_text, _ = self.bot.extract_pptx_content(pptx_bytes.getvalue(), "metadata.pptx")
        self.assertIn("PPTX AUDIT TITLE", pptx_text)
        self.assertIn("PPTX AUDIT AUTHOR", pptx_text)

        wb = openpyxl.Workbook()
        wb.active["A1"] = "XLSX BODY"
        wb.properties.title = "XLSX AUDIT TITLE"
        wb.properties.creator = "XLSX AUDIT CREATOR"
        xlsx_bytes = io.BytesIO()
        wb.save(xlsx_bytes)
        wb.close()
        xlsx_text, _ = self.bot.extract_xlsx_content(xlsx_bytes.getvalue(), "metadata.xlsx")
        self.assertIn("XLSX AUDIT TITLE", xlsx_text)
        self.assertIn("XLSX AUDIT CREATOR", xlsx_text)

if __name__ == "__main__":
    unittest.main()


class NestedVectorSignatureCoverageTests(unittest.TestCase):
    @staticmethod
    def make_zip(entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
        return buffer.getvalue()

    def setUp(self):
        self.bot = module.ClouzerBot.__new__(module.ClouzerBot)

    def test_invalid_svg_eps_and_ai_members_are_rejected_by_signature(self):
        raw = self.make_zip([
            ("bad.svg", b"<html><body>not svg</body></html>"),
            ("bad.eps", b"plain text, not postscript"),
            ("bad.ai", b"not an illustrator or PDF document"),
        ])
        text, _ = self.bot.extract_generic_zip_content(raw, "vectors.zip")
        self.assertIn("bad.svg", text)
        self.assertIn("bad.eps", text)
        self.assertIn("bad.ai", text)
        self.assertEqual(text.count("ditolak karena signature tidak cocok"), 3)

    def test_valid_vector_members_pass_signature_gate_but_are_not_claimed_parsed(self):
        raw = self.make_zip([
            ("valid.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="2" height="2"/></svg>'),
            ("valid.eps", b"%!PS-Adobe-3.0 EPSF-3.0\\n%%BoundingBox: 0 0 2 2\\n"),
            ("valid.ai", b"%PDF-1.4\\n% Illustrator-compatible PDF\\n"),
        ])
        text, _ = self.bot.extract_generic_zip_content(raw, "vectors.zip")
        self.assertEqual(text.count("Format anggota belum didukung"), 3)
        self.assertNotIn("ditolak karena signature tidak cocok", text)
