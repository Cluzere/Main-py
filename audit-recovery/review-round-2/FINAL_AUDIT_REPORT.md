# Audit Final — Candidate CLOUZER

**Status saat ini: CI PASS, belum boleh disebut siap produksi.**  
Seluruh perubahan audit dilakukan pada kandidat branch. File produksi `main.py` tidak diubah.

## Identitas kandidat

- Repository: `Cluzere/Main-py`
- Branch: `audit-recovery-gpt-2026-11-23`
- File: `audit-recovery/review-round-2/revisi_audit_final_main.py`
- Commit branch saat laporan ini dibuat: `61a26fb8fb0ff76b1da553ad0f4e36bb123ddecc`
- Blob kandidat: `87936de8ffe6454e0c8fe5704d227a149965cb85`
- Jumlah baris: 5.520
- CI terakhir: [Run #45511576862 — SUCCESS](https://github.com/Cluzere/Main-py/actions/runs/45511576862)

## Bukti CI terakhir

- CPython 3.12: `py_compile` PASS.
- Bandit high-severity scan: PASS, tidak ada temuan high-severity.
- Tes kandidat tambahan: **32/32 PASS**.
- Regresi audit yang sudah ada: **74/74 PASS**.
- Total dua suite yang dijalankan pada run terakhir: **106 tes PASS**.
- Dependensi CI mengikuti versi hosting yang diketahui, termasuk `PyMuPDF<2` dan `py7zr>=1.0,<2`.

Ini tes unit/regresi. Bukan live test Discord Gateway, CDN, Xingchi, Cloudflare, Pollinations, atau Wispbyte.

## Perubahan nyata pada kandidat

1. Maksimal 10 lampiran per pesan; setiap file punya manifest nama, ekstensi, MIME, ukuran, dan status.
2. Batas total lampiran default 100 MB; batas per ekstensi tetap diberlakukan.
3. Satu file gagal tidak menghentikan pemrosesan file lain.
4. Semaphore lampiran menunggu giliran FIFO, bukan timeout lalu meminta pengguna mengunggah ulang.
5. ZIP dan 7Z dibaca tanpa mengekstrak path arsip ke filesystem. Ada batas jumlah anggota, ukuran per anggota, total hasil ekstraksi, rasio kompresi, dan kedalaman arsip.
6. Isi ZIP/7Z mendukung teks/kode, gambar, PDF, DOCX, PPTX, dan XLSX yang dikenali parser. ZIP bersarang hanya satu tingkat; 7Z bersarang tidak diperluas.
7. 7Z memerlukan dependency `py7zr`. Tanpanya, file diberi status unsupported dan tidak diklaim telah dibaca.
8. Dokumen dari pesan yang di-reply ikut dibaca bersama lampiran baru, dengan sisa anggaran ukuran dan teks yang sama.
9. Jika pesan sumber reply tidak bisa diambil, bot menjelaskan bahwa lampiran aslinya tidak tersedia.
10. Konteks gabungan lampiran dibatasi default 100.000 karakter. Manifest menandai konten yang tidak seluruhnya masuk konteks AI.
11. Batas teks parser dokumen dinaikkan ke default 50.000 karakter per dokumen.
12. DOCX membaca header/footer; PPTX membaca speaker notes dan judul/data seri grafik; XLSX membaca sheet serta metadata judul/range seri grafik.
13. Retry Responses hanya satu kali untuk HTTP 502/503/504 sebelum event stream pertama. Tidak ada retry otomatis setelah stream mulai.
14. Jika stream terputus setelah sebagian teks diterima, teks parsial dipertahankan dan user request tidak dibuang dari history.
15. Antrean percakapan menggunakan lock FIFO per scope user/channel. Cancellation membersihkan referensi lock; scope antar-user terpisah.

## Audit 64 butir — status jujur

Status: **DONE** = perubahan dan tes relevan tersedia; **PARTIAL** = ada mitigasi tetapi cakupan/validasi belum lengkap; **NOT DONE** = belum diterapkan.

### A. Attachment dan pembacaan file

| No. | Status | Catatan |
|---:|---|---|
| 1 | DONE | Default 10 attachment per pesan. |
| 2 | DONE | Loop memproses semua file dalam batas 10. |
| 3 | PARTIAL | Total default 100 MB dan laporan skip; belum live diuji dengan 10 file besar. |
| 4 | DONE | Konkurensi attachment default 3. |
| 5 | DONE | Semaphore menunggu giliran, tidak membuang file karena timeout. |
| 6 | DONE | Manifest nama, ekstensi, MIME, ukuran. |
| 7 | PARTIAL | Label ekstensi/MIME; signature check hanya untuk format tertentu. |
| 8 | DONE | Status per file dicatat. |
| 9 | PARTIAL | Alasan skip/error utama dicatat; beberapa parser hanya memberi kategori error, belum diagnosis rinci. |
| 10 | DONE | Kegagalan satu file diisolasi; ada tes batch 10 file dengan satu kegagalan. |

### B. ZIP dan 7Z

| No. | Status | Catatan |
|---:|---|---|
| 11 | DONE | ZIP umum dibaca, bukan hanya Office ZIP. |
| 12 | DONE | Teks, gambar, PDF, DOCX, PPTX, XLSX dibaca dari arsip. |
| 13 | PARTIAL | Format anggota dikenali terutama dari ekstensi; validasi signature belum menyeluruh per anggota. |
| 14 | DONE | Parser 7Z memakai `py7zr`; dependency wajib untuk deployment. |
| 15 | PARTIAL | ZIP bersarang satu tingkat; 7Z bersarang dilewati. |
| 16 | PARTIAL | Batas ukuran/rasio/entry, encrypted ZIP dan password-protected 7Z ditolak; belum fuzz-test menyeluruh. |
| 17 | DONE | Arsip yang tidak diparse tidak diklaim sudah dibaca. |

### C. Reply dan konteks percakapan

| No. | Status | Catatan |
|---:|---|---|
| 18 | DONE | Dokumen dari pesan reply ikut diproses; ada tes resolver dan batas anggaran. |
| 19 | PARTIAL | Resolver gambar lama tetap ada; belum ada tes live baru untuk semua kondisi reply-image. |
| 20 | PARTIAL | Konteks gambar/edit sebelumnya dipertahankan oleh alur yang ada; belum ada regresi penuh untuk semua urutan edit. |
| 21 | PARTIAL | User/channel scope dan dokumen reply dijaga; kombinasi semua metadata dan media belum live diuji. |
| 22 | DONE | Reply dokumen tidak lagi dianggap kosong hanya karena pesan baru tidak punya attachment. |
| 23 | DONE | Kegagalan mengambil pesan sumber reply menghasilkan penjelasan eksplisit. |

### D. Antrean pesan

| No. | Status | Catatan |
|---:|---|---|
| 24 | DONE | Antrean attachment tidak meminta unggah ulang karena semaphore timeout; rate-limit tetap dapat menolak spam. |
| 25 | DONE | Status menunggu dikirim sebelum lock. |
| 26 | PARTIAL | FIFO per user/channel, bukan satu antrean global seluruh server. |
| 27 | DONE | Pesan diproses otomatis saat lock tersedia. |
| 28 | PARTIAL | Alur memproses pesan asal; live delivery/reply Discord belum dibuktikan. |
| 29 | DONE | Scope percakapan dibedakan per user/channel. |
| 30 | PARTIAL | Cancellation lock dibersihkan dan dites; queue tidak persisten setelah restart dan belum ada fitur antrean penuh/cancel user. |
| 31 | PARTIAL | Rate limit user dan global budget tetap terpisah dari antrean; konfigurasi multi-instance tetap tidak didukung. |

### E. Responses API dan streaming

| No. | Status | Catatan |
|---:|---|---|
| 32 | PARTIAL | HTTP gateway 502/503/504 sebelum event pertama ditangani dengan retry terbatas; bukan semua bentuk upstream stream failure. |
| 33 | DONE | Retry maksimal satu kali hanya untuk kegagalan gateway yang aman sebelum stream. |
| 34 | PARTIAL | Tidak replay setelah event stream dimulai; belum ada idempotency key untuk semua side effect provider. |
| 35 | PARTIAL | Kebijakan retry menghindari duplikasi dari retry stream; duplikasi live delivery belum diuji. |
| 36 | DONE | User request tidak dibuang; teks parsial dipertahankan ketika stream terputus setelah delta. |
| 37 | PARTIAL | Teks parsial dipertahankan sebagai jawaban bertanda terputus; stream tidak dilanjutkan dari titik terakhir. |
| 38 | DONE | Kegagalan memberi status error. |
| 39 | PARTIAL | Waktu headers, event pertama, teks pertama, web search, dan durasi dicatat; diagnosis upstream belum lengkap untuk semua provider. |

### F. Kualitas pembacaan dokumen

| No. | Status | Catatan |
|---:|---|---|
| 40 | DONE | Batas gambar vision default 10. |
| 41 | DONE | Batas teks dokumen default 50.000 karakter; gabungan lampiran 100.000 karakter. |
| 42 | PARTIAL | PDF panjang dibatasi dan halaman scan dikirim sebagai visual; tidak ada OCR khusus untuk tulisan tangan/scan buruk. |
| 43 | DONE | Halaman yang dilewati akibat batas dilaporkan oleh parser PDF. |
| 44 | PARTIAL | XLSX sheet/chart metadata dan PPTX chart/slide data dibaca; tidak semua jenis chart/cache/format kompleks tercakup. |
| 45 | PARTIAL | DOCX header/footer, PPTX speaker notes, dan media tertanam tertentu; bukan seluruh metadata/header/footer/embedded object. |
| 46 | DONE | Pemotongan teks diberi keterangan. |
| 47 | DONE | Ada batas gabungan dan manifest file yang terdampak truncation. |
| 48 | DONE | Legacy DOC/XLS/PPT tidak dijanjikan sebagai format yang terbaca; format yang tak didukung harus ditandai. |

### G. Keamanan dan stabilitas

| No. | Status | Catatan |
|---:|---|---|
| 49 | PARTIAL | Signature PDF/ZIP/7Z/RTF diperiksa; image divalidasi parser; belum signature-check menyeluruh untuk semua tipe/audio. |
| 50 | PARTIAL | Ukuran, rasio, entry, gambar, teks, dan buffer dibatasi; bukan sandbox CPU/RAM tingkat OS. |
| 51 | PARTIAL | Parser berat utama dijalankan via thread; belum profiling seluruh jalur parser di hosting. |
| 52 | PARTIAL | Corrupt/empty/oversize/mismatch punya guard dan beberapa tes; belum semua format/fuzz corpus. |
| 53 | PARTIAL | Arsip tidak diekstrak ke disk; pembersihan semua state/resource tetap perlu uji restart/shutdown. |
| 54 | PARTIAL | Konten eksternal diperlakukan sebagai data tidak tepercaya di prompt; ini defense-in-depth, bukan sandbox. |

### H. Tes dan verifikasi

| No. | Status | Catatan |
|---:|---|---|
| 55 | PARTIAL | 10 file campuran diuji melalui mocked Discord event; belum 10 upload sungguhan melalui Discord CDN. |
| 56 | PARTIAL | ZIP berisi beberapa format dites; belum semua kombinasi format/korupsi. |
| 57 | PARTIAL | 7Z teks/DOCX dan compression-ratio bomb dites; encrypted/arsip dunia nyata belum live diuji. |
| 58 | PARTIAL | Reply dokumen dites; reply gambar perlu staging tambahan. |
| 59 | PARTIAL | Tes image generation/edit sebelumnya sudah dikonfirmasi pengguna; belum dire-run pada kandidat terbaru. |
| 60 | PARTIAL | Scope user berbeda dan cancellation lock dites; belum tes dua user aktif bersamaan di Discord. |
| 61 | PARTIAL | SSE partial disconnect dan retry policy dites; belum inject error pada upstream live. |
| 62 | PARTIAL | Corrupt ZIP, signature mismatch, rasio 7Z, dan batas file dites; belum seluruh format. |
| 63 | PARTIAL | History user/partial response dipertahankan dan retry dibatasi; belum pembuktian no-loss/no-duplicate live. |
| 64 | PARTIAL | CI dan regresi lulus; staging dan promosi produksi belum dilakukan. |

## Gerbang yang masih wajib sebelum produksi

1. **Wispbyte staging:** pasang `py7zr>=1.0,<2`, jalankan kandidat dengan file data staging, jangan timpa `main.py`.
2. **Discord real uploads:** unggah 10 file campuran nyata, termasuk ZIP/7Z, PDF scan, XLSX/PPTX, gambar, dan satu file corrupt/unsupported.
3. **Reply real:** reply ke dokumen dan gambar; ulangi dengan lampiran baru pada pesan yang sama.
4. **Multi-user:** dua atau lebih user mengirim pesan hampir bersamaan; pastikan history, status, dan jawaban tidak tertukar.
5. **Stream fault injection:** putuskan koneksi setelah delta pertama dan sebelum response.completed; pastikan teks parsial terlihat dan tidak ada request/tool duplicate.
6. **Restart/shutdown:** uji `memory.json`, `view_state.json`, `global_usage.json`, persistent buttons, dan antrean yang sedang menunggu.
7. **Provider behavior:** verifikasi Xingchi, Cloudflare, Discord CDN, dan fallback image dari jaringan Wispbyte.
8. **Cost control:** kuota request global bukan billing hard cap; pasang alert/cap di provider jika tersedia.
9. **Single process:** global budget menggunakan lock proses lokal. Jangan jalankan beberapa replica dengan file budget yang sama tanpa koordinasi lintas proses.

## Keputusan

Kandidat saat ini telah melewati compile, Bandit high-severity, 32 tes khusus kandidat, dan 74 tes regresi lama. Itu bukti kuat untuk gerbang otomatis, **bukan bukti bahwa 64 butir semuanya sudah selesai atau bot siap produksi**.

Jangan menimpa `main.py` sampai semua tes staging di atas selesai.