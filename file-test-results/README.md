# CLOUZER File Format Test Results

Folder terpisah untuk mengumpulkan artefak pengujian format file CLOUZER.

## Aturan keselamatan
- Folder ini berada di branch `audit-recovery-gpt-2026-11-23`.
- Tidak mengubah file produksi `main.py`.
- Tidak mengubah kode kandidat atau konfigurasi bot.
- File hasil pengujian disimpan apa adanya; jangan mengedit artefak asli.
- Status "berhasil" dari laporan pengguna belum dianggap lulus independen sampai file asli diperiksa.

## Status awal per 2026-11-25

| Format | Status sementara | Catatan |
|---|---|---|
| TXT | Menurut laporan pengguna berhasil; belum diverifikasi | Menunggu file asli |
| DOCX | Menurut laporan pengguna berhasil; belum diverifikasi | Menunggu file asli |
| PDF | Gagal menurut pemeriksaan pengguna | Output sebelumnya tidak membuktikan PDF valid |
| CSV | Gagal | CLOUZER menghasilkan gambar, bukan file CSV |
| XLSX | Menurut laporan pengguna berhasil; belum diverifikasi | Menunggu file asli |
| PPTX | Menurut laporan pengguna berhasil; belum diverifikasi | Menunggu file asli |
| JSON | Gagal | File tidak dibuat atau dikirim |
| ZIP | Menurut laporan pengguna berhasil; belum diverifikasi | Menunggu file asli |

## Struktur artefak

Simpan file asli pada subfolder berdasarkan format:
- `artifacts/txt/`
- `artifacts/docx/`
- `artifacts/pdf/`
- `artifacts/csv/`
- `artifacts/xlsx/`
- `artifacts/pptx/`
- `artifacts/json/`
- `artifacts/zip/`

## Standar verifikasi

Setiap artefak diperiksa satu per satu:
1. File benar-benar ada dan ekstensi sesuai.
2. Struktur internal format valid (bila format terkontainer).
3. Isi memenuhi permintaan pengujian.
4. File dapat dibuka/dibaca tanpa kerusakan.
5. Hasil dicatat sebagai PASS, FAIL, atau PARTIAL dengan bukti.

Belum ada artefak biner yang diunggah atau diverifikasi pada saat README ini dibuat.
