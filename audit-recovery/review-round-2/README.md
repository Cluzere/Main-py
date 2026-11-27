# Audit Recovery — Review Round 2

## Kandidat aktif

- Branch: `audit-recovery-gpt-2026-11-23`
- File kandidat yang sedang diaudit: `revisi_audit_final_main.py`
- File sumber asli repo `main (4).py` tidak diubah (blob sama dengan branch `main`); file produksi Wispbyte juga tidak disentuh.
- `test_audit_regressions.py`: 74 regression tests yang sudah ada.
- `tests/test_audit_candidate_archives.py`: tes tambahan khusus parser arsip, antrean lampiran, retry stream, dan alur pesan Discord yang dimock.
- `FINAL_AUDIT_REPORT.md`: status gerbang CI dan pekerjaan staging yang belum dapat dibuktikan lewat CI.

## Perubahan putaran audit

- Batas pesan lampiran dinaikkan menjadi 10 dengan laporan per file.
- Pembacaan ZIP dan 7Z dibatasi ukuran, jumlah anggota, rasio kompresi, kedalaman arsip, dan jumlah visual; ekstraksi dilakukan di memori tanpa menulis path arsip ke disk.
- DOCX/PDF/PPTX/XLSX di dalam arsip diparse menggunakan parser dokumen yang sudah tersedia.
- Signature anggota PDF/Office/audio yang dikenal diperiksa sebelum parser dokumen dijalankan; mismatch ditolak dan dilaporkan.
- DOCX header/footer dan PPTX speaker notes/chart data ikut diekstrak.
- Teks gabungan lampiran dibatasi agar tidak diam-diam membanjiri konteks model.
- Dokumen dari pesan yang di-reply dibaca bersama lampiran baru dengan sisa anggaran ukuran/teks.
- Antrean semaphore lampiran menunggu giliran, bukan langsung meminta pengguna mengunggah ulang.
- Retry Responses maksimal satu kali untuk HTTP 502/503/504, atau HTTP 500 dengan pesan `Unable to forward upstream stream`, hanya sebelum event stream pertama.
- Antrean AI FIFO per user/channel dibatasi 10 waiter, timeout default 300 detik, dan memberi status antrean penuh/timeout/dibatalkan; queue tidak persisten setelah hard restart.

## Dependensi tambahan

Parser 7Z memerlukan `py7zr`, dan parser XML aman memerlukan `defusedxml`. Untuk staging Wispbyte, pasang:

```bash
pip install --no-cache-dir "py7zr>=1.0,<2" defusedxml
```

Lihat `requirements-audit.txt` untuk daftar dependensi audit. Jika `py7zr` tidak terpasang, file 7Z ditandai unsupported dan tidak diklaim sudah dibaca.

## Gerbang verifikasi

CI terbaru (run #45514190007) memeriksa Python 3.12, kompilasi, Bandit medium/high severity, 57 tes kandidat, pip check, pip-audit, dan 74 tes regresi yang sudah ada: total 130 tes lulus. CI bukan pengganti tes staging Discord/Wispbyte.

Jangan mengganti file produksi sebelum checklist staging di `FINAL_AUDIT_REPORT.md` selesai.