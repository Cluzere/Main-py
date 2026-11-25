import csv
import io
import json
import unittest
import zipfile

from clouzer_file_creation import create_document_file, detect_requested_file_format, validate_document_file


class FileCreationTests(unittest.TestCase):
    def test_detect_all_eight_formats(self):
        prompts = {
            "Buat catatan TXT": "txt", "Buat laporan PDF": "pdf",
            "Buat dokumen Word DOCX": "docx", "Buat tabel Excel": "xlsx",
            "Buat presentasi PowerPoint": "pptx", "Buat arsip ZIP": "zip",
            "Buat file CSV": "csv", "Buat file JSON": "json",
        }
        for prompt, expected in prompts.items():
            self.assertEqual(detect_requested_file_format(prompt), expected)

    def test_csv_creation_and_dimensions(self):
        prompt = "Buat CSV dengan 3 baris data dan 3 kolom"
        data, filename, mime = create_document_file("csv", prompt, "id,nama,status\n1,A,OK\n2,B,OK\n3,C,OK")
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertTrue(filename.endswith(".csv"))
        self.assertIn("text/csv", mime)
        self.assertEqual((len(rows)-1, len(rows[0])), (3, 3))

    def test_json_is_valid_and_invalid_rejected(self):
        tiny_data, _, _ = create_document_file("json", "Buat JSON", "{}")
        self.assertEqual(json.loads(tiny_data), {})
        data, filename, mime = create_document_file("json", "Buat file JSON", '{"project":"CLOUZER"}')
        self.assertTrue(filename.endswith(".json"))
        self.assertIn("application/json", mime)
        self.assertEqual(json.loads(data)["project"], "CLOUZER")
        with self.assertRaises(ValueError):
            create_document_file("json", "Buat JSON", "bukan json valid")

    def test_xlsx_enforces_requested_rows_and_columns(self):
        prompt = "Buat Excel 3 produk dan 3 kolom"
        data, _, _ = create_document_file("xlsx", prompt, "ID\tNama\tHarga\n1\tA\t10\n2\tB\t20\n3\tC\t30")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"]), (3, 3))
        with self.assertRaisesRegex(ValueError, "hanya berisi 1 baris data"):
            create_document_file("xlsx", prompt, "ID\tNama\tHarga\n1\tA\t10")

    def test_pptx_slide_count_and_refusal(self):
        prompt = "Buat PowerPoint 2 slide"
        content = "Judul\nMateri pengenalan\n--- SLIDE ---\nKesimpulan\nMateri akhir"
        data, _, _ = create_document_file("pptx", prompt, content)
        self.assertEqual(validate_document_file("pptx", prompt, data)["slides"], 2)
        with self.assertRaises(ValueError):
            create_document_file("pptx", prompt, "Gagal: file PPTX tidak dapat dibuat atau dikirim sebagai attachment.")

    def test_zip_dynamic_required_members(self):
        prompt = "Buat ZIP berisi README.txt, data.json, dan data.csv"
        content = ("=== FILE: README.txt ===\nPenjelasan CLOUZER.\n"
                   "=== FILE: data.json ===\n{\"project\":\"CLOUZER\"}\n"
                   "=== FILE: data.csv ===\nid,nama,status\n1,A,OK\n2,B,OK\n3,C,OK\n")
        data, _, _ = create_document_file("zip", prompt, content)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {"README.txt", "data.json", "data.csv"})
        with self.assertRaises(ValueError):
            create_document_file("zip", prompt, "tidak ada marker anggota")

    def test_all_remaining_formats_build(self):
        content = "# Dokumen CLOUZER\n\nIsi dokumen yang cukup panjang untuk validasi hasil pembuatan file."
        for fmt in ("txt", "pdf", "docx"):
            with self.subTest(fmt=fmt):
                data, name, mime = create_document_file(fmt, "Buat " + fmt, content)
                self.assertTrue(data and name.endswith("." + fmt) and mime)
                self.assertTrue(validate_document_file(fmt, "Buat " + fmt, data)["valid"])


if __name__ == "__main__":
    unittest.main()
