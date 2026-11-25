import ast
import csv
import io
import json
import os
import re
import unittest
from pathlib import Path

import fitz
import openpyxl
from docx import Document
from pptx import Presentation


CANDIDATE = Path("audit-recovery/review-round-2/revisi_audit_final_main.py")


def load_file_helpers():
    source = CANDIDATE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted_assignments = {
        "FILE_CREATE_INTENT_RE", "FILE_EXT_RE", "FILE_FORMAT_ALIASES",
        "MAX_GENERATED_FILE_BYTES",
    }
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & wanted_assignments:
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            "is_file_creation_request", "infer_generated_filename",
            "build_generated_file_bytes",
        }:
            nodes.append(node)
    namespace = {
        "os": os, "re": re, "json": json, "csv": csv, "io": io,
        "docx": __import__("docx"), "openpyxl": openpyxl,
        "Presentation": Presentation, "fitz": fitz,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(CANDIDATE), "exec"), namespace)
    return namespace


HELPERS = load_file_helpers()


class FileCreationHelpersTests(unittest.TestCase):
    def test_detects_explicit_file_creation_requests(self):
        detect = HELPERS["is_file_creation_request"]
        self.assertTrue(detect("Buatkan file laporan PDF tentang kebun kota"))
        self.assertTrue(detect("Tolong bikin dokumen proposal"))
        self.assertTrue(detect("Create file ringkasan.md"))
        self.assertFalse(detect("Jelaskan isi file laporan.pdf"))

    def test_filename_inference_and_path_sanitization(self):
        infer = HELPERS["infer_generated_filename"]
        self.assertEqual(infer("Buat file laporan.pdf"), "laporan.pdf")
        self.assertEqual(infer("Buatkan dokumen Word"), "hasil_clouzer.docx")
        self.assertEqual(infer("Buat file ../../rahasia.txt"), "rahasia.txt")

    def test_plain_text_markdown_python_and_csv_bytes(self):
        build = HELPERS["build_generated_file_bytes"]
        for filename in ("hasil.txt", "hasil.md", "script.py"):
            data, mime = build(filename, "Isi file\nBaris kedua")
            self.assertEqual(data.decode("utf-8"), "Isi file\nBaris kedua")
            self.assertTrue(mime.startswith("text/"))
        data, mime = build("data.csv", "Nama,Jumlah\nTomat,10")
        self.assertEqual(list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))[1], ["Tomat", "10"])
        self.assertEqual(mime, "text/csv")

    def test_json_is_validated_and_normalized(self):
        build = HELPERS["build_generated_file_bytes"]
        data, mime = build("data.json", '{"nama":"Kebun","jumlah":42}')
        self.assertEqual(json.loads(data), {"nama": "Kebun", "jumlah": 42})
        self.assertEqual(mime, "application/json")
        with self.assertRaises(json.JSONDecodeError):
            build("bad.json", "{invalid}")

    def test_docx_xlsx_pptx_and_pdf_are_real_files(self):
        build = HELPERS["build_generated_file_bytes"]

        docx_data, _ = build("laporan.docx", "Judul Laporan\nIsi laporan")
        self.assertIn("Isi laporan", "\n".join(p.text for p in Document(io.BytesIO(docx_data)).paragraphs))

        xlsx_data, _ = build("data.xlsx", "Nama,Jumlah\nTomat,10\nCabai,14")
        sheet = openpyxl.load_workbook(io.BytesIO(xlsx_data), data_only=True).active
        self.assertEqual(sheet["A2"].value, "Tomat")
        self.assertEqual(sheet["B3"].value, "14")

        pptx_data, _ = build("presentasi.pptx", "# Pembuka\nTujuan\n# Data\n42 tanaman")
        presentation = Presentation(io.BytesIO(pptx_data))
        self.assertEqual(len(presentation.slides), 2)
        self.assertIn("Pembuka", presentation.slides[0].shapes.title.text)

        pdf_data, _ = build("laporan.pdf", "Laporan CLOUZER\nTotal: 42")
        pdf = fitz.open(stream=pdf_data, filetype="pdf")
        self.assertGreaterEqual(len(pdf), 1)
        self.assertIn("Laporan CLOUZER", pdf[0].get_text())
        pdf.close()

    def test_empty_content_and_oversized_output_are_rejected(self):
        build = HELPERS["build_generated_file_bytes"]
        with self.assertRaises(ValueError):
            build("kosong.txt", "  ")
        namespace = HELPERS
        namespace["MAX_GENERATED_FILE_BYTES"] = 4
        try:
            with self.assertRaises(ValueError):
                build("besar.txt", "konten lebih besar")
        finally:
            namespace["MAX_GENERATED_FILE_BYTES"] = 8 * 1024 * 1024


if __name__ == "__main__":
    unittest.main()
