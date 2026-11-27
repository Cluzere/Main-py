import csv
import io
import json
import re
import unittest
import zipfile

from clouzer_file_creation import create_document_file, detect_requested_file_format, validate_document_file, resolve_document_source, build_file_generation_system_instruction, _xlsx_formula_specs, _requested_sheet_title


class FileCreationTests(unittest.TestCase):
    def test_file_generation_system_instruction_uses_local_backend_for_all_formats(self):
        formats = ("txt", "pdf", "docx", "xlsx", "pptx", "zip", "csv", "json", "md", "html", "xml", "yaml", "rtf", "odt")
        for fmt in formats:
            with self.subTest(fmt=fmt):
                instruction = build_file_generation_system_instruction(fmt)
                self.assertIn(fmt.upper(), instruction)
                self.assertIn("generator dokumen lokal", instruction)
                self.assertIn("TIDAK perlu", instruction)
                self.assertIn("jangan pernah menjawab", instruction.lower())
        pdf_instruction = build_file_generation_system_instruction("pdf")
        self.assertIn("backend memakai template deterministik", pdf_instruction)
        self.assertIn("tepat dua paragraf naratif", pdf_instruction)
        self.assertIn("45-60 kata per paragraf", pdf_instruction)
        self.assertIn("jangan menyatakan hasil lulus/gagal", pdf_instruction)
        self.assertIn("jangan mengklaim pemeriksaan visual manual", pdf_instruction.lower())
        with self.assertRaises(ValueError):
            build_file_generation_system_instruction("exe")

    def test_detect_pdf_request_even_when_spec_mentions_selectable_text(self):
        prompt = """Buat PDF sungguhan bernama TES_PDF_MASTER_FINAL.pdf dan kirim sebagai attachment Discord.

Buat laporan berjudul LAPORAN PENGUJIAN GENERATOR PDF CLOUZER.
Validasi teks: PDF harus bisa dibuka, teks dapat diseleksi, disalin, dan dicari.
Jangan mengklaim berhasil jika file belum benar-benar dibuat dan dikirim."""
        self.assertEqual(detect_requested_file_format(prompt), "pdf")

    def test_detect_capability_question_as_non_file_request(self):
        prompts = (
            "Apakah CLOUZER bisa membuat PDF?",
            "Bisakah kamu buat PDF?",
            "CLOUZER dapat membuat PDF?",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertIsNone(detect_requested_file_format(prompt))

    def test_detect_all_eight_formats(self):
        prompts = {
            "Buat catatan TXT": "txt", "Buat laporan PDF": "pdf",
            "Buat dokumen Word DOCX": "docx", "Buat tabel Excel": "xlsx",
            "Buat presentasi PowerPoint": "pptx", "Buat arsip ZIP": "zip",
            "Buat file CSV": "csv", "Buat file JSON": "json",
        }
        for prompt, expected in prompts.items():
            self.assertEqual(detect_requested_file_format(prompt), expected)

    def test_pdf_renders_real_styled_table_and_selectable_text(self):
        prompt = "Buat laporan PDF dengan tabel status pengujian"
        content = (
            "# Laporan Tes PDF CLOUZER\n\n"
            "## Ringkasan\n\n"
            "Dokumen ini merangkum hasil pengujian pembuatan file.\n\n"
            "No. | Komponen | Status\n"
            "--- | --- | ---\n"
            "1 | Penerimaan perintah | LULUS\n"
            "2 | Pembuatan isi dokumen | LULUS\n"
            "3 | Pembuatan file PDF | WAJIB DIUJI\n"
            "4 | Validasi file PDF | WAJIB DIUJI\n"
            "| 5 | Pengiriman lampiran Discord | WAJIB DIUJI |\n\n"
            "## Kesimpulan\n\n"
            "Validasi lanjutan masih diperlukan."
        )
        data, filename, mime = create_document_file("pdf", prompt, content)
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            extracted = "\n".join(page.get_text() for page in doc)
            drawings = sum(len(page.get_drawings()) for page in doc)
            self.assertGreaterEqual(doc.page_count, 1)
            self.assertIn("Penerimaan perintah", extracted)
            self.assertIn("WAJIB DIUJI", extracted)
            self.assertIn("Kesimpulan", extracted)
            self.assertGreaterEqual(drawings, 6)
        summary = validate_document_file("pdf", prompt, data)
        self.assertTrue(filename.endswith(".pdf"))
        self.assertIn("application/pdf", mime)
        self.assertGreater(summary["text_chars"], 80)

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

    def test_docx_converts_markdown_table_and_numbered_list_to_native_word_structure(self):
        from docx import Document
        prompt = (
            "Buat DOCX dengan heading Word, tabel Word asli, dan daftar bernomor. "
            "Judul hanya satu kali."
        )
        content = """# LAPORAN PENGUJIAN CLOUZER

## 1. Identitas
Nama proyek: CLOUZER.

## 2. Data Pengujian
Data berikut harus menjadi tabel Word asli.

| No | Nama | Umur | Jabatan | Catatan |
|---|---|---:|---|---|
| 1 | Andi | 25 | Admin | Normal |
| 2 | Budi | 30 | Manager | =1+1 |
| 3 | Citra | 27 | Staff | +SUM(1,1) |

1. Periksa judul.
2. Periksa tabel.
3. Periksa struktur Word.

## 3. Kesimpulan
Dokumen pendek untuk tes konversi Markdown."""
        data, filename, mime = create_document_file("docx", prompt, content)
        doc = Document(io.BytesIO(data))
        paragraphs = doc.paragraphs
        all_text = "\n".join(p.text for p in paragraphs)
        self.assertTrue(filename.endswith(".docx"))
        self.assertIn("application/vnd.openxmlformats-officedocument.wordprocessingml.document", mime)
        self.assertEqual(all_text.count("LAPORAN PENGUJIAN CLOUZER"), 1)
        self.assertEqual(sum(p.style.name.startswith("Heading") for p in paragraphs), 4)
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual((len(doc.tables[0].rows), len(doc.tables[0].columns)), (4, 5))
        self.assertEqual(doc.tables[0].cell(2, 4).text, "=1+1")
        self.assertEqual(doc.tables[0].cell(3, 4).text, "+SUM(1,1)")
        self.assertEqual(sum(p.style.name.casefold().startswith("list number") for p in paragraphs), 3)
        summary = validate_document_file("docx", prompt, data)
        self.assertEqual(summary["headings"], 4)
        self.assertEqual(summary["tables"], 1)
        self.assertEqual(summary["numbered_lists"], 3)

    def test_docx_converts_html_into_native_word_structure(self):
        from docx import Document
        prompt = (
            "Buat DOCX dengan heading Word, tabel Word asli, dan daftar bernomor. "
            "Judul hanya satu kali."
        )
        content = """<h1>LAPORAN PENGUJIAN CLOUZER</h1>
<h2>1. Identitas</h2>
<p>Nama proyek: CLOUZER.</p>
<h2>2. Data Pengujian</h2>
<p>Data contoh untuk pemeriksaan tabel.</p>
<table>
<thead><tr><th>No</th><th>Nama</th><th>Umur</th><th>Jabatan</th><th>Catatan</th></tr></thead>
<tbody>
<tr><td>1</td><td>Andi</td><td>25</td><td>Admin</td><td>Normal</td></tr>
<tr><td>2</td><td>Budi</td><td>30</td><td>Manager</td><td>=1+1</td></tr>
<tr><td>3</td><td>Citra</td><td>27</td><td>Staff</td><td>+SUM(1,1)</td></tr>
</tbody></table>
<ol><li>Periksa judul.</li><li>Periksa tabel.</li><li>Periksa struktur Word.</li></ol>
<h2>3. Kesimpulan</h2>
<p>Dokumen pendek untuk tes konversi HTML.</p>"""
        data, filename, mime = create_document_file("docx", prompt, content)
        doc = Document(io.BytesIO(data))
        paragraphs = doc.paragraphs
        all_text = "\n".join(p.text for p in paragraphs)
        self.assertTrue(filename.endswith(".docx"))
        self.assertIn("application/vnd.openxmlformats-officedocument.wordprocessingml.document", mime)
        self.assertEqual(all_text.count("LAPORAN PENGUJIAN CLOUZER"), 1)
        self.assertFalse(any("<h1>" in p.text or "<table>" in p.text or "<li>" in p.text for p in paragraphs))
        self.assertEqual(sum(p.style.name.startswith("Heading") for p in paragraphs), 4)
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual((len(doc.tables[0].rows), len(doc.tables[0].columns)), (4, 5))
        self.assertEqual(doc.tables[0].cell(2, 4).text, "=1+1")
        self.assertEqual(sum(p.style.name.casefold().startswith("list number") for p in paragraphs), 3)
        summary = validate_document_file("docx", prompt, data)
        self.assertEqual(summary["headings"], 4)
        self.assertEqual(summary["tables"], 1)
        self.assertEqual(summary["numbered_lists"], 3)

    def test_docx_converts_mixed_html_and_markdown_to_native_word_structure(self):
        from docx import Document
        prompt = (
            "Buat DOCX dengan heading Word, tabel Word asli, dan daftar bernomor. "
            "Judul hanya satu kali."
        )
        content = """<p>Ringkasan dokumen.</p>
## Identitas
Nama proyek CLOUZER.

## Data Pengujian
| No | Nama | Umur | Jabatan | Catatan |
|---|---|---:|---|---|
| 1 | Andi | 25 | Admin | Normal |
| 2 | Budi | 30 | Manager | =1+1 |
| 3 | Citra | 27 | Staff | +SUM(1,1) |

1. Periksa judul.
2. Periksa tabel.
3. Periksa struktur Word.

## Kesimpulan
Dokumen selesai diuji."""
        data, filename, mime = create_document_file("docx", prompt, content)
        doc = Document(io.BytesIO(data))
        paragraphs = doc.paragraphs
        self.assertTrue(filename.endswith(".docx"))
        self.assertIn("application/vnd.openxmlformats-officedocument.wordprocessingml.document", mime)
        self.assertEqual(sum(p.style.name.startswith("Heading") for p in paragraphs), 3)
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual((len(doc.tables[0].rows), len(doc.tables[0].columns)), (4, 5))
        self.assertEqual(doc.tables[0].cell(2, 4).text, "=1+1")
        self.assertEqual(doc.tables[0].cell(3, 4).text, "+SUM(1,1)")
        self.assertEqual(sum(p.style.name.casefold().startswith("list number") for p in paragraphs), 3)
        summary = validate_document_file("docx", prompt, data)
        self.assertEqual(summary["headings"], 3)
        self.assertEqual(summary["tables"], 1)
        self.assertEqual(summary["numbered_lists"], 3)

    def test_docx_validator_rejects_raw_html_and_missing_native_table(self):
        from docx import Document
        doc = Document()
        doc.add_paragraph("<h1>Judul</h1>")
        output = io.BytesIO()
        doc.save(output)
        prompt = "Buat DOCX dengan heading Word dan tabel Word asli."
        with self.assertRaisesRegex(ValueError, "tag HTML mentah"):
            validate_document_file("docx", prompt, output.getvalue())

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

    def test_pptx_uses_each_slide_chunk_title_instead_of_repeating_deck_title(self):
        from pptx import Presentation
        from pptx.util import Inches

        prompt = "Buat presentasi PowerPoint 5 slide tentang CLOUZER"
        slide_titles = [
            "Slide 1 — Pengenalan CLOUZER AI",
            "Slide 2 — Tujuan dan Fungsi Utama",
            "Slide 3 — Cara Kerja Sistem",
            "Slide 4 — Manfaat dan Kemampuan",
            "Slide 5 — Kesimpulan",
        ]
        sections = [
            title + "\nIsi khusus untuk " + title + "."
            for title in slide_titles
        ]
        content = "\n--- SLIDE ---\n".join(sections)

        data, _, _ = create_document_file("pptx", prompt, content)
        prs = Presentation(io.BytesIO(data))

        self.assertEqual(len(prs.slides), 5)
        actual_titles = []
        title_shapes = []
        for slide in prs.slides:
            text_shapes = [
                shape for shape in slide.shapes
                if getattr(shape, "has_text_frame", False) and shape.text.strip()
            ]
            # The redesigned deck uses a blank layout and editable text boxes,
            # so Presentation slide.shapes.title is intentionally None.
            title_shape = next(
                (shape for shape in text_shapes if shape.top < Inches(2.2)
                 and shape.left < Inches(8.0)),
                None,
            )
            self.assertIsNotNone(title_shape, "Slide title textbox must exist")
            title_shapes.append(title_shape)
            actual_titles.append(title_shape.text.strip())
        self.assertEqual(actual_titles, slide_titles)
        for slide, expected_title, title_shape in zip(prs.slides, slide_titles, title_shapes):
            body_text = " ".join(
                shape.text for shape in slide.shapes
                if getattr(shape, "has_text_frame", False) and shape.shape_id != title_shape.shape_id
            )
            body_lines = [line.strip() for line in body_text.splitlines() if line.strip()]
            # The title may legitimately be mentioned inside body prose; only
            # reject a standalone duplicate title line in the body placeholder.
            self.assertNotIn(expected_title, body_lines)
            self.assertIn("Isi khusus untuk " + expected_title, body_text)

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


    def test_pdf_keeps_heading_with_following_paragraph(self):
        import pymupdf
        preceding = [f"Baris pengantar nomor {i}." for i in range(1, 31)]
        conclusion = (
            "Isi kesimpulan ini sengaja dibuat cukup panjang agar membungkus menjadi "
            "lebih dari satu baris pada lebar halaman PDF dan menguji bahwa judulnya "
            "tidak tertinggal sendirian di bagian bawah halaman."
        )
        content = "\\n\\n".join(["# Laporan PDF"] + preceding + ["## Kesimpulan", conclusion])
        data, _, _ = create_document_file("pdf", "Buat PDF", content)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            page_texts = [page.get_text() for page in doc]
            self.assertTrue(any(
                "Kesimpulan" in page_text and "Isi kesimpulan ini" in page_text
                for page_text in page_texts
            ))

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

    def test_xlsx_explicit_source_table_wins_over_invalid_model_values(self):
        from openpyxl import load_workbook
        prompt = """Buat Excel worksheet bernama Data Pegawai dengan tepat 2 baris data dan 2 kolom.

A|Umur
1|25
2|30"""
        corrupted_model_output = """A|Umur
1|dua puluh lima
2|31"""
        data, _, _ = create_document_file("xlsx", prompt, corrupted_model_output)
        validate_document_file("xlsx", prompt, data)
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb["Data Pegawai"]
            self.assertEqual((ws["B2"].value, ws["B3"].value), (25, 30))
        finally:
            wb.close()

        with self.assertRaisesRegex(ValueError, "harus berupa angka"):
            create_document_file(
                "xlsx",
                "Buat Excel dengan kolom Umur, tanpa tabel sumber di prompt.",
                """Nama|Umur
Andi|dua puluh lima""",
            )

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
        from datetime import datetime
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
            self.assertEqual(ws["B2"].value, datetime(2026, 11, 20))
            self.assertEqual(ws["B3"].value, datetime(2026, 11, 21))
            self.assertEqual(ws["B4"].value, datetime(2026, 11, 22))
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

        # Non-calculating readers must also receive cached formula results.
        cached_wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            cached_ws = cached_wb["Audit Stok"]
            self.assertEqual(cached_ws["G2"].value, 12)
            self.assertEqual(cached_ws["I2"].value, 90000000)
            self.assertEqual(cached_ws["G10"].value, 0)
            self.assertEqual(cached_ws["I10"].value, 0)
            self.assertEqual(cached_ws["G12"].value, 212)
            self.assertEqual(cached_ws["I12"].value, 189575000)
        finally:
            cached_wb.close()

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


    def test_xlsx_named_stock_audit_uses_authoritative_fixture_when_model_output_is_malformed(self):
        from openpyxl import load_workbook
        prompt = """Buat file Excel bernama TES_PASS_RENDER_XLSX_03.xlsx.
Sheet: Audit Stok.
Buat tabel dengan 10 kolom:
No, Kode, Nama, Stok Awal, Barang Masuk, Barang Keluar, Stok Akhir, Harga Satuan, Total Nilai Stok, Tanggal Update.
Masukkan 10 data barang BRG001 sampai BRG010 sesuai data pengujian audit stok yang sudah ditentukan.
Stok Akhir = Stok Awal + Barang Masuk - Barang Keluar.
Total Nilai Stok = Stok Akhir x Harga Satuan.
Gunakan formula Excel asli, tambahkan baris TOTAL dengan formula SUM."""
        malformed_model_output = """Berikut hasil audit stok yang diminta.
Data sudah disusun sesuai ketentuan.
| No | Kode | Nama | Stok Awal | Barang Masuk | Barang Keluar | Stok Akhir | Harga Satuan | Total Nilai Stok | Tanggal Update |
| 1 | BRG001 | Laptop | sepuluh unit | 5 | 3 | 12 | 7.500.000 | 90.000.000 | 20/11/2026 |"""
        data, filename, _ = create_document_file("xlsx", prompt, malformed_model_output)
        self.assertEqual(filename, "TES_PASS_RENDER_XLSX_03.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (10, 10, "Audit Stok"))
        wb = load_workbook(io.BytesIO(data), data_only=False)
        try:
            ws = wb["Audit Stok"]
            self.assertEqual(ws.max_row, 12)
            self.assertEqual(ws["B2"].value, "BRG001")
            self.assertEqual(ws["D2"].value, 10)
            self.assertEqual(ws["D7"].value, 0)
            self.assertEqual(ws["F9"].value, 0)
            self.assertEqual(ws["G10"].value, "=D10+E10-F10")
            self.assertEqual(ws["I10"].value, "=G10*H10")
            self.assertEqual(ws["G12"].value, "=SUM(G2:G11)")
            self.assertEqual(ws["I12"].value, "=SUM(I2:I11)")
            self.assertEqual(ws.freeze_panes, "A2")
            self.assertEqual(ws.auto_filter.ref, "A1:J12")
        finally:
            wb.close()


    def test_xlsx_explicit_prompt_table_overrides_corrupted_model_output(self):
        from openpyxl import load_workbook
        prompt = """Buat XLSX bernama SOURCE.xlsx, sheet Data, tepat 2 baris data dan 2 kolom.
Nama|Harga
Laptop|1500000
Mouse|150000"""
        model_output = "Nama|Harga\nLaptop|bukan angka\nProduk karangan|999999999"
        data, _, _ = create_document_file("xlsx", prompt, model_output)
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (2, 2, "Data"))
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb["Data"]
            self.assertEqual(ws["A2"].value, "Laptop")
            self.assertEqual(ws["B2"].value, 1500000)
            self.assertEqual(ws["A3"].value, "Mouse")
            self.assertEqual(ws["B3"].value, 150000)
        finally:
            wb.close()

    def test_xlsx_refusal_response_uses_explicit_prompt_table_authoritatively(self):
        from openpyxl import load_workbook

        prompt = """Buat file Excel (.xlsx).
Nama worksheet wajib persis: `Data Pegawai`
| No | Nama | Umur | Catatan |
|---|---|---:|---|
| 1 | Andi | 25 | =1+1 |
| 2 | Budi | 30 | +SUM(1,1) |
| 3 | Citra | 28 | @SUM(1,1) |"""
        refusal = (
            "Maaf, saya tidak dapat membuat file Excel secara langsung. "
            "Saya hanya dapat memberikan panduan dan contoh tabel dalam teks."
        )

        data, filename, _ = create_document_file("xlsx", prompt, refusal)
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (3, 4, "Data Pegawai"))

        wb = load_workbook(io.BytesIO(data), data_only=False)
        try:
            ws = wb["Data Pegawai"]
            self.assertEqual(ws.max_row, 4)
            self.assertEqual(ws.max_column, 4)
            self.assertEqual([ws.cell(row, 2).value for row in range(2, 5)], ["Andi", "Budi", "Citra"])
            for row, expected in enumerate(("=1+1", "+SUM(1,1)", "@SUM(1,1)"), start=2):
                self.assertEqual(ws.cell(row, 4).value, expected)
                self.assertEqual(ws.cell(row, 4).data_type, "s")
        finally:
            wb.close()

    def test_xlsx_empty_model_response_uses_complete_prompt_table(self):
        prompt = "Buat XLSX dengan tabel berikut, tepat 1 baris data dan 2 kolom.\nKode|Stok\nA01|12"
        data, _, _ = create_document_file("xlsx", prompt, "")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"]), (1, 2))

    def test_xlsx_parses_indonesian_currency_and_thousands_separators(self):
        from openpyxl import load_workbook
        prompt = "Buat XLSX berisi daftar produk dengan kolom Harga dan Stok."
        content = "Nama|Harga|Stok\nLaptop|Rp 1.500.000|1.250\nMouse|Rp150.000|25"
        data, _, _ = create_document_file("xlsx", prompt, content)
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb.active
            self.assertEqual(ws["B2"].value, 1500000)
            self.assertEqual(ws["C2"].value, 1250)
            self.assertEqual(ws["B3"].value, 150000)
            self.assertEqual(ws["C3"].value, 25)
        finally:
            wb.close()


    def test_xlsx_total_row_label_in_second_column_allows_blank_numeric_no(self):
        from openpyxl import load_workbook

        prompt = """Buat XLSX bernama TES_TOTAL_ROW_LABEL.xlsx dengan worksheet Inventaris.
Buat tepat 10 baris data dan 3 kolom.
Kolom: No | Nama Barang | Stok
Tambahkan baris TOTAL di akhir. Kolom No pada baris TOTAL boleh kosong.
No dan Stok untuk setiap baris barang wajib angka Excel.
Aktifkan autofilter dan freeze panes A2."""
        content = """No|Nama Barang|Stok
1|Laptop|10
2|Mouse|20
3|Keyboard|30
4|Monitor|40
5|Printer|50
6|Webcam|60
7|Headset|70
8|SSD|80
9|RAM|90
10|Flashdisk|100
| | TOTAL | 550 |"""
        data, filename, _ = create_document_file("xlsx", prompt, content)
        self.assertEqual(filename, "TES_TOTAL_ROW_LABEL.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (10, 3, "Inventaris"))
        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb["Inventaris"]
            self.assertEqual(ws.max_row, 12)
            self.assertIsNone(ws["A12"].value)
            self.assertEqual(ws["B12"].value, "TOTAL")
            self.assertEqual(ws["C12"].value, 550)
            for row in range(2, 12):
                self.assertIsInstance(ws.cell(row, 1).value, (int, float))
                self.assertIsInstance(ws.cell(row, 3).value, (int, float))
        finally:
            wb.close()



    def test_xlsx_formula_like_user_text_is_preserved_as_literal_string(self):
        from openpyxl import load_workbook

        prompt = """Buat XLSX bernama TES_REDTEAM_CLOUZER.xlsx dengan worksheet Data Pegawai.
Gunakan tepat 1 baris data dan 3 kolom.
Nama|Umur|Catatan
Intan|26|=1+1"""
        content = "Nama|Umur|Catatan\nIntan|26|=1+1"
        data, _, _ = create_document_file("xlsx", prompt, content)
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (1, 3, "Data Pegawai"))

        wb = load_workbook(io.BytesIO(data), data_only=False)
        try:
            ws = wb["Data Pegawai"]
            self.assertEqual(ws["C2"].value, "=1+1")
            self.assertEqual(ws["C2"].data_type, "s")
        finally:
            wb.close()

        cached_wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            cached_ws = cached_wb["Data Pegawai"]
            self.assertEqual(cached_ws["C2"].value, "=1+1")
            self.assertEqual(cached_ws["C2"].data_type, "s")
        finally:
            cached_wb.close()

    def test_xlsx_requested_sheet_name_indonesian_instruction_variants(self):
        prompts = (
            "Worksheet wajib bernama: Data Pegawai",
            "Nama worksheet harus bernama Data Pegawai",
            "Sheet: Data Pegawai",
            "Buat worksheet bernama `Data Pegawai` dengan data berikut",
            "Nama worksheet wajib persis: `Data Pegawai`",
            "Nama worksheet harus persis `Data Pegawai`",
            "Nama worksheet wajib: `Data Pegawai`",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(_requested_sheet_title(prompt), "Data Pegawai")
                data, _, _ = create_document_file(
                    "xlsx", prompt, "No\tNama\tUmur\n1\tAndi\t25"
                )
                summary = validate_document_file("xlsx", prompt, data)
                self.assertEqual(summary["sheet_name"], "Data Pegawai")


    def test_detect_six_additional_native_formats(self):
        prompts = {
            "Buat file Markdown bernama TES_MARKDOWN_CLOUZER.md": "md",
            "Buat file HTML bernama TES_HTML_CLOUZER.html": "html",
            "Buat file XML bernama TES_XML_CLOUZER.xml": "xml",
            "Buat file YAML bernama TES_YAML_CLOUZER.yaml": "yaml",
            "Buat file RTF bernama TES_RTF_CLOUZER.rtf": "rtf",
            "Buat file ODT bernama TES_ODT_CLOUZER.odt": "odt",
        }
        for prompt, expected in prompts.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(detect_requested_file_format(prompt), expected)

    def test_markdown_native_extension_and_content(self):
        content = "# Laporan CLOUZER\n\n## Hasil\n\n**Lulus** untuk format Markdown."
        data, filename, mime = create_document_file("md", "Buat Markdown bernama TES_MARKDOWN_CLOUZER.md", content)
        self.assertEqual(filename, "TES_MARKDOWN_CLOUZER.md")
        self.assertIn("text/markdown", mime)
        self.assertEqual(data.decode("utf-8").strip(), content)
        self.assertTrue(validate_document_file("md", "Buat Markdown", data)["valid"])

    def test_html_native_document_and_table_validation(self):
        content = """<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8"><title>Tes</title></head><body><h1>Tes</h1><table><thead><tr><th>Nama</th></tr></thead><tbody><tr><td>Andi</td></tr></tbody></table></body></html>"""
        data, filename, mime = create_document_file("html", "Buat HTML dengan tabel asli bernama TES_HTML_CLOUZER.html", content)
        self.assertEqual(filename, "TES_HTML_CLOUZER.html")
        self.assertIn("text/html", mime)
        self.assertTrue(validate_document_file("html", "Buat HTML dengan tabel", data)["valid"])
        with self.assertRaisesRegex(ValueError, "tabel asli"):
            validate_document_file("html", "Buat HTML dengan tabel", b"<!DOCTYPE html><html><head></head><body>Tanpa tabel</body></html>")

    def test_xml_is_parsed_and_malformed_xml_rejected(self):
        content = '<?xml version="1.0" encoding="UTF-8"?><pengujian><sistem>CLOUZER</sistem></pengujian>'
        data, filename, mime = create_document_file("xml", "Buat XML bernama TES_XML_CLOUZER.xml", content)
        self.assertEqual(filename, "TES_XML_CLOUZER.xml")
        self.assertIn("application/xml", mime)
        self.assertTrue(validate_document_file("xml", "Buat XML", data)["valid"])
        with self.assertRaises(ValueError):
            validate_document_file("xml", "Buat XML", b"<pengujian><rusak></pengujian>")

    def test_yaml_native_extension_and_safe_validation(self):
        content = "sistem: CLOUZER\nstatus: pengujian\ndata:\n  - id: 1\n    nama: Andi\n    hasil: Lulus\n"
        data, filename, mime = create_document_file("yaml", "Buat YAML bernama TES_YAML_CLOUZER.yaml", content)
        self.assertEqual(filename, "TES_YAML_CLOUZER.yaml")
        self.assertIn("yaml", mime)
        self.assertTrue(validate_document_file("yaml", "Buat YAML", data)["valid"])
        with self.assertRaises(ValueError):
            validate_document_file("yaml", "Buat YAML", b"ini bukan yaml: [")

    def test_rtf_is_real_rtf_not_renamed_plain_text(self):
        content = r"{\rtf1\ansi{\b Teks tebal}\par{\i Teks miring}\par}"
        data, filename, mime = create_document_file("rtf", "Buat RTF bernama TES_RTF_CLOUZER.rtf", content)
        self.assertEqual(filename, "TES_RTF_CLOUZER.rtf")
        self.assertEqual(mime, "application/rtf")
        self.assertTrue(validate_document_file("rtf", "Buat RTF", data)["valid"])
        with self.assertRaisesRegex(ValueError, "header RTF"):
            validate_document_file("rtf", "Buat RTF", b"Ini hanya teks biasa")

    def test_odt_is_native_package_with_real_table(self):
        content = """# LAPORAN PENGUJIAN ODT

## Informasi Pengujian

Dokumen OpenDocument CLOUZER.

| No | Nama | Status |
|---|---|---|
| 1 | Andi | Lulus |
| 2 | Budi | Lulus |"""
        prompt = "Buat file ODT bernama TES_ODT_CLOUZER.odt dengan tabel asli."
        data, filename, mime = create_document_file("odt", prompt, content)
        self.assertEqual(filename, "TES_ODT_CLOUZER.odt")
        self.assertEqual(mime, "application/vnd.oasis.opendocument.text")
        summary = validate_document_file("odt", prompt, data)
        self.assertTrue(summary["valid"])
        self.assertEqual(summary["tables"], 1)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertEqual(archive.read("mimetype"), b"application/vnd.oasis.opendocument.text")
            root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(archive.read("content.xml"))
            table_tag = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table"
            self.assertTrue(any(node.tag == table_tag for node in root.iter()))

    def test_pdf_removes_markdown_heading_and_emphasis_tokens(self):
        import pymupdf
        content = "## Hasil Pengujian\n\n**CLOUZER** berhasil membuat dokumen."
        data, _, _ = create_document_file("pdf", "Buat PDF", content)
        with pymupdf.open(stream=data, filetype="pdf") as document:
            extracted = "\n".join(page.get_text() for page in document)
        self.assertNotIn("##", extracted)
        self.assertNotIn("**", extracted)
        self.assertIn("Hasil Pengujian", extracted)
        self.assertIn("CLOUZER", extracted)


    def test_xlsx_gaji_column_is_native_numeric_and_validator_rejects_text(self):
        from openpyxl import load_workbook

        prompt = """Buat XLSX bernama TES_LIVE_CLOUZER.xlsx dengan worksheet Data Pegawai.
Buat tepat 5 baris data dan 4 kolom.
Nama|Umur|Jabatan|Gaji
Andi Pratama|28|Staf Administrasi|5500000
Siti Rahmawati|32|Manajer Keuangan|12000000
Budi Santoso|26|Staf Teknologi Informasi|7500000
Dewi Lestari|30|Supervisor Pemasaran|9000000
Rizky Saputra|24|Staf Layanan Pelanggan|5000000"""
        data, filename, _ = create_document_file("xlsx", prompt, "")
        self.assertEqual(filename, "TES_LIVE_CLOUZER.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (5, 4, "Data Pegawai"))

        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb["Data Pegawai"]
            self.assertEqual([ws.cell(row, 4).value for row in range(2, 7)],
                             [5500000, 12000000, 7500000, 9000000, 5000000])
            for row in range(2, 7):
                self.assertIsInstance(ws.cell(row, 4).value, (int, float))
                self.assertNotIsInstance(ws.cell(row, 4).value, bool)
            self.assertTrue(ws["A1"].font.bold)
            self.assertTrue(all(ws.column_dimensions[col].width >= 12 for col in ("A", "B", "C", "D")))
        finally:
            wb.close()

        broken = load_workbook(io.BytesIO(data), data_only=False)
        try:
            broken["Data Pegawai"]["D2"] = "5500000"
            output = io.BytesIO()
            broken.save(output)
        finally:
            broken.close()
        with self.assertRaisesRegex(ValueError, "Kolom Gaji harus berisi angka"):
            validate_document_file("xlsx", prompt, output.getvalue())




    def test_xlsx_recovers_missing_header_from_explicit_schema_and_validates_all_numeric_columns(self):
        from openpyxl import load_workbook

        prompt = """Buat XLSX bernama TES_SCHEMA_RECOVERY.xlsx.
Buat worksheet bernama Data Pegawai.
Buat tepat 5 baris data dan 4 kolom Nama, Umur, Jabatan, Gaji.
Umur dan Gaji wajib berupa angka Excel."""
        # Model returned five records but omitted the header row.
        content = """Andi Pratama|28|Staf Administrasi|5500000
Siti Rahmawati|32|Manajer Keuangan|12000000
Budi Santoso|26|Staf Teknologi Informasi|7500000
Dewi Lestari|30|Supervisor Pemasaran|9000000
Rizky Saputra|24|Staf Layanan Pelanggan|5000000"""
        data, filename, _ = create_document_file("xlsx", prompt, content)
        self.assertEqual(filename, "TES_SCHEMA_RECOVERY.xlsx")
        summary = validate_document_file("xlsx", prompt, data)
        self.assertEqual((summary["rows"], summary["columns"], summary["sheet_name"]), (5, 4, "Data Pegawai"))

        wb = load_workbook(io.BytesIO(data), data_only=True)
        try:
            ws = wb["Data Pegawai"]
            self.assertEqual([ws.cell(1, col).value for col in range(1, 5)],
                             ["Nama", "Umur", "Jabatan", "Gaji"])
            for row in range(2, 7):
                for col in (2, 4):
                    self.assertIsInstance(ws.cell(row, col).value, (int, float))
                    self.assertNotIsInstance(ws.cell(row, col).value, bool)
            self.assertEqual(ws["D2"].value, 5500000)
            self.assertEqual(ws["D6"].value, 5000000)
            self.assertTrue(ws["A1"].font.bold)
        finally:
            wb.close()

        broken = load_workbook(io.BytesIO(data), data_only=False)
        try:
            broken["Data Pegawai"]["A1"] = "Nama Pegawainya"
            output = io.BytesIO()
            broken.save(output)
        finally:
            broken.close()
        with self.assertRaisesRegex(ValueError, "Header XLSX tidak sesuai"):
            validate_document_file("xlsx", prompt, output.getvalue())


    def test_refusal_uses_explicit_fenced_source_for_all_supported_formats(self):
        prompt = "Buat file dokumen.\nIsi dokumen sumber:\n```text\nJudul Audit CLOUZER\n\nKonten sumber harus dipertahankan persis.\n```\nJangan tambahkan data."
        expected = "Judul Audit CLOUZER\n\nKonten sumber harus dipertahankan persis."
        formats = ("txt", "pdf", "docx", "xlsx", "pptx", "zip", "csv", "json", "md", "html", "xml", "yaml", "rtf", "odt")
        for fmt in formats:
            with self.subTest(fmt=fmt):
                source = resolve_document_source(fmt, prompt, "Saya tidak bisa membuat file.", model_refused=True)
                self.assertEqual(source, expected)

    def test_refusal_without_explicit_source_is_retryable_and_fails_closed(self):
        # The entrypoint catches this invalid-source error to make one format-aware
        # retry; if that retry is also refused, the operation still fails closed.
        with self.assertRaisesRegex(ValueError, "tidak akan mengarang"):
            resolve_document_source("pdf", "Buat file PDF tentang CLOUZER.", "Saya tidak bisa membuat file.", model_refused=True)

    def test_non_refusal_keeps_model_content(self):
        content = "# Judul\n\nIsi dari model."
        self.assertEqual(resolve_document_source("pdf", "Buat PDF", content), content)

    def test_refusal_extracts_explicit_markdown_table_for_pdf(self):
        prompt = "Buat PDF dari tabel berikut:\n| Nama | Umur |\n|---|---|\n| Andi | 25 |\n| Budi | 30 |"
        source = resolve_document_source("pdf", prompt, "Tidak bisa membuat file.", model_refused=True)
        self.assertIn("Andi", source)
        self.assertIn("Budi", source)

    def test_refusal_fallback_builds_and_validates_each_native_format(self):
        fixtures = {
            "txt": ("Buat file TXT", "Isi TXT CLOUZER yang dipertahankan."),
            "pdf": ("Buat file PDF", "# Laporan PDF\n\nIsi PDF CLOUZER yang dipertahankan."),
            "docx": ("Buat file DOCX", "# Laporan DOCX\n\nIsi DOCX CLOUZER yang dipertahankan."),
            "xlsx": ("Buat Excel tepat 2 baris data dan 2 kolom:\nNama|Umur\nAndi|25\nBudi|30", ""),
            "pptx": ("Buat PowerPoint 2 slide", "Pengenalan\nCLOUZER adalah bot Discord.\n--- SLIDE ---\nPengujian\nPembuatan file berhasil divalidasi."),
            "zip": ("Buat ZIP berisi README.txt dan data.json", "=== FILE: README.txt ===\nDokumen CLOUZER.\n=== FILE: data.json ===\n{\"status\":\"lulus\"}"),
            "csv": ("Buat CSV dengan 2 baris data dan 2 kolom", "Nama,Umur\nAndi,25\nBudi,30"),
            "json": ("Buat JSON", "{\"project\":\"CLOUZER\",\"status\":\"lulus\"}"),
            "md": ("Buat Markdown", "# CLOUZER\n\nDokumen Markdown."),
            "html": ("Buat HTML", "<!DOCTYPE html><html lang=\"id\"><head><title>CLOUZER</title></head><body><h1>Audit</h1></body></html>"),
            "xml": ("Buat XML", "<?xml version=\"1.0\"?><audit><status>lulus</status></audit>"),
            "yaml": ("Buat YAML", "project: CLOUZER\nstatus: lulus\n"),
            "rtf": ("Buat RTF", r"{\rtf1\ansi CLOUZER audit passed\par}"),
            "odt": ("Buat ODT", "Dokumen OpenDocument CLOUZER dengan isi yang valid."),
        }
        for fmt, (instruction, explicit_content) in fixtures.items():
            with self.subTest(fmt=fmt):
                if fmt == "xlsx":
                    prompt = instruction
                    source = resolve_document_source(fmt, prompt, "Saya tidak bisa membuat file.", model_refused=True)
                else:
                    prompt = instruction + "\nIsi dokumen sumber:\n```text\n" + explicit_content + "\n```\nGunakan isi sumber tanpa mengarang data."
                    source = resolve_document_source(fmt, prompt, "Saya tidak bisa membuat file.", model_refused=True)
                data, filename, mime = create_document_file(fmt, prompt, source)
                self.assertTrue(data)
                self.assertTrue(filename.endswith("." + fmt))
                self.assertTrue(mime)
                self.assertTrue(validate_document_file(fmt, prompt, data)["valid"])


    def test_pdf_renders_markdown_bold_italic_and_real_list_markers(self):
        import pymupdf
        content = (
            "# Laporan CLOUZER\n\n"
            "Teks **tebal** dan *miring* harus memiliki gaya font yang benar.\n\n"
            "1. Langkah pertama\n"
            "2. Langkah kedua\n"
            "- Butir pertama\n"
            "- Butir kedua\n"
        )
        data, _, _ = create_document_file("pdf", "Buat PDF dengan bold italic daftar bernomor dan daftar berpoin", content)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            spans = [
                span
                for page in doc
                for block in page.get_text("dict")["blocks"]
                if "lines" in block
                for line in block["lines"]
                for span in line["spans"]
            ]
            extracted = "\n".join(page.get_text() for page in doc)
            self.assertIn("tebal", extracted)
            self.assertIn("miring", extracted)
            self.assertTrue(any("tebal" in span["text"] and span["flags"] & 16 for span in spans))
            self.assertTrue(any("miring" in span["text"] and span["flags"] & 2 for span in spans))
            for item in ("Langkah pertama", "Langkah kedua", "Butir pertama", "Butir kedua"):
                self.assertIn(item, extracted)
            self.assertNotIn("**", extracted)
            self.assertNotIn("*miring*", extracted)

    def test_pdf_text_geometry_stays_inside_page_bounds(self):
        import pymupdf
        content = "# Audit CLOUZER\n\n" + "\n\n".join(
            "Paragraf pengujian nomor %d dengan teks yang cukup panjang untuk menguji pembungkusan dan batas halaman." % i
            for i in range(1, 100)
        )
        data, _, _ = create_document_file("pdf", "Buat PDF", content)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            self.assertGreaterEqual(doc.page_count, 2)
            for page in doc:
                self.assertAlmostEqual(page.rect.width, 595, delta=1)
                self.assertAlmostEqual(page.rect.height, 842, delta=1)
                for block in page.get_text("dict")["blocks"]:
                    if "lines" not in block:
                        continue
                    for line in block["lines"]:
                        for span in line["spans"]:
                            x0, y0, x1, y1 = span["bbox"]
                            self.assertGreaterEqual(x0, 35)
                            self.assertGreaterEqual(y0, 15)
                            self.assertLessEqual(x1, 560)
                            self.assertLessEqual(y1, 835)


    def test_pdf_validator_enforces_minimum_pages_a4_and_required_styles(self):
        content = (
            "# Laporan CLOUZER\n\n"
            "Teks **tebal** dan *miring* untuk memvalidasi font.\n\n"
            "1. Langkah pertama\n"
            "- Butir pertama\n\n"
            + "\n\n".join(
                "Paragraf panjang nomor %d untuk memastikan dokumen melewati empat halaman A4 dengan tata letak yang stabil." % i
                for i in range(1, 230)
            )
            + "\n\n## Kesimpulan\n\nKesimpulan lengkap berada pada halaman yang sama dengan heading dan menjelaskan hasil pengujian."
        )
        prompt = "Buat PDF A4 minimal 4 halaman dengan teks bold italic, daftar bernomor dan daftar berpoin, serta heading Kesimpulan bersama paragraf pembuka."
        data, _, _ = create_document_file("pdf", prompt, content)
        summary = validate_document_file("pdf", prompt, data)
        self.assertGreaterEqual(summary["pages"], 4)
        self.assertTrue(summary["text_layer"])
        self.assertTrue(summary["within_page_bounds"])
        self.assertGreater(summary["bold_spans"], 0)
        self.assertGreater(summary["italic_spans"], 0)


    def test_pdf_validator_counts_ten_product_rows_and_repeated_table_header(self):
        prompt = "Buat PDF dengan tabel 10 produk dan header tabel berulang di halaman lanjutan."
        rows = ["No | Produk | Deskripsi", "--- | --- | ---"]
        for i in range(1, 11):
            rows.append(
                f"{i} | Produk {i} | " + ("Deskripsi panjang untuk menguji pembungkusan teks pada tabel dan menjaga seluruh nilai tetap berada dalam sel yang benar tanpa terpotong. " * 9)
            )
        content = "# Audit Tabel CLOUZER\n\n" + "\n".join(rows)
        data, _, _ = create_document_file("pdf", prompt, content)
        summary = validate_document_file("pdf", prompt, data)
        self.assertGreaterEqual(summary["table_row_groups"], 11)
        self.assertGreaterEqual(summary["table_header_pages"], 2)


    def _master_final_pdf_fixture(self):
        prompt = """Buat PDF sungguhan bernama Tes_PDF_Final.pdf dan kirim sebagai attachment Discord.
Judul laporan: LAPORAN PENGUJIAN GENERATOR PDF CLOUZER.
Wajib ada bagian Pendahuluan, Tujuan Pengujian, Metode Pengujian, Hasil Pengujian, dan Kesimpulan, masing-masing minimal dua paragraf.
Buat tabel berisi 10 data produk dengan 7 kolom: No, Nama, Produk, Kategori, Harga, Stok, Status.
Gunakan format A4 minimal 4 halaman, teks bold dan italic, daftar bernomor dan daftar berpoin.
Ulangi header jika tabel berlanjut ke halaman berikutnya. Pastikan teks dapat diseleksi, disalin, dan dicari, termasuk kata CLOUZER."""
        long_a = (
            "Pengujian ini dilakukan dengan prosedur yang terukur untuk memastikan setiap tahap berjalan sesuai tujuan. "
            "Data diperiksa dari sisi kelengkapan, konsistensi, keterbacaan, dan kesesuaian terhadap spesifikasi yang ditetapkan. "
            "Hasil utama **harus dapat ditelusuri** dan dijelaskan dengan *konsistensi* tanpa mengarang bukti yang tidak tersedia. " * 8
        )
        long_b = (
            "Catatan evaluasi menjelaskan hasil yang benar-benar diamati, batas pemeriksaan, dan alasan setiap status diberikan. "
            "Setiap kesimpulan harus didukung oleh keluaran yang dapat diperiksa ulang serta tidak boleh menyamakan pengujian otomatis "
            "dengan pemeriksaan visual atau pengujian runtime. " * 8
        )
        sections = []
        for heading in ("Pendahuluan", "Tujuan Pengujian", "Metode Pengujian", "Hasil Pengujian", "Kesimpulan"):
            sections.extend((f"## {heading}", long_a, long_b))
            if heading == "Metode Pengujian":
                sections.extend(("1. Periksa file hasil", "- Cocokkan hasil dengan kriteria"))
            if heading == "Hasil Pengujian":
                sections.append(
                    "No | Nama | Produk | Kategori | Harga | Stok | Status\n"
                    "--- | --- | --- | --- | --- | --- | ---\n" +
                    "\n".join(
                        f"{i} | Nama {i} | Produk {i} | Elektronik | Rp{i}00.000 | {i * 3} | PASS"
                        for i in range(1, 11)
                    )
                )
        content = "# LAPORAN PENGUJIAN GENERATOR PDF CLOUZER\n\n" + "\n\n".join(sections)
        return prompt, content


    def test_master_final_page_word_cell_extraction_matches_legacy(self):
        import pymupdf
        from clouzer_file_creation import _extract_cell_text_from_words

        prompt, content = self._master_final_pdf_fixture()
        data, _, _ = create_document_file("pdf", prompt, content)
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            compared_cells = 0
            for page in doc:
                page_words = page.get_textpage().extractWORDS()
                grouped = {}
                for drawing in page.get_drawings():
                    rect = drawing.get("rect")
                    if rect is None or rect.x0 < 40 or rect.x1 > 555 or rect.y0 < 75 or rect.y1 > 795:
                        continue
                    if rect.width < 30 or rect.height < 10:
                        continue
                    grouped.setdefault(round(rect.y0, 1), []).append(rect)
                for cells in grouped.values():
                    if len(cells) != 7:
                        continue
                    cells.sort(key=lambda rect: rect.x0)
                    for cell in cells:
                        rect = pymupdf.Rect(
                            cell.x0 + 1, cell.y0 + 1, cell.x1 - 1, cell.y1 - 1,
                        )
                        legacy = page.get_textbox(rect)
                        optimized = _extract_cell_text_from_words(page_words, rect)
                        self.assertEqual(
                            " ".join(optimized.split()), " ".join(legacy.split()),
                            "Page-level word extraction must preserve normalized cell text and reading order.",
                        )
                        compared_cells += 1
            self.assertGreaterEqual(compared_cells, 77)
        finally:
            doc.close()


    def test_master_final_pdf_detector_accepts_ten_data_produk_wording(self):
        from clouzer_file_creation import _is_master_final_pdf_prompt
        prompt, _ = self._master_final_pdf_fixture()
        self.assertIn("10 data produk", prompt.casefold())
        self.assertTrue(_is_master_final_pdf_prompt(prompt))
        self.assertFalse(_is_master_final_pdf_prompt(
            prompt.replace("10 data produk", "10 data barang", 1)
        ))

    def test_master_final_detector_does_not_require_backend_owned_table_columns(self):
        from clouzer_file_creation import _is_master_final_pdf_prompt
        prompt = """Buatkan file PDF dengan judul LAPORAN PENGUJIAN GENERATOR PDF CLOUZER.
        Memuat lima bagian: Pendahuluan, Tujuan Pengujian, Metode Pengujian,
        Hasil Pengujian, dan Kesimpulan. Backend menyusun tabel 10 produk."""
        self.assertTrue(_is_master_final_pdf_prompt(prompt))

    def test_master_final_source_uses_backend_template_for_title_table_and_lists(self):
        prompt, model_content = self._master_final_pdf_fixture()
        source = resolve_document_source("pdf", prompt, model_content)
        self.assertIn("# LAPORAN PENGUJIAN GENERATOR PDF CLOUZER", source)
        self.assertIn("No | Nama | Produk | Kategori | Harga | Stok | Status", source)
        self.assertIn("1 | Laptop | Laptop bisnis 14 inci", source)
        self.assertIn("10 | Headset | Headset USB", source)
        self.assertIn("1. **Periksa**", source)
        self.assertIn("*hasil terukur*", source)
        self.assertNotIn("Nama 1 | Produk 1", source)
        self.assertEqual(len(re.findall(r"(?m)^## ", source)), 5)

    def test_master_final_pdf_enforces_report_sections_paragraphs_and_table(self):
        import pymupdf
        prompt, content = self._master_final_pdf_fixture()
        data, filename, mime = create_document_file("pdf", prompt, content)
        # The first pass is intentionally lightweight; the exact final PDF
        # still receives the complete deep validator below.
        baseline = validate_document_file(
            "pdf", prompt, data, enforce_minimum_pages=False, deep_inspection=False
        )
        self.assertTrue(baseline["valid"])
        self.assertNotIn("vector_drawings", baseline)
        self.assertNotIn("master_final_report", baseline)
        summary = validate_document_file("pdf", prompt, data)
        self.assertEqual(filename, "Tes_PDF_Final.pdf")
        self.assertEqual(mime, "application/pdf")
        self.assertGreaterEqual(summary["pages"], 4)
        self.assertLessEqual(summary["pages"], 7, "The long-content fixture must not exceed its measured seven-page baseline.")
        self.assertTrue(summary["master_final_report"])
        self.assertEqual(summary["product_rows"], 10)
        self.assertEqual(summary["table_columns"], 7)
        self.assertEqual(summary["required_sections"], [
            "Pendahuluan", "Tujuan Pengujian", "Metode Pengujian", "Hasil Pengujian", "Kesimpulan"
        ])
        self.assertTrue(all(count >= 2 for count in summary["paragraphs_per_section"].values()))
        self.assertTrue(summary["conclusion_with_opening_paragraph"])
        self.assertGreaterEqual(summary["last_page_text_chars"], 220)
        self.assertTrue(all(value >= 120 for value in summary["page_body_chars"]))
        self.assertTrue(summary["page_numbers_correct"])
        self.assertTrue(summary["table_header_style"])
        self.assertTrue(summary["alternating_table_rows"])
        self.assertTrue(summary["table_cell_borders"])
        self.assertTrue(summary["body_text_geometry"])
        self.assertFalse(summary["body_text_overlaps"])
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            extracted = "\n".join(page.get_text() for page in doc)
        self.assertIn("LAPORAN PENGUJIAN GENERATOR PDF CLOUZER", extracted)
        self.assertIn("CLOUZER", extracted)
        self.assertIn("Ringkasan Validasi Otomatis CLOUZER", extracted)
        self.assertIn("Inspeksi visual manual", extracted)
        self.assertIn("NOT VERIFIED", extracted)
        self.assertEqual(re.sub(r"\s+", " ", extracted).casefold().count("not verified"), 3)
        self.assertIn("Kirim attachment Discord", extracted)
        page_match = re.search(r"Jumlah halaman sebelum lampiran validasi:\s*(\d+)", extracted)
        self.assertIsNotNone(page_match)
        self.assertLessEqual(int(page_match.group(1)), 6, "The long-content fixture may use up to six report pages before its appendix.")
        self.assertLessEqual(int(page_match.group(1)), summary["pages"])


    def test_master_final_pdf_short_ai_narrative_fits_five_pages_after_backend_summary(self):
        prompt, _ = self._master_final_pdf_fixture()
        paragraph_a = (
            "Pengujian generator dokumen menilai ketepatan struktur, keterbacaan, konsistensi isi, "
            "dan kesesuaian keluaran terhadap persyaratan yang telah ditentukan. "
        ) * 4
        paragraph_b = (
            "Setiap temuan harus dapat ditelusuri melalui artefak, pengukuran otomatis, atau pemeriksaan manual "
            "yang dinyatakan secara jujur sesuai batas bukti yang tersedia. "
        ) * 4
        model_content = "\n\n".join(
            part
            for section in ("Pendahuluan", "Tujuan Pengujian", "Metode Pengujian", "Hasil Pengujian", "Kesimpulan")
            for part in ("## " + section, paragraph_a, paragraph_b)
        )
        resolved_content = resolve_document_source("pdf", prompt, model_content)
        data, filename, mime = create_document_file("pdf", prompt, resolved_content)
        summary = validate_document_file("pdf", prompt, data)
        self.assertEqual(filename, "Tes_PDF_Final.pdf")
        self.assertEqual(mime, "application/pdf")
        self.assertGreaterEqual(summary["pages"], 4)
        self.assertLessEqual(summary["pages"], 5, "The short-content fixture should fit four report pages plus one validation appendix page.")
        self.assertTrue(summary["master_final_report"])
        self.assertEqual(summary["product_rows"], 10)
        self.assertEqual(summary["table_columns"], 7)
        self.assertGreaterEqual(summary["last_page_text_chars"], 220)
        self.assertTrue(all(count >= 120 for count in summary["page_body_chars"]))
        self.assertTrue(all(count >= 4 for count in summary["paragraphs_per_section"].values()))
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            extracted = "\n".join(page.get_text() for page in doc)
        self.assertIn("Jumlah halaman sebelum lampiran validasi:", extracted)
        page_match = re.search(r"Jumlah halaman sebelum lampiran validasi:\s*(\d+)", extracted)
        self.assertIsNotNone(page_match)
        self.assertLessEqual(int(page_match.group(1)), 4, "The short report body must finish by page four before its appendix.")
        self.assertIn("Elemen vektor PDF", extracted)
        self.assertEqual(re.sub(r"\s+", " ", extracted).casefold().count("not verified"), 3)

    def test_master_final_pdf_rejects_wrong_table_header_schema(self):
        prompt, content = self._master_final_pdf_fixture()
        content = content.replace(
            "No | Nama | Produk | Kategori | Harga | Stok | Status",
            "No | Item | Produk | Kategori | Harga | Stok | Status",
            1,
        )
        with self.assertRaisesRegex(ValueError, "(?i)header|kolom|MASTER FINAL"):
            create_document_file("pdf", prompt, content)

    def test_master_final_pdf_rejects_unverified_placeholders_in_model_source(self):
        prompt, content = self._master_final_pdf_fixture()
        content = content.replace("Hasil utama", "Hasil utama NOT VERIFIED", 1)
        with self.assertRaisesRegex(ValueError, "placeholder NOT VERIFIED"):
            create_document_file("pdf", prompt, content)

    def test_master_final_pdf_rejects_section_with_only_one_paragraph(self):
        prompt, content = self._master_final_pdf_fixture()
        content = content.replace(
            "## Tujuan Pengujian\n\n" + content.split("## Tujuan Pengujian\n\n", 1)[1].split("\n\n", 1)[0] + "\n\n",
            "## Tujuan Pengujian\n\n",
            1,
        )
        with self.assertRaisesRegex(ValueError, "Tujuan Pengujian.*minimal dua"):
            create_document_file("pdf", prompt, content)




    def test_pdf_page_number_start_supports_appended_appendix_pages(self):
        import pymupdf
        from clouzer_file_creation import _make_pdf
        data = _make_pdf(
            "### Lampiran Validasi\n\nRingkasan pengujian terukur.",
            "Laporan CLOUZER",
            page_number_start=4,
        )
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            self.assertEqual(doc.page_count, 1)
            self.assertIn("Halaman 4", doc[0].get_text())

    def test_pdf_splits_oversized_paragraph_without_orphaning_following_heading(self):
        import pymupdf
        from clouzer_file_creation import _make_pdf
        long_paragraph = (
            "Paragraf panjang ini memeriksa aliran teks lintas halaman dengan kalimat yang tetap utuh. "
            "Tata letak harus mempertahankan judul bagian bersama awal paragraf berikutnya. " * 18
        )
        content = (
            "# Uji Layout PDF\n\n## Pendahuluan\n\nParagraf pembuka singkat.\n\n"
            "## Kesimpulan\n\n" + long_paragraph
        )
        data = _make_pdf(content, "Uji Layout PDF")
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            conclusion_page = next(
                index for index, page in enumerate(doc)
                if "Kesimpulan" in page.get_text()
            )
            page_text = doc[conclusion_page].get_text()
        self.assertIn("Kesimpulan", page_text)
        self.assertIn("Paragraf panjang ini memeriksa", page_text)

    def test_pdf_conditional_repeated_header_is_not_required_when_table_fits_one_page(self):
        prompt = "Buat PDF dengan tabel 10 produk. Ulangi header jika tabel berlanjut ke halaman berikutnya."
        rows = ["No | Produk | Status", "--- | --- | ---"]
        rows.extend(f"{i} | Produk {i} | PASS" for i in range(1, 11))
        data, _, _ = create_document_file("pdf", prompt, "\n".join(rows))
        summary = validate_document_file("pdf", prompt, data)
        self.assertEqual(summary["table_header_pages"], 1)
        self.assertEqual(summary["pages"], 1)





    def test_master_final_pdf_rejects_question_mark_product_placeholders(self):
        prompt, content = self._master_final_pdf_fixture()
        content = content.replace("1 | Nama 1 | Produk 1 | Elektronik | Rp100.000 | 3 | PASS",
                                  "1 | ? | ? | ? | ? | ? | ?")
        with self.assertRaisesRegex(ValueError, "placeholder"):
            create_document_file("pdf", prompt, content)

    def test_master_final_validation_summary_is_appended_after_conclusion(self):
        from clouzer_file_creation import _append_master_final_validation_summary
        content = (
            "# Laporan CLOUZER\n\n"
            "## Kesimpulan\n\n"
            "Paragraf kesimpulan pertama yang menjelaskan hasil pengujian secara lengkap.\n\n"
            "Paragraf kesimpulan kedua yang menjelaskan batasan hasil pengujian secara lengkap."
        )
        summary = {
            "pages": 4,
            "page_sizes": [(595, 842)] * 4,
            "table_content_pages": 2,
            "table_header_pages": 2,
            "product_rows": 10,
            "table_columns": 7,
            "vector_drawings": 164,
            "numbered_list": True,
            "bullet_list": True,
            "text_layer": True,
            "bold_spans": 1,
            "italic_spans": 1,
            "body_text_geometry": True,
            "body_text_overlaps": False,
        }
        result = _append_master_final_validation_summary(content, summary)
        self.assertLess(result.index("Paragraf kesimpulan kedua"),
                        result.index("Ringkasan Validasi Otomatis CLOUZER"))
        self.assertEqual(result.count("Ringkasan Validasi Otomatis CLOUZER"), 1)
        self.assertIn("| Tabel produk 10 baris dan 7 kolom | PASS |", result)
        self.assertIn("| Elemen vektor PDF | PASS |", result)
        self.assertNotIn("**PASS**", result)
        self.assertNotIn("**FAIL**", result)
        self.assertIn("10 baris produk × 7 kolom.", result)

    def test_pdf_body_overlap_sweep_matches_exhaustive_check(self):
        import pymupdf
        from clouzer_file_creation import _first_body_text_overlap

        records = [
            {"page": 0, "rect": pymupdf.Rect(40, 10, 180, 20), "text": "A"},
            {"page": 0, "rect": pymupdf.Rect(40, 20, 180, 30), "text": "touching edge"},
            {"page": 0, "rect": pymupdf.Rect(220, 22, 350, 32), "text": "vertical overlap only"},
            {"page": 0, "rect": pymupdf.Rect(40, 40, 180, 52), "text": "first collision"},
            {"page": 0, "rect": pymupdf.Rect(42, 45, 182, 58), "text": "second collision"},
            {"page": 1, "rect": pymupdf.Rect(40, 10, 180, 20), "text": "next page"},
        ]

        expected = None
        for index, first in enumerate(records):
            for second in records[index + 1:]:
                if second["page"] != first["page"]:
                    if second["page"] > first["page"]:
                        break
                    continue
                intersection = first["rect"] & second["rect"]
                if not intersection.is_empty and intersection.get_area() > 1.0:
                    expected = (first, second)
                    break
            if expected:
                break

        actual = _first_body_text_overlap(records)
        self.assertIsNotNone(actual)
        self.assertIs(actual[0], expected[0])
        self.assertIs(actual[1], expected[1])

    def test_pdf_body_overlap_sweep_skips_disjoint_vertical_blocks(self):
        import pymupdf
        from clouzer_file_creation import _first_body_text_overlap

        records = [
            {"page": 0, "rect": pymupdf.Rect(40, y, 180, y + 8), "text": str(y)}
            for y in range(10, 1010, 10)
        ]
        self.assertIsNone(_first_body_text_overlap(records))


    def test_docx_recovers_explicit_user_table_when_model_html_omits_it(self):
        prompt = """Buat file `TES_DOCX_CLOUZER_ULANG.docx` dengan ketentuan:

1. Judul: LAPORAN PENGUJIAN CLOUZER.
2. Buat 3 heading Word: Identitas, Data Pengujian, Kesimpulan.
3. Buat paragraf singkat pada setiap bagian.
4. Buat tabel Word asli dengan kolom No, Nama, Umur, Jabatan, Catatan dan data:
   - 1 | Andi | 25 | Admin | Normal
   - 2 | Budi | 30 | Manager | =1+1
   - 3 | Citra | 27 | Staff | +SUM(1,1)
5. Buat daftar bernomor otomatis sebanyak 3 poin.
6. Judul hanya boleh muncul satu kali.
7. Jangan gunakan HTML atau tabel Markdown sebagai pengganti struktur Word.
8. Hasil wajib DOCX asli dan dikirim sebagai attachment Discord yang bisa diunduh.

Jangan mengklaim berhasil jika file gagal dibuat atau dikirim."""
        # Reproduce the failure mode: model emits HTML structure and prose,
        # but omits the explicit data table supplied in the original prompt.
        model_content = (
            "<h1>LAPORAN PENGUJIAN CLOUZER</h1>"
            "<h2>Identitas</h2><p>Dokumen ini mencatat identitas pengujian.</p>"
            "<h2>Data Pengujian</h2><p>Data pegawai disajikan pada tabel.</p>"
            "<h2>Kesimpulan</h2><p>Pengujian memerlukan validasi struktur.</p>"
            "<ol><li>Periksa struktur.</li><li>Periksa isi.</li><li>Periksa hasil.</li></ol>"
        )
        data, filename, mime = create_document_file("docx", prompt, model_content)
        from docx import Document
        document = Document(io.BytesIO(data))
        self.assertEqual(filename, "TES_DOCX_CLOUZER_ULANG.docx")
        self.assertEqual(mime, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(len(document.tables), 1)
        table = document.tables[0]
        self.assertEqual((len(table.rows), len(table.columns)), (4, 5))
        values = [[cell.text for cell in row.cells] for row in table.rows]
        self.assertEqual(values[0], ["No", "Nama", "Umur", "Jabatan", "Catatan"])
        self.assertEqual(values[1], ["1", "Andi", "25", "Admin", "Normal"])
        self.assertEqual(values[2], ["2", "Budi", "30", "Manager", "=1+1"])
        self.assertEqual(values[3], ["3", "Citra", "27", "Staff", "+SUM(1,1)"])
        self.assertEqual(sum(p.style.name.startswith("Heading") for p in document.paragraphs), 4)  # title plus three section headings
        self.assertEqual(sum(p.style.name.casefold().startswith("list number") for p in document.paragraphs), 3)
        all_text = "\\n".join(p.text for p in document.paragraphs)
        self.assertEqual(all_text.count("LAPORAN PENGUJIAN CLOUZER"), 1)
        summary = validate_document_file("docx", prompt, data)
        self.assertEqual(summary["tables"], 1)
        self.assertEqual(summary["headings"], 4)  # title plus three section headings
        self.assertEqual(summary["numbered_lists"], 3)

    def test_docx_removes_only_complete_tab_delimited_copy_of_native_table(self):
        from docx import Document

        prompt = (
            "Buat DOCX dengan heading Word, tabel Word asli, dan daftar bernomor. "
            "Judul hanya satu kali."
        )
        content = """<h1>LAPORAN PENGUJIAN CLOUZER</h1>
<h2>Identitas</h2><p>Nama proyek: CLOUZER.</p>
<h2>Data Pengujian</h2><p>Data pegawai disajikan pada tabel.</p>
<table>
<thead><tr><th>No</th><th>Nama</th><th>Umur</th><th>Jabatan</th><th>Catatan</th></tr></thead>
<tbody>
<tr><td>1</td><td>Andi</td><td>25</td><td>Admin</td><td>Normal</td></tr>
<tr><td>2</td><td>Budi</td><td>30</td><td>Manager</td><td>=1+1</td></tr>
<tr><td>3</td><td>Citra</td><td>27</td><td>Staff</td><td>+SUM(1,1)</td></tr>
</tbody></table>
<ol><li>Periksa struktur.</li><li>Periksa isi.</li><li>Periksa hasil.</li></ol>
<p>No	Nama	Umur	Jabatan	Catatan</p>
<p>1	Andi	25	Admin	Normal</p>
<p>2	Budi	30	Manager	=1+1</p>
<p>3	Citra	27	Staff	+SUM(1,1)</p>
<p>Catatan	khusus	ini bukan salinan tabel.</p>
<h2>Kesimpulan</h2><p>Dokumen selesai diuji.</p>"""

        data, _, _ = create_document_file("docx", prompt, content)
        document = Document(io.BytesIO(data))

        self.assertEqual(len(document.tables), 1)
        table = document.tables[0]
        expected_rows = [
            ["No", "Nama", "Umur", "Jabatan", "Catatan"],
            ["1", "Andi", "25", "Admin", "Normal"],
            ["2", "Budi", "30", "Manager", "=1+1"],
            ["3", "Citra", "27", "Staff", "+SUM(1,1)"],
        ]
        self.assertEqual(
            [[cell.text for cell in row.cells] for row in table.rows],
            expected_rows,
        )
        paragraph_text = [paragraph.text for paragraph in document.paragraphs]
        duplicate_rows = {"\t".join(row) for row in expected_rows}
        self.assertTrue(duplicate_rows.isdisjoint(paragraph_text))
        self.assertIn("Catatan\tkhusus\tini bukan salinan tabel.", paragraph_text)
        self.assertEqual(sum(p.style.name.casefold().startswith("list number") for p in document.paragraphs), 3)
        self.assertEqual(sum(p.style.name.startswith("Heading") for p in document.paragraphs), 4)
        summary = validate_document_file("docx", prompt, data)
        self.assertEqual(summary["tables"], 1)
        self.assertEqual(summary["numbered_lists"], 3)


    def test_uploaded_docx_artifact_structure_matches_user_acceptance_spec(self):
        from pathlib import Path
        from docx import Document

        artifact = Path("audit-recovery/review-round-2/incoming/TES_DOCX_CLOUZER_ULANG (1).docx")
        self.assertTrue(artifact.is_file(), f"Uploaded audit artifact is missing: {artifact}")
        data = artifact.read_bytes()
        self.assertGreater(len(data), 0)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip(), "DOCX ZIP package has a corrupt member")
            xml_parts = [name for name in archive.namelist() if name.endswith(".xml")]
            self.assertTrue(xml_parts)
            for name in xml_parts:
                import xml.etree.ElementTree as ET
                ET.fromstring(archive.read(name))

        document = Document(io.BytesIO(data))
        self.assertEqual(len(document.tables), 1)
        expected_rows = [
            ["No", "Nama", "Umur", "Jabatan", "Catatan"],
            ["1", "Andi", "25", "Admin", "Lulus"],
            ["2", "Budi", "30", "Developer", "Perlu review"],
            ["3", "Citra", "28", "Tester", "Selesai"],
        ]
        self.assertEqual(
            [[cell.text for cell in row.cells] for row in document.tables[0].rows],
            expected_rows,
        )
        paragraphs = document.paragraphs
        paragraph_text = [paragraph.text for paragraph in paragraphs]
        self.assertEqual(paragraph_text.count("LAPORAN PENGUJIAN CLOUZER"), 1)
        self.assertIn("Hasil Pengujian Sistem", paragraph_text)
        self.assertEqual(
            sum(paragraph.style.name.casefold().startswith("list number") for paragraph in paragraphs),
            3,
        )
        for literal in (
            "=SUM(A1:A10)",
            "=CMD|'/C calc'!A0",
            "Jangan mengubah teks ini menjadi formula.",
            "Pengujian DOCX telah selesai.",
        ):
            self.assertIn(literal, paragraph_text)
        table_rows_as_text = {"\t".join(row) for row in expected_rows}
        self.assertTrue(table_rows_as_text.isdisjoint(paragraph_text))
        summary = validate_document_file(
            "docx",
            "Buat DOCX dengan satu tabel Word asli, tiga daftar bernomor, dan teks literal.",
            data,
        )
        self.assertEqual(summary["tables"], 1)
        self.assertEqual(summary["numbered_lists"], 3)

    def test_docx_adds_missing_section_prose_before_table_when_prompt_requires_it(self):
        from docx import Document
        from docx.oxml.ns import qn
        from docx.text.paragraph import Paragraph

        prompt = """Buat file DOCX dengan tiga bagian Identitas, Data Pengujian, dan Kesimpulan.
Buat paragraf singkat pada setiap bagian.
Buat tabel Word asli dengan kolom No, Nama dan data:
- 1 | Andi
- 2 | Budi"""
        model_content = """<h1>LAPORAN PENGUJIAN CLOUZER</h1>
<h2>Identitas</h2><p>Dokumen ini mencatat identitas pengujian.</p>
<h2>Data Pengujian</h2>
<table><tr><th>No</th><th>Nama</th></tr><tr><td>1</td><td>Andi</td></tr><tr><td>2</td><td>Budi</td></tr></table>
<h2>Kesimpulan</h2><p>Dokumen ini menutup uraian pengujian.</p>"""

        data, _, _ = create_document_file("docx", prompt, model_content)
        document = Document(io.BytesIO(data))
        self.assertEqual(len(document.tables), 1)
        self.assertEqual(
            [[cell.text for cell in row.cells] for row in document.tables[0].rows],
            [["No", "Nama"], ["1", "Andi"], ["2", "Budi"]],
        )

        body_children = list(document._element.body.iterchildren())
        for heading in ("Identitas", "Data Pengujian", "Kesimpulan"):
            heading_index = next(
                index for index, element in enumerate(body_children)
                if element.tag == qn("w:p")
                and Paragraph(element, document).text.strip() == heading
            )
            next_heading_index = next(
                (
                    index for index in range(heading_index + 1, len(body_children))
                    if body_children[index].tag == qn("w:p")
                    and Paragraph(body_children[index], document).style.name.startswith("Heading")
                ),
                len(body_children),
            )
            section_elements = body_children[heading_index + 1:next_heading_index]
            first_table_index = next(
                (index for index, element in enumerate(section_elements) if element.tag == qn("w:tbl")),
                len(section_elements),
            )
            prose = [
                Paragraph(element, document)
                for element in section_elements[:first_table_index]
                if element.tag == qn("w:p")
                and Paragraph(element, document).text.strip()
                and not Paragraph(element, document).style.name.startswith(("Heading", "List Number", "List Bullet"))
            ]
            self.assertTrue(prose, f"Section '{heading}' has no prose paragraph before its table.")
        data_heading_index = next(
            index for index, element in enumerate(body_children)
            if element.tag == qn("w:p")
            and Paragraph(element, document).text.strip() == "Data Pengujian"
        )
        table_index = next(
            index for index, element in enumerate(body_children)
            if element.tag == qn("w:tbl")
        )
        inserted = Paragraph(body_children[data_heading_index + 1], document)
        self.assertEqual(
            inserted.text,
            "Bagian ini menjelaskan data yang digunakan dalam pengujian; rincian setiap entri disajikan pada tabel berikut.",
        )
        self.assertEqual(body_children[data_heading_index + 1].tag, qn("w:p"))
        self.assertLess(data_heading_index + 1, table_index)

    def test_docx_generator_matches_uploaded_user_acceptance_spec(self):
        from docx import Document

        prompt = """Buat file TES_DOCX_CLOUZER_ULANG.docx.
Judul: LAPORAN PENGUJIAN CLOUZER.
Subtitle: Hasil Pengujian Sistem.
Buat tabel Word asli dengan kolom No, Nama, Umur, Jabatan, Catatan dan data:
- 1 | Andi | 25 | Admin | Lulus
- 2 | Budi | 30 | Developer | Perlu review
- 3 | Citra | 28 | Tester | Selesai
Buat daftar bernomor otomatis:
1. Pengujian struktur dokumen.
2. Pengujian tabel dan isi sel.
3. Pengujian format dan konsistensi.
Pertahankan teks literal berikut persis:
=SUM(A1:A10)
=CMD|'/C calc'!A0
Jangan mengubah teks ini menjadi formula.
Paragraf penutup: Pengujian DOCX telah selesai.
Jangan menggandakan tabel sebagai teks biasa."""
        model_content = """<h1>LAPORAN PENGUJIAN CLOUZER</h1>
<h2>Hasil Pengujian Sistem</h2>
<ol>
<li>Pengujian struktur dokumen.</li>
<li>Pengujian tabel dan isi sel.</li>
<li>Pengujian format dan konsistensi.</li>
</ol>
<p>=SUM(A1:A10)</p>
<p>=CMD|'/C calc'!A0</p>
<p>Jangan mengubah teks ini menjadi formula.</p>
<p>Pengujian DOCX telah selesai.</p>"""
        data, filename, mime = create_document_file("docx", prompt, model_content)
        document = Document(io.BytesIO(data))
        self.assertEqual(filename, "TES_DOCX_CLOUZER_ULANG.docx")
        self.assertEqual(mime, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(len(document.tables), 1)
        expected_rows = [
            ["No", "Nama", "Umur", "Jabatan", "Catatan"],
            ["1", "Andi", "25", "Admin", "Lulus"],
            ["2", "Budi", "30", "Developer", "Perlu review"],
            ["3", "Citra", "28", "Tester", "Selesai"],
        ]
        self.assertEqual(
            [[cell.text for cell in row.cells] for row in document.tables[0].rows],
            expected_rows,
        )
        paragraphs = document.paragraphs
        paragraph_text = [paragraph.text for paragraph in paragraphs]
        self.assertEqual(paragraph_text.count("LAPORAN PENGUJIAN CLOUZER"), 1)
        self.assertIn("Hasil Pengujian Sistem", paragraph_text)
        self.assertEqual(
            sum(paragraph.style.name.casefold().startswith("list number") for paragraph in paragraphs),
            3,
        )
        for literal in (
            "=SUM(A1:A10)",
            "=CMD|'/C calc'!A0",
            "Jangan mengubah teks ini menjadi formula.",
            "Pengujian DOCX telah selesai.",
        ):
            self.assertIn(literal, paragraph_text)
        table_rows_as_text = {"\t".join(row) for row in expected_rows}
        self.assertTrue(table_rows_as_text.isdisjoint(paragraph_text))
        summary = validate_document_file("docx", prompt, data)
        self.assertEqual(summary["tables"], 1)
        self.assertEqual(summary["numbered_lists"], 3)


    def test_pptx_visual_embedding_preserves_editable_text_and_adds_picture_relationships(self):
        from pptx import Presentation
        from PIL import Image
        from clouzer_file_creation import embed_pptx_visuals, extract_pptx_visual_briefs

        base = create_document_file(
            "pptx",
            "Buat presentasi PPTX 2 slide.",
            "Pengenalan CLOUZER\nIsi slide pertama.\n--- SLIDE ---\nCara Kerja\nIsi slide kedua.",
        )[0]
        briefs = extract_pptx_visual_briefs(base)
        self.assertEqual([item["title"] for item in briefs], ["Pengenalan CLOUZER", "Cara Kerja"])
        visual = io.BytesIO()
        Image.new("RGB", (800, 600), "navy").save(visual, format="PNG")
        result = embed_pptx_visuals(base, [visual.getvalue(), visual.getvalue()])
        prs = Presentation(io.BytesIO(result))
        self.assertEqual(len(prs.slides), 2)
        for slide, expected in zip(prs.slides, ("Pengenalan CLOUZER", "Cara Kerja")):
            texts = [shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False)]
            self.assertTrue(any(expected in value for value in texts))
            self.assertTrue(any("Isi slide" in value for value in texts))
            self.assertEqual(sum(shape.shape_type == 13 for shape in slide.shapes), 1)
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue(any(name.startswith("ppt/media/") for name in archive.namelist()))


    def test_pptx_cleans_markdown_and_removes_visual_direction_lines(self):
        from pptx import Presentation
        from clouzer_file_creation import create_document_file

        prompt = "Buat presentasi PowerPoint 2 slide."
        content = """### Mengenal AI
- **AI membantu analisis data**
- *Contoh: rekomendasi konten*
Arahan visual: Buat ilustrasi otak digital dengan cahaya biru.
--- SLIDE ---
## Risiko AI
1. **Bias pada data**
2. Privasi dan keamanan
Deskripsi visual: Gambar robot dengan teks AI besar."""
        data, _, _ = create_document_file("pptx", prompt, content)
        presentation = Presentation(io.BytesIO(data))
        self.assertEqual(len(presentation.slides), 2)
        all_text = "\n".join(
            shape.text
            for slide in presentation.slides
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
        )
        self.assertIn("Mengenal AI", all_text)
        self.assertIn("AI membantu analisis data", all_text)
        self.assertIn("Risiko AI", all_text)
        self.assertIn("Bias pada data", all_text)
        for forbidden in ("###", "**", "Arahan visual:", "Deskripsi visual:", "otak digital", "robot dengan teks"):
            self.assertNotIn(forbidden, all_text)

    def test_pptx_generated_backgrounds_are_full_bleed_and_behind_editable_text(self):
        from pptx import Presentation
        from PIL import Image
        from clouzer_file_creation import create_document_file, embed_pptx_visuals

        prompt = "Buat presentasi PowerPoint 2 slide."
        base = create_document_file(
            "pptx", prompt,
            "Pengenalan CLOUZER\nKonten editable slide pertama.\n--- SLIDE ---\nCara Kerja\nKonten editable slide kedua.",
        )[0]
        backgrounds = []
        for color in ("#102040", "#17254a"):
            image = Image.new("RGB", (1024, 576), color)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            backgrounds.append(buffer.getvalue())
        result = embed_pptx_visuals(base, backgrounds)
        presentation = Presentation(io.BytesIO(result))
        self.assertEqual(len(presentation.slides), 2)
        for slide, expected in zip(presentation.slides, ("Pengenalan CLOUZER", "Cara Kerja")):
            pictures = [shape for shape in slide.shapes if shape.shape_type == 13]
            self.assertEqual(len(pictures), 1)
            background = pictures[0]
            self.assertEqual((background.left, background.top), (0, 0))
            self.assertEqual((background.width, background.height), (presentation.slide_width, presentation.slide_height))
            text_shapes = [shape for shape in slide.shapes if getattr(shape, "has_text_frame", False) and shape.text.strip()]
            self.assertTrue(any(expected in shape.text for shape in text_shapes))
            self.assertLess(list(slide.shapes).index(background), min(list(slide.shapes).index(shape) for shape in text_shapes))

    def test_pptx_generation_instruction_forbids_markdown_and_visual_prompts(self):
        from clouzer_file_creation import build_file_generation_system_instruction

        instruction = build_file_generation_system_instruction("pptx")
        self.assertIn("JANGAN gunakan Markdown", instruction)
        self.assertIn("arahan visual", instruction)
        self.assertIn("background", instruction)


if __name__ == "__main__":
    unittest.main()

def test_pptx_request_phrase_routes_to_pptx_file_generation(self):
        prompt = "Buatkan presentasi PowerPoint PPTX 5 slide tentang keamanan AI."
        self.assertEqual(detect_requested_file_format(prompt), "pptx")

    def test_pptx_visual_failure_guard_precedes_generic_file_retry(self):
        candidate_path = Path(__file__).with_name("revisi_audit_final_main.py")
        source = candidate_path.read_text(encoding="utf-8")
        handler_start = source.index("except (ValueError, RuntimeError) as build_exc:")
        pptx_guard = source.index('if requested_file_format == "pptx":', handler_start)
        failed_closed_log = source.index(
            '"[PPTX-VISUAL] scope=%s stage=failed_closed reason=%s"', pptx_guard
        )
        fail_closed_raise = source.index("\n                                raise\n", failed_closed_log)
        generic_retry_log = source.index(
            '"[FILE-RETRY] scope=%s format=%s reason=%s action=retry_once"', handler_start
        )
        self.assertLess(pptx_guard, failed_closed_log)
        self.assertLess(failed_closed_log, fail_closed_raise)
        self.assertLess(fail_closed_raise, generic_retry_log)
