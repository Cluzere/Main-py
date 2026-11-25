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
        with self.assertRaisesRegex(ValueError, "berisi 1 baris data; permintaan tepat 3"):
            create_document_file("xlsx", prompt, "ID\tNama\tHarga\n1\tA\t10")

    def test_pptx_five_slide_requirement_and_refusal(self):
        prompt = "Buat presentasi PowerPoint 5 slide tentang CLOUZER"
        sections = [("Pengenalan", "CLOUZER adalah bot Discord AI."), ("Tujuan", "Membantu pengguna menyelesaikan tugas."), ("Cara kerja", "Discord meneruskan pesan ke layanan AI."), ("Manfaat", "Membaca dokumen dan membuat file."), ("Kesimpulan", "CLOUZER menggabungkan percakapan dan otomasi.")]
        content = "\n--- SLIDE ---\n".join(title + "\n" + body for title, body in sections)
        data, _, _ = create_document_file("pptx", prompt, content)
        self.assertEqual(validate_document_file("pptx", prompt, data)["slides"], 5)
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

    def test_xlsx_exact_30_product_rows_and_six_columns(self):
        prompt = "Buat Excel berisi 30 produk dan 6 kolom"
        header = ["ID", "Nama", "Harga", "Kategori", "Stok", "Status"]
        rows = [header] + [[str(i), f"Produk {i}", str(i * 1000), "Umum", str(100-i), "Aktif"] for i in range(1, 31)]
        content = "\n".join("\t".join(row) for row in rows)
        data, _, _ = create_document_file("xlsx", prompt, content)
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"]), (30, 6))

    def test_docx_does_not_duplicate_existing_title(self):
        from docx import Document
        data, _, _ = create_document_file("docx", "Buat laporan CLOUZER", "# Laporan CLOUZER\n\nIsi laporan cukup panjang dan dapat dibaca dengan jelas.")
        doc = Document(io.BytesIO(data))
        headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Title") or p.style.name.startswith("Heading")]
        self.assertEqual(headings.count("Laporan CLOUZER"), 1)

    def test_all_remaining_formats_build(self):
        content = "# Dokumen CLOUZER\n\nIsi dokumen yang cukup panjang untuk validasi hasil pembuatan file."
        for fmt in ("txt", "pdf", "docx"):
            with self.subTest(fmt=fmt):
                data, name, mime = create_document_file(fmt, "Buat " + fmt, content)
                self.assertTrue(data and name.endswith("." + fmt) and mime)
                self.assertTrue(validate_document_file(fmt, "Buat " + fmt, data)["valid"])


    def test_xlsx_sheet_name_numeric_cells_and_leading_zero(self):
        from openpyxl import load_workbook
        prompt = "Buat Excel dengan nama sheet 'Data Produk' berisi tepat 2 produk dan 3 kolom"
        content = "No\tKode\tHarga\n1\t00123\t78000\n2\t00456\t19000"
        data, _, _ = create_document_file("xlsx", prompt, content)
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb.active
            self.assertEqual(ws.title, "Data Produk")
            self.assertIsInstance(ws["C2"].value, int)
            self.assertEqual(ws["C2"].value, 78000)
            self.assertEqual(ws["B2"].value, "00123")
        finally:
            wb.close()

    def test_csv_quoted_multiline_field_is_preserved(self):
        prompt = "Buat CSV dengan tepat 2 baris data dan 2 kolom"
        content = 'id,deskripsi\n1,"baris pertama\nbaris kedua"\n2,normal'
        data, _, _ = create_document_file("csv", prompt, content)
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows) - 1, 2)
        self.assertEqual(rows[1][1], "baris pertama\nbaris kedua")

    def test_csv_rejects_extra_rows_when_exact_count_requested(self):
        prompt = "Buat CSV dengan tepat 2 baris data dan 2 kolom"
        with self.assertRaisesRegex(ValueError, "tepat 2"):
            create_document_file("csv", prompt, "id,nama\n1,A\n2,B\n3,C")

    def test_pptx_preserves_lines_beyond_old_truncation_limit(self):
        from pptx import Presentation
        prompt = "Buat presentasi PowerPoint 2 slide"
        first = "Judul pertama\n" + "\n".join(f"Baris konten {i}" for i in range(1, 36))
        content = first + "\n--- SLIDE ---\nJudul kedua\nIsi slide kedua yang cukup panjang."
        data, _, _ = create_document_file("pptx", prompt, content)
        prs = Presentation(io.BytesIO(data))
        self.assertEqual(len(prs.slides), 2)
        all_text = "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if getattr(shape, "has_text_frame", False))
        self.assertIn("Baris konten 35", all_text)

    def test_short_txt_is_valid(self):
        data, _, _ = create_document_file("txt", "Buat TXT", "OK")
        self.assertEqual(data, b"OK")

    def test_zip_rejects_path_traversal(self):
        prompt = "Buat ZIP"
        content = "=== FILE: ../outside.txt ===\nkonten tidak aman"
        with self.assertRaisesRegex(ValueError, "tidak aman"):
            create_document_file("zip", prompt, content)


if __name__ == "__main__":
    unittest.main()
