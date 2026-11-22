import ast
import json
import logging
import os
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
                    }
                    exec(compile(module, str(SOURCE), "exec"), namespace)
                    return namespace[method_name]
    raise AssertionError(f"method not found: {class_name}.{method_name}")


load_memory_method = extract_method("ClouzerBot", "load_memory")
view_load_method = extract_method("ViewStateStore", "_load")


class MemoryHarness:
    load_memory = load_memory_method

    def _normalize_scope_state(self, value):
        return value if isinstance(value, dict) else {}

    def _scrub_loaded_scope(self, value):
        return value


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
