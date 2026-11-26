import csv
import io
import json
import unittest
import zipfile

from clouzer_file_creation import create_document_file, detect_requested_file_format, validate_document_file, _xlsx_formula_specs


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


    def test_pdf_keeps_content_across_multiple_pages(self):
        import pymupdf
        content = "# Laporan PDF\n" + "\n".join(f"Baris konten {i}" for i in range(1, 90))
        data, _, _ = create_document_file("pdf", "Buat PDF", content)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            extracted = "\n".join(page.get_text() for page in doc)
            self.assertGreaterEqual(doc.page_count, 2)
            self.assertIn("Baris konten 89", extracted)

    def test_docx_does_not_duplicate_plain_first_line_as_title(self):
        from docx import Document
        data, _, _ = create_document_file("docx", "Buat DOCX", "Judul singkat\n\nIsi dokumen.")
        doc = Document(io.BytesIO(data))
        self.assertEqual(sum(p.text == "Judul singkat" for p in doc.paragraphs), 1)


    def test_xlsx_exact_clouzer_employee_acceptance_prompt(self):
        from openpyxl import load_workbook
        prompt = """Ulangi pembuatan file Excel. Buat file asli bernama `TES_AUDIT_CLOUZER.xlsx`.

Buat worksheet bernama `Data Pegawai`.

Gunakan data berikut. Baris pertama adalah header. Setiap baris setelahnya adalah satu baris data. Pisahkan setiap kolom dengan tanda `|`.

```text
No|Nama|Umur|Kota|Harga|Status
1|Andi|25|Jakarta|150000|Aktif
2|Budi|30|Bandung|200000|Aktif
3|Citra|22|Surabaya|175000|Nonaktif
4|Dedi|28|Medan|125000|Aktif
5|Eka|26|Yogyakarta|300000|Aktif
6|Fajar|32|Semarang|225000|Nonaktif
7|Gina|24|Malang|180000|Aktif
8|Hadi|29|Bekasi|250000|Aktif
9|Intan|27|Depok|190000|Nonaktif
10|Joko|31|Tangerang|275000|Aktif
```

Wajib menghasilkan 1 header dan 10 baris data. Wajib menghasilkan tepat 6 kolom."""
        content = """No|Nama|Umur|Kota|Harga|Status
1|Andi|25|Jakarta|150000|Aktif
2|Budi|30|Bandung|200000|Aktif
3|Citra|22|Surabaya|175000|Nonaktif
4|Dedi|28|Medan|125000|Aktif
5|Eka|26|Yogyakarta|300000|Aktif
6|Fajar|32|Semarang|225000|Nonaktif
7|Gina|24|Malang|180000|Aktif
8|Hadi|29|Bekasi|250000|Aktif
9|Intan|27|Depok|190000|Nonaktif
10|Joko|31|Tangerang|275000|Aktif"""
        data, filename, _ = create_document_file("xlsx", prompt, content)
        self.assertEqual(filename, "TES_AUDIT_CLOUZER.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"]), (10, 6))
        self.assertEqual(summary["sheet_name"], "Data Pegawai")
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            self.assertEqual(wb.sheetnames, ["Data Pegawai"])
            ws = wb["Data Pegawai"]
            self.assertEqual([ws.cell(1, col).value for col in range(1, 7)],
                             ["No", "Nama", "Umur", "Kota", "Harga", "Status"])
            for row in range(2, 12):
                for col in (1, 3, 5):
                    self.assertIsInstance(ws.cell(row, col).value, (int, float))
                    self.assertNotIsInstance(ws.cell(row, col).value, bool)
            self.assertEqual(ws.max_row, 11)
            self.assertEqual(ws.max_column, 6)
        finally:
            wb.close()

    def test_xlsx_falls_back_to_explicit_prompt_table_when_model_refuses(self):
        from openpyxl import load_workbook
        prompt = """Buat file asli bernama `TES_AUDIT_CLOUZER_ULANG.xlsx`.
Buat worksheet bernama `Data Pegawai`.
Gunakan tabel lengkap berikut:
No|Nama|Umur|Kota|Harga|Status
1|Andi|25|Jakarta|150000|Aktif
2|Budi|30|Bandung|200000|Aktif
3|Citra|22|Surabaya|175000|Nonaktif
4|Dedi|28|Medan|125000|Aktif
5|Eka|26|Yogyakarta|300000|Aktif
6|Fajar|32|Semarang|225000|Nonaktif
7|Gina|24|Malang|180000|Aktif
8|Hadi|29|Bekasi|250000|Aktif
9|Intan|27|Depok|190000|Nonaktif
10|Joko|31|Tangerang|275000|Aktif
Wajib tepat 10 baris data dan 6 kolom."""
        refusal = "Maaf, saya tidak dapat membuat file XLSX pada sesi ini."
        data, filename, _ = create_document_file("xlsx", prompt, refusal)
        self.assertEqual(filename, "TES_AUDIT_CLOUZER_ULANG.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"]), (10, 6))
        self.assertEqual(summary["sheet_name"], "Data Pegawai")
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb["Data Pegawai"]
            self.assertEqual(ws.max_row, 11)
            self.assertEqual(ws.max_column, 6)
            for row in range(2, 12):
                for col in (1, 3, 5):
                    self.assertIsInstance(ws.cell(row, col).value, (int, float))
                    self.assertNotIsInstance(ws.cell(row, col).value, bool)
        finally:
            wb.close()

        with self.assertRaisesRegex(ValueError, "Model mengembalikan pesan kegagalan"):
            create_document_file("xlsx", "Buat Excel tanpa tabel sumber", refusal)

    def test_xlsx_rejects_non_numeric_age_and_changed_source_data(self):
        prompt = """Buat Excel worksheet bernama Data Pegawai dengan tepat 2 baris data dan 2 kolom.

A|Umur
1|25
2|30"""
        with self.assertRaisesRegex(ValueError, "harus berupa angka"):
            create_document_file("xlsx", prompt, """A|Umur
1|dua puluh lima
2|30""")
        with self.assertRaisesRegex(ValueError, "tidak sama persis"):
            create_document_file("xlsx", prompt, """A|Umur
1|25
2|31""")

    def test_xlsx_recovers_from_unstructured_partial_model_response(self):
        from openpyxl import load_workbook
        prompt = """Buat file asli bernama `TES_AUDIT_CLOUZER_ULANG.xlsx`.
Buat worksheet bernama `Data Pegawai`.
Gunakan tabel sumber lengkap berikut:
No|Nama|Umur|Kota|Harga|Status
1|Andi|25|Jakarta|150000|Aktif
2|Budi|30|Bandung|200000|Aktif
3|Citra|22|Surabaya|175000|Nonaktif
4|Dedi|28|Medan|125000|Aktif
5|Eka|26|Yogyakarta|300000|Aktif
6|Fajar|32|Semarang|225000|Nonaktif
7|Gina|24|Malang|180000|Aktif
8|Hadi|29|Bekasi|250000|Aktif
9|Intan|27|Depok|190000|Nonaktif
10|Joko|31|Tangerang|275000|Aktif
Wajib tepat 10 baris data dan 6 kolom."""
        # Simulate the observed model behavior: ordinary prose, not a table.
        partial_response = """Saya akan menyiapkan file Excel.
Berikut data pegawai yang diminta.
Data sudah disusun.
Sebagian baris berhasil diproses.
Silakan periksa hasilnya.
Terima kasih."""
        data, filename, _ = create_document_file("xlsx", prompt, partial_response)
        self.assertEqual(filename, "TES_AUDIT_CLOUZER_ULANG.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"]), (10,  6))
        self.assertEqual(summary["sheet_name"], "Data Pegawai")
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb["Data Pegawai"]
            self.assertEqual(ws.max_row, 11)
            self.assertEqual(ws.max_column, 6)
            self.assertEqual(ws["B11"].value, "Joko")
            self.assertEqual(ws["E11"].value, 275000)
        finally:
            wb.close()

    def test_xlsx_recovers_from_exact_truncated_prefix_of_prompt_table(self):
        prompt = """Buat Excel dengan tepat 10 baris data dan 6 kolom.
No|Nama|Umur|Kota|Harga|Status
1|Andi|25|Jakarta|150000|Aktif
2|Budi|30|Bandung|200000|Aktif
3|Citra|22|Surabaya|175000|Nonaktif
4|Dedi|28|Medan|125000|Aktif
5|Eka|26|Yogyakarta|300000|Aktif
6|Fajar|32|Semarang|225000|Nonaktif
7|Gina|24|Malang|180000|Aktif
8|Hadi|29|Bekasi|250000|Aktif
9|Intan|27|Depok|190000|Nonaktif
10|Joko|31|Tangerang|275000|Aktif"""
        partial_table = """No\tNama\tUmur\tKota\tHarga\tStatus
1\tAndi\t25\tJakarta\t150000\tAktif
2\tBudi\t30\tBandung\t200000\tAktif
3\tCitra\t22\tSurabaya\t175000\tNonaktif
4\tDedi\t28\tMedan\t125000\tAktif
5\tEka\t26\tYogyakarta\t300000\tAktif"""
        data, _, _ = create_document_file("xlsx", prompt, partial_table)
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"]), (10, 6))


    def test_xlsx_inventory_formula_dates_filter_and_exact_source(self):
        from datetime import date, datetime
        from openpyxl import load_workbook

        prompt = """Buat file Excel (.xlsx) bernama **TES_INVENTARIS_GUDANG_02.xlsx**.
Buat worksheet bernama **Inventaris** dengan data berikut:
| Kode | Nama Barang | Kategori | Stok Awal | Barang Masuk | Barang Keluar | Stok Akhir | Harga Satuan | Tanggal Update | Status |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| INV001 | Laptop Lenovo | Elektronik | 25 | 10 | 8 | 27 | 8500000 | 2026-11-20 | Tersedia |
| INV002 | Mouse Wireless | Aksesoris | 100 | 50 | 35 | 115 | 125000 | 2026-11-21 | Tersedia |
| INV003 | Keyboard Mechanical | Aksesoris | 40 | 0 | 12 | 28 | 450000 | 2026-11-21 | Tersedia |
| INV004 | Monitor 24 Inch | Elektronik | 15 | 5 | 18 | 2 | 2100000 | 2026-11-22 | Menipis |
| INV005 | Flashdisk 64GB | Penyimpanan | 80 | 20 | 90 | 10 | 95000 | 2026-11-22 | Menipis |
| INV006 | SSD 1TB | Penyimpanan | 30 | 15 | 10 | 35 | 1250000 | 2026-11-23 | Tersedia |
| INV007 | Printer Laser | Elektronik | 12 | 3 | 15 | 0 | 3200000 | 2026-11-24 | Habis |
| INV008 | Webcam HD | Aksesoris | 45 | 10 | 20 | 35 | 375000 | 2026-11-24 | Tersedia |
Stok Akhir harus menggunakan rumus Stok Awal + Barang Masuk - Barang Keluar, bukan angka ketik.
Kolom Tanggal Update harus berupa tanggal Excel, bukan teks.
Kolom Stok Awal, Barang Masuk, Barang Keluar, Stok Akhir, dan Harga Satuan harus berupa angka.
Format header tebal dan berikan filter tabel.
Harus tepat 8 baris data dan 10 kolom."""
        content = """| Kode | Nama Barang | Kategori | Stok Awal | Barang Masuk | Barang Keluar | Stok Akhir | Harga Satuan | Tanggal Update | Status |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| INV001 | Laptop Lenovo | Elektronik | 25 | 10 | 8 | 27 | 8500000 | 2026-11-20 | Tersedia |
| INV002 | Mouse Wireless | Aksesoris | 100 | 50 | 35 | 115 | 125000 | 2026-11-21 | Tersedia |
| INV003 | Keyboard Mechanical | Aksesoris | 40 | 0 | 12 | 28 | 450000 | 2026-11-21 | Tersedia |
| INV004 | Monitor 24 Inch | Elektronik | 15 | 5 | 18 | 2 | 2100000 | 2026-11-22 | Menipis |
| INV005 | Flashdisk 64GB | Penyimpanan | 80 | 20 | 90 | 10 | 95000 | 2026-11-22 | Menipis |
| INV006 | SSD 1TB | Penyimpanan | 30 | 15 | 10 | 35 | 1250000 | 2026-11-23 | Tersedia |
| INV007 | Printer Laser | Elektronik | 12 | 3 | 15 | 0 | 3200000 | 2026-11-24 | Habis |
| INV008 | Webcam HD | Aksesoris | 45 | 10 | 20 | 35 | 375000 | 2026-11-24 | Tersedia |"""
        data, filename, _ = create_document_file("xlsx", prompt, content)
        self.assertEqual(filename, "TES_INVENTARIS_GUDANG_02.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (8, 10, "Inventaris"))

        wb = load_workbook(io.BytesIO(data), data_only=False)
        try:
            ws = wb["Inventaris"]
            self.assertEqual(ws.max_row, 9)
            self.assertEqual(ws.max_column, 10)
            self.assertEqual(ws.auto_filter.ref, "A1:J9")
            self.assertTrue(ws["A1"].font.bold)
            for row in range(2, 10):
                self.assertEqual(ws.cell(row, 7).value, f"=D{row}+E{row}-F{row}")
                for col in (4, 5, 6, 8):
                    self.assertIsInstance(ws.cell(row, col).value, (int, float))
                    self.assertNotIsInstance(ws.cell(row, col).value, bool)
                self.assertIsInstance(ws.cell(row, 9).value, (date, datetime))
        finally:
            wb.close()


    def test_xlsx_parses_common_localized_date_formats_as_excel_dates(self):
        from datetime import date
        from openpyxl import load_workbook

        prompt = "Buat XLSX dengan kolom Tanggal Update berupa tanggal Excel."
        content = (
            "Kode|Tanggal Update\n"
            "A|20/11/2026\n"
            "B|21-11-2026\n"
            "C|2026-11-22T00:00:00"
        )
        data, _, _ = create_document_file("xlsx", prompt, content)
        result = validate_document_file("xlsx", prompt, data)
        self.assertEqual(result["rows"], 3)
        wb = load_workbook(io.BytesIO(data), data_only=False)
        try:
            ws = wb.active
            self.assertEqual(ws["B2"].value, date(2026, 11, 20))
            self.assertEqual(ws["B3"].value, date(2026, 11, 21))
            self.assertEqual(ws["B4"].value, date(2026, 11, 22))
        finally:
            wb.close()


    def test_xlsx_formula_specs_accepts_indonesian_rumus_and_formula_wording(self):
        headers = ["Stok Awal", "Barang Masuk", "Barang Keluar", "Stok Akhir", "Harga Satuan", "Total Nilai Stok"]
        expected = {
            3: ("stock_balance", (0, 1, 2)),
            5: ("stock_value", (3, 4)),
        }
        self.assertEqual(_xlsx_formula_specs(headers, "Gunakan rumus Excel asli"), expected)
        self.assertEqual(_xlsx_formula_specs(headers, "Gunakan formula Excel asli"), expected)

    def test_xlsx_stock_audit_two_formulas_total_and_header_freeze(self):
        from datetime import date
        from openpyxl import load_workbook
        prompt = """Buat XLSX bernama TES_PASS_RENDER_XLSX_03.xlsx, worksheet 'Audit Stok'.
Gunakan formula Excel asli untuk Stok Akhir dan Total Nilai Stok. Wajib tepat 10 baris data dan 10 kolom.
Bekukan baris pertama. Tambahkan baris TOTAL dengan rumus SUM.
No|Kode|Nama|Stok Awal|Barang Masuk|Barang Keluar|Stok Akhir|Harga Satuan|Total Nilai Stok|Tanggal Update
1|BRG001|Laptop|10|5|3|12|7500000|90000000|2026-11-20
2|BRG002|Mouse|50|20|15|55|150000|8250000|2026-11-21
3|BRG003|Keyboard|30|10|5|35|250000|8750000|2026-11-22
4|BRG004|Monitor|15|5|8|12|1800000|21600000|2026-11-23
5|BRG005|Printer|8|4|2|10|2200000|22000000|2026-11-24
6|BRG006|Flashdisk|0|25|10|15|75000|1125000|2026-11-25
7|BRG007|SSD|12|0|4|8|950000|7600000|2026-11-26
8|BRG008|RAM|20|10|0|30|600000|18000000|2026-11-26
9|BRG009|Webcam|5|5|10|0|400000|0|2026-11-26
10|BRG010|Headset|25|15|5|35|350000|12250000|2026-11-26
TOTAL||||||212||189575000|"""
        content = prompt.split("No|Kode|Nama|", 1)[1]
        content = "No|Kode|Nama|" + content
        data, filename, _ = create_document_file("xlsx", prompt, content)
        self.assertEqual(filename, "TES_PASS_RENDER_XLSX_03.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (10, 10, "Audit Stok"))
        wb = load_workbook(io.BytesIO(data), data_only=False)
        try:
            ws = wb["Audit Stok"]
            self.assertEqual(ws.max_row, 12)
            self.assertEqual(ws.max_column, 10)
            self.assertEqual(ws.freeze_panes, "A2")
            self.assertEqual(ws.auto_filter.ref, "A1:J12")
            for row in range(2, 12):
                self.assertEqual(ws.cell(row, 7).value, f"=D{row}+E{row}-F{row}")
                self.assertEqual(ws.cell(row, 9).value, f"=G{row}*H{row}")
            self.assertEqual(ws["G12"].value, "=SUM(G2:G11)")
            self.assertEqual(ws["I12"].value, "=SUM(I2:I11)")
            self.assertTrue(ws["A1"].font.bold)
        finally:
            wb.close()

    def test_xlsx_ignores_explanatory_prose_around_pipe_table(self):
        prompt = "Buat XLSX tepat 2 baris data dan 2 kolom.\nA|B\n1|x\n2|y"
        content = "Berikut tabel yang diminta:\nA|B\n1|x\n2|y\nSelesai dibuat."
        data, _, _ = create_document_file("xlsx", prompt, content)
        result = validate_document_file("xlsx", prompt, data)
        self.assertEqual((result["rows"], result["columns"]), (2, 2))


    def test_xlsx_selects_matching_table_block_instead_of_concatenating_tables(self):
        prompt = """Buat XLSX tepat 2 baris data dan 2 kolom.
Nama|Status
Andi|Aktif
Budi|Nonaktif"""
        content = """Ringkasan tambahan:
Topik\tJumlah
A\t1
B\t2
C\t3
D\t4
E\t5

Data yang diminta:
Nama|Status
Andi|Aktif
Budi|Nonaktif"""
        data, _, _ = create_document_file("xlsx", prompt, content)
        result = validate_document_file("xlsx", prompt, data)
        self.assertEqual((result["rows"], result["columns"]), (2, 2))

    def test_xlsx_tsv_chooses_block_matching_requested_dimensions(self):
        prompt = "Buat XLSX tepat 2 baris data dan 2 kolom."
        content = """Judul\tJumlah
Satu\t1
Dua\t2

Data Utama\tNilai
A\t10
B\t20"""
        data, _, _ = create_document_file("xlsx", prompt, content)
        result = validate_document_file("xlsx", prompt, data)
        self.assertEqual((result["rows"], result["columns"]), (2, 2))
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb.active
            self.assertEqual(ws["A1"].value, "Judul")
            self.assertEqual(ws["A3"].value, "Dua")
        finally:
            wb.close()

if __name__ == "__main__":
    unittest.main()
