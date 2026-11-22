import ast
import json
import logging
import os
import types
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

SOURCE = Path(__file__).with_name("main_final_hardened_candidate.py")
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))


def extract_method(class_name, method_name):
    for node in TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == method_name:
                    module = ast.Module(body=[member], type_ignores=[])
                    ast.fix_missing_locations(module)
                    namespace = {
                        "os": os, "json": json, "logging": logging,
                        "MEMORY_FILE": "", "MAX_MEMORY_FILE_BYTES": 1024,
                        "IMAGE_EXTS": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
                        "AUDIO_EXTS": {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".webm", ".aac", ".opus"},
                        "FULL_CONTENT_EXTS": {".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv", ".md", ".xml", ".yml", ".yaml", ".ini", ".log", ".sql", ".sh", ".pdf", ".docx", ".pptx", ".xlsx", ".rtf", ".odt", ".ods", ".odp", ".epub", ".svg", ".eps", ".ai"},
                        "OFFICE_DOC_EXTS": {".pdf", ".docx", ".pptx", ".xlsx", ".rtf", ".odt", ".ods", ".odp", ".epub", ".svg", ".eps", ".ai"},
                        "check_file_signature": lambda ext, data: None,
                    }
                    exec(compile(module, str(SOURCE), "exec"), namespace)
                    return namespace[method_name]
    raise AssertionError(f"method not found: {class_name}.{method_name}")


load_memory_method = extract_method("ClouzerBot", "load_memory")
view_load_method = extract_method("ViewStateStore", "_load")
redaction_filter_method = extract_method("SecretRedactionFilter", "filter")
read_attachment_method = extract_method("ClouzerBot", "_read_attachment_checked")


class MemoryHarness:
    load_memory = load_memory_method

    def _normalize_scope_state(self, value):
        return value if isinstance(value, dict) else {}

    def _scrub_loaded_scope(self, value):
        return value


class RedactionHarness:
    filter = redaction_filter_method


class AttachmentHarness:
    _read_attachment_checked = read_attachment_method


class ViewHarness:
    _load = view_load_method

    def __init__(self, path):
        self.path = path
        self._entries = OrderedDict()

    def _enforce_limits(self):
        pass


class MemoryLoadRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "memory.json")
        self.harness = MemoryHarness()
        self.harness.load_memory.__globals__["MEMORY_FILE"] = self.path
        self.harness.load_memory.__globals__["MAX_MEMORY_FILE_BYTES"] = 1024

    def tearDown(self):
        self.temp.cleanup()

    def write(self, raw):
        with open(self.path, "wb") as handle:
            handle.write(raw)

    def test_missing_file_starts_empty(self):
        self.assertEqual(self.harness.load_memory(), {"guilds": {}})

    def test_valid_current_format_loads(self):
        self.write(b'{"guilds":{"guild_1":{"messages":[]}}}')
        self.assertIn("guild_1", self.harness.load_memory()["guilds"])

    def test_valid_legacy_format_loads(self):
        self.write(b'{"guild_1":{"messages":[]}}')
        self.assertIn("guild_1", self.harness.load_memory()["guilds"])

    def test_malformed_json_fails_closed(self):
        self.write(b'{"guilds":')
        with self.assertRaises(RuntimeError):
            self.harness.load_memory()

    def test_wrong_guilds_type_fails_closed(self):
        self.write(b'{"guilds":[]}')
        with self.assertRaises(RuntimeError):
            self.harness.load_memory()

    def test_oversized_file_fails_closed(self):
        self.write(b" " * 1025)
        with self.assertRaises(RuntimeError):
            self.harness.load_memory()

    def test_non_object_root_fails_closed(self):
        self.write(b"[]")
        with self.assertRaises(RuntimeError):
            self.harness.load_memory()


class AttachmentReadRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_declared_size_rejected_before_read(self):
        class Attachment:
            size = 0
            called = False
            async def read(self):
                self.called = True
                return b"content"
        attachment = Attachment()
        with self.assertRaises(ValueError):
            await AttachmentHarness()._read_attachment_checked(attachment, ".txt")
        self.assertFalse(attachment.called)

    async def test_over_limit_declared_size_rejected_before_read(self):
        class Attachment:
            size = 3 * 1024 * 1024
            called = False
            async def read(self):
                self.called = True
                return b"content"
        attachment = Attachment()
        with self.assertRaises(ValueError):
            await AttachmentHarness()._read_attachment_checked(attachment, ".txt")
        self.assertFalse(attachment.called)

    async def test_valid_small_attachment_is_read_and_signature_checked(self):
        class Attachment:
            size = 7
            async def read(self):
                return b"content"
        self.assertEqual(await AttachmentHarness()._read_attachment_checked(Attachment(), ".txt"), b"content")


class LogRedactionRegressionTests(unittest.TestCase):
    def test_redaction_failure_does_not_emit_original_message_or_traceback(self):
        method_globals = redaction_filter_method.__globals__
        original_scrubber = method_globals["scrub_secrets"]
        try:
            method_globals["scrub_secrets"] = lambda value: (_ for _ in ()).throw(RuntimeError("simulated scrubber failure"))
            record = logging.LogRecord("test", logging.ERROR, __file__, 1, "secret=DO_NOT_LEAK", (), None)
            record.exc_info = (RuntimeError, RuntimeError("DO_NOT_LEAK_TRACE"), None)
            record.stack_info = "DO_NOT_LEAK_STACK"
            RedactionHarness().filter(record)
            self.assertEqual(record.getMessage(), "[LOG MESSAGE REDACTED: redaction failure]")
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.exc_text)
            self.assertIsNone(record.stack_info)
        finally:
            method_globals["scrub_secrets"] = original_scrubber


class ViewStateLoadRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "view_state.json")
        self.harness = ViewHarness(self.path)
        self.harness._load.__globals__["MAX_VIEW_STATE_FILE_BYTES"] = 1024

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_view_file_is_empty(self):
        self.harness._load()
        self.assertEqual(len(self.harness._entries), 0)

    def test_oversized_view_file_fails_closed_before_json_parse(self):
        with open(self.path, "wb") as handle:
            handle.write(b" " * 1025)
        with self.assertRaises(RuntimeError):
            self.harness._load()

    def test_malformed_view_json_fails_closed(self):
        with open(self.path, "wb") as handle:
            handle.write(b'{"entries":')
        with self.assertRaises(RuntimeError):
            self.harness._load()

    def test_invalid_view_root_fails_closed(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump([], handle)
        with self.assertRaises(RuntimeError):
            self.harness._load()


if __name__ == "__main__":
    unittest.main()
