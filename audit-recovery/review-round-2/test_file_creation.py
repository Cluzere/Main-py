import io
import unittest
import zipfile

from clouzer_file_creation import create_document_file, detect_requested_file_format


class FileCreationTests(unittest.TestCase):
    def test_detect_supported_formats(self):
        samples = {
            "Buatkan catatan TXT tentang keamanan": "txt",
            "Tolong buat laporan PDF": "pdf",
            "Bikinkan surat DOCX": "docx",
            "Buat tabel anggaran Excel": "xlsx",
            "Buat presentasi PowerPoint": "pptx",
            "Buat arsip ZIP dari catatan ini": "zip",
        }
        for prompt, expected in samples.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(detect_requested_file_format(prompt), expected)

    def test_capability_question_is_not_a_create_command(self):
        self.assertIsNone(detect_requested_file_format("CLOUZER sudah bisa membuat file PDF belum?"))

    def test_all_formats_create_nonempty_files(self):
        content = "# Contoh Dokumen\n\nIsi pertama.\n\nNama\tNilai\nA\t10\nB\t20"
        for fmt in ("txt", "pdf", "docx", "xlsx", "pptx", "zip"):
            with self.subTest(format=fmt):
                data, filename, mime = create_document_file(fmt, "Buatkan dokumen contoh", content)
                self.assertTrue(data)
                self.assertTrue(filename.endswith("." + fmt))
                self.assertTrue(mime)
                if fmt == "txt":
                    self.assertIn("Isi pertama".encode(), data)
                elif fmt == "pdf":
                    self.assertTrue(data.startswith(b"%PDF-"))
                else:
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        names = archive.namelist()
                        if fmt == "zip":
                            self.assertIn("konten.txt", names)
                        else:
                            self.assertTrue(names)


if __name__ == "__main__":
    unittest.main()
