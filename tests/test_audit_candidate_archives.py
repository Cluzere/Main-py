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
            ".wav": b"RIFF" + b"\\x00" * 4 + b"WAVE" + b"data",
            ".ogg": b"OggS" + b"payload",
            ".opus": b"OggS" + b"payload",
            ".flac": b"fLaC" + b"payload",
            ".webm": bytes((0x1A, 0x45, 0xDF, 0xA3)) + b"payload",
            ".m4a": b"\\x00\\x00\\x00\\x18ftypM4A " + b"payload",
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

    def test_default_attachment_limit_is_ten(self):
        self.assertEqual(module.MAX_ATTACHMENTS_PER_MESSAGE, 10)

    def test_chat_and_file_requests_enter_fifo_queue_without_cooldown_rejection(self):
        self.assertEqual(module._RATE_DEFAULTS["chat"], (0.0, 10, 60.0))
        self.assertEqual(module._RATE_DEFAULTS["file"], (0.0, 6, 300.0))

    def test_rate_limit_message_does_not_claim_rejected_turn_was_queued(self):
        message = module.rate_limit_text(4)
        self.assertIn("belum masuk antrean", message)
        self.assertIn("4 detik", message)


if __name__ == "__main__":
    unittest.main()
