# Audit Final — Candidate CLOUZER

**Status saat ini: CI PASS, belum boleh disebut siap produksi.**  
Seluruh perubahan audit dilakukan pada kandidat branch. File sumber asli repo `main (4).py` tetap identik dengan branch `main`; file produksi Wispbyte tidak disentuh.

## Identitas kandidat

- Repository: `Cluzere/Main-py`
- Branch: `audit-recovery-gpt-2026-11-23`
- File: `audit-recovery/review-round-2/revisi_audit_final_main.py`
- Commit terakhir yang mengubah file kandidat: `6a92f1692128a2a330aad37f5d77b571e36c99c9`
- Commit branch terbaru yang diuji pada CI: `886af9936e9c601407bd59dd300c5dad51db6255`.
- File sumber asli di repo (`main (4).py`) identik dengan `main` branch: SHA `ffffd0c17169b29504f4a8713dca396cf61ddeff`.
- Blob kandidat terbaru: `da46080de4a8b9f3921bc44ead480ae2d819fa53`
- Jumlah baris kandidat: 5.934
- CI terbaru: [Run #45565802138 — SUCCESS](https://github.com/Cluzere/Main-py/actions/runs/45565802138)

## Bukti CI terakhir

- CPython 3.12: `py_compile` PASS.
- Bandit medium/high severity scan (`-ll`): PASS, tidak ada temuan medium/high.
- `defusedxml` dipakai untuk XML tidak tepercaya; DOCTYPE eksternal, entity eksternal, nested entity, nilai entity terlalu panjang, dan referensi entity berlebihan ditolak.
- `pip check`: PASS.
- `pip-audit --strict`: PASS, tidak ada kerentanan dependency yang diketahui.
- Tes kandidat: **85/85 PASS**.
- Regresi audit yang sudah ada: **76/76 PASS**.
- Total dua suite pada run terbaru: **161 tes PASS** (85 tes kandidat + 76 regresi lama).
- Dependensi CI mengikuti versi hosting yang diketahui, termasuk `PyMuPDF<2`, `py7zr>=1.0,<2`, dan `defusedxml`.

Ini tes unit/regresi. Bukan live test Discord Gateway, CDN, Xingchi, Cloudflare, Pollinations, atau Wispbyte.

## Ringkasan 64 butir

- **32 DONE**: perubahan dan tes relevan tersedia.
- **32 PARTIAL**: mitigasi ada, tetapi masih perlu live staging, fuzzing, atau cakupan tambahan.
- **0 NOT DONE**: tidak ada item yang sama sekali belum disentuh; item PARTIAL tetap belum boleh dianggap selesai.

## Perubahan nyata pada kandidat

1. Maksimal 10 lampiran per pesan; setiap file punya manifest nama, ekstensi, MIME, ukuran, dan status.
2. Batas total lampiran default 100 MB; batas per ekstensi tetap diberlakukan.
3. Satu file gagal tidak menghentikan pemrosesan file lain.
4. Semaphore lampiran menunggu giliran FIFO, bukan timeout lalu meminta pengguna mengunggah ulang.
5. ZIP dan 7Z dibaca tanpa mengekstrak path arsip ke filesystem. Ada batas jumlah anggota, ukuran per anggota, total hasil ekstraksi, rasio kompresi, dan kedalaman arsip.
6. Isi ZIP/7Z mendukung teks/kode, gambar, PDF, DOCX, PPTX, dan XLSX yang dikenali parser. ZIP bersarang hanya satu tingkat; setiap entri kini diberi path penuh dan asal `ROOT`/`NESTED` secara eksplisit. 7Z bersarang tidak diperluas.
7. 7Z memerlukan dependency `py7zr`. Tanpanya, file diberi status unsupported dan tidak diklaim telah dibaca.
8. Dokumen dari pesan yang di-reply ikut dibaca bersama lampiran baru, dengan sisa anggaran ukuran dan teks yang sama.
9. Jika pesan sumber reply tidak bisa diambil, bot menjelaskan bahwa lampiran aslinya tidak tersedia.
10. Konteks gabungan lampiran dibatasi default 100.000 karakter. Manifest menandai konten yang tidak seluruhnya masuk konteks AI.
11. Batas teks parser dokumen dinaikkan ke default 50.000 karakter per dokumen.
12. DOCX membaca header/footer; PPTX membaca speaker notes dan judul/data seri grafik; XLSX membaca sheet serta metadata judul/range seri grafik.
13. Retry Responses maksimal satu kali untuk HTTP 502/503/504, serta HTTP 500 hanya jika error memuat frasa persis `Unable to forward upstream stream`, dan hanya sebelum event stream pertama. Tidak ada retry otomatis setelah stream mulai.
14. Jika stream terputus setelah sebagian teks diterima, teks parsial dipertahankan dan user request tidak dibuang dari history.
15. Antrean pesan masuk per scope user/channel memperoleh FIFO gate sebelum parsing/unduh lampiran. Maksimal 10 waiter, timeout 300 detik, status saat penuh/timeout, dan pembersihan referensi saat cancellation. Setelah preprocessing, pemrosesan AI tetap memakai guild lock yang sudah ada. Antrean in-memory dan tidak dipulihkan setelah hard restart.
16. Environment override resource diberi hard cap: 10 lampiran, 10 gambar vision, 100 MB total lampiran, batas teks dokumen/konteks, serta batas konkurensi AI/image/audio.
17. XML parser beralih ke `defusedxml` dengan batas internal entity yang kompatibel dengan XML sederhana lama, tanpa mengizinkan external DTD/entity.
18. Anggota PDF/Office/audio dalam ZIP dan 7Z diperiksa signature-nya sebelum diparse; file dengan ekstensi yang tidak cocok ditolak dan dilaporkan.
19. Container DOCX/PPTX/XLSX wajib memiliki part inti OOXML. ODT/ODS/ODP dan EPUB memeriksa mimetype yang benar; entri mimetype wajib pertama dan tidak dikompresi. Container juga dibatasi jumlah anggota, ukuran per anggota, dan total ukuran sebelum parser dokumen dijalankan.

19. Sumber gambar edit menggabungkan lampiran baru dan gambar reply dengan deduplikasi, urutan prioritas, dan batas visual.
20. Provenance ZIP memberi path penuh, tipe, dan asal `ROOT`/`NESTED`; dua tes memastikan file root tidak salah diatribusikan ke arsip nested.
21. Vision memilih data URL lokal lebih dulu; URL CDN hanya fallback bila tidak ada gambar lokal.
22. Gambar reply yang sudah diproses saat ingress digunakan ulang saat image-edit, tanpa unduh/kompres ulang.
23. Nested ZIP dan ZIP di dalam 7Z memberi penanda truncation dan jumlah karakter yang tidak dimasukkan; anggota teks/dokumen 7Z juga menandai truncation.
24. Peringatan gambar dilewati menghitung jumlah yang benar-benar tidak tertampung.
25. Anggaran byte dekompresi dibagi bersama antara ZIP utama dan semua ZIP bersarang; ZIP di dalam 7Z memakai sisa budget hasil ekstraksi 7Z agar banyak arsip kecil tidak menggandakan batas dekompresi.
26. Status awal punya fallback ke channel jika reply ke pesan sumber gagal.
27. Konteks gambar edit yang disimpan di RAM dideduplikasi dan dibatasi ke jumlah maksimum gambar vision.

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
| 7 | PARTIAL | Signature PDF/7Z/RTF/audio, SVG, EPS, dan AI diperiksa; container OOXML/ODF/EPUB memerlukan struktur/mimetype sesuai. Teks biasa memang tidak memiliki satu magic signature universal. |
| 8 | DONE | Status per file dicatat. |
| 9 | DONE | Manifest menyertakan alasan error/unsupported yang sudah disanitasi; beberapa error tetap hanya dapat dikategorikan. |
| 10 | DONE | Kegagalan satu file diisolasi; ada tes batch 10 file dengan satu kegagalan. |

### B. ZIP dan 7Z

| No. | Status | Catatan |
|---:|---|---|
| 11 | DONE | ZIP umum dibaca, bukan hanya Office ZIP. |
| 12 | DONE | Teks, gambar, PDF, DOCX, PPTX, XLSX dibaca dari arsip. |
| 13 | PARTIAL | Anggota ZIP/7Z memeriksa struktur DOCX/PPTX/XLSX dan signature PDF/audio; validasi SVG/EPS/AI berlaku di jalur lampiran langsung. Teks/format langka dan validasi semantik semua anggota belum tuntas. |
| 14 | DONE | Parser 7Z memakai `py7zr`; dependency wajib untuk deployment. |
| 15 | PARTIAL | ZIP bersarang satu tingkat; path dan asal entri sudah eksplisit, tetapi ZIP lebih dalam dan 7Z bersarang tetap tidak diproses. |
| 16 | PARTIAL | Batas ukuran/rasio/entry, encrypted ZIP dan password-protected 7Z ditolak; corpus deterministik ZIP terpotong dan local-header termutasi diuji, tetapi belum fuzz-test menyeluruh lintas format. |
| 17 | DONE | Arsip yang tidak diparse tidak diklaim sudah dibaca. |

### C. Reply dan konteks percakapan

| No. | Status | Catatan |
|---:|---|---|
| 18 | DONE | Dokumen dari pesan reply ikut diproses; ada tes resolver dan batas anggaran. |
| 19 | DONE | Reply-image diproses sekali di ingress, kegagalan per gambar diisolasi, dan sumber baru + reply digabung; helper diuji, alur image-edit Discord tetap perlu live test. |
| 20 | PARTIAL | Konteks gambar diedit diuji untuk deduplikasi, batas vision, isolasi antar-user, dan expiry TTL; urutan edit/reply/regenerate end-to-end belum seluruhnya diuji. |
| 21 | PARTIAL | User/channel scope dan dokumen reply dijaga; kombinasi semua metadata dan media belum live diuji. |
| 22 | DONE | Reply dokumen tidak lagi dianggap kosong hanya karena pesan baru tidak punya attachment. |
| 23 | DONE | Dokumen reply yang tak tersedia dijelaskan; alur edit gambar memberi error bila sumber gambar tidak ditemukan. |

### D. Antrean pesan

| No. | Status | Catatan |
|---:|---|---|
| 24 | DONE | Antrean attachment tidak meminta unggah ulang karena semaphore timeout; rate-limit tetap dapat menolak spam. |
| 25 | DONE | Status menunggu dikirim sebelum lock. |
| 26 | DONE | Ingress FIFO gate diperoleh sebelum I/O lampiran; tes integrasi menahan parsing pesan pertama dan membuktikan pesan kedua tidak mulai lebih dulu. Tes FIFO, antrean penuh, dan cancellation/cleanup juga lulus. |
| 27 | DONE | Pesan diproses otomatis saat lock tersedia. |
| 28 | DONE | Status dan hasil diedit pada reply pesan asal; live delivery Discord tetap perlu staging. |
| 29 | DONE | Scope percakapan dibedakan per user/channel. |
| 30 | PARTIAL | Maksimal 10 waiter per scope, timeout 300 detik, status antrean penuh/timeout, dan pembatalan tugas sudah dites. Queue tidak persisten setelah hard restart dan belum ada tombol cancel oleh user. |
| 31 | DONE | Rate limit user dan global request budget terpisah dari antrean; satu instance per file budget tetap menjadi batas deployment. |

### E. Responses API dan streaming

| No. | Status | Catatan |
|---:|---|---|
| 32 | PARTIAL | Retry mencakup 502/503/504 dan HTTP 500 dengan frasa `Unable to forward upstream stream` sebelum event pertama; bentuk error upstream lain belum seluruhnya tercakup. |
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
| 44 | PARTIAL | XLSX sheet/chart metadata dan PPTX chart/slide data dibaca; column/bar dan pie chart kini punya fixture regresi, tetapi tidak semua jenis chart/cache/format kompleks tercakup. |
| 45 | PARTIAL | DOCX header/footer dan core metadata; PPTX speaker notes/chart dan core metadata; XLSX core metadata serta chart data. Comments, footnotes, embedded object, dan metadata kompleks belum seluruhnya diekstrak. |
| 46 | DONE | Pemotongan teks diberi keterangan, termasuk nested ZIP dan ZIP dalam 7Z. |
| 47 | DONE | Ada batas gabungan dan manifest file yang terdampak truncation. |
| 48 | DONE | Legacy DOC/XLS/PPT tidak dijanjikan sebagai format yang terbaca; format yang tak didukung harus ditandai. |

### G. Keamanan dan stabilitas

| No. | Status | Catatan |
|---:|---|---|
| 49 | PARTIAL | Signature PDF/7Z/RTF/audio/SVG/EPS/AI dan struktur container OOXML/ODF/EPUB diperiksa; matriks audio diuji dan image divalidasi parser. Format teks tetap tidak punya magic signature universal. |
| 50 | PARTIAL | Ukuran, rasio, entry, gambar, teks, dan buffer dibatasi; bukan sandbox CPU/RAM tingkat OS. |
| 51 | PARTIAL | Parser berat utama dijalankan via thread; belum profiling seluruh jalur parser di hosting. |
| 52 | PARTIAL | Corpus deterministik mencakup Office/ODF/EPUB salah label, mimetype salah, ZIP truncated/local-header mutation, audio/vector, dan expiry konteks gambar; belum fuzz corpus lintas semua format. |
| 53 | PARTIAL | Arsip tidak diekstrak ke disk; pembersihan semua state/resource tetap perlu uji restart/shutdown. |
| 54 | PARTIAL | XML tidak tepercaya diproses dengan defusedxml dan batas entity; instruksi dokumen tetap ditangani lewat prompt boundary, bukan sandbox penuh. |

### H. Tes dan verifikasi

| No. | Status | Catatan |
|---:|---|---|
| 55 | PARTIAL | 10 file campuran diuji melalui mocked Discord event; belum 10 upload sungguhan melalui Discord CDN. |
| 56 | PARTIAL | ZIP berisi beberapa format dan mismatch signature diuji; container DOCX/PPTX/XLSX kini diperiksa struktur internalnya, tetapi belum semua kombinasi format/korupsi. |
| 57 | PARTIAL | 7Z teks/DOCX, compression-ratio bomb, dan mismatch signature PDF dites; encrypted/arsip dunia nyata belum live diuji. |
| 58 | PARTIAL | Reply dokumen dan gambar, termasuk penggabungan gambar baru + gambar reply serta isolasi kegagalan gambar, dites via mock; live Discord staging tetap wajib. |
| 59 | PARTIAL | Tes image generation/edit sebelumnya sudah dikonfirmasi pengguna; belum dire-run pada kandidat terbaru. |
| 60 | PARTIAL | Scope user berbeda, konkurensi antar-scope, FIFO, queue timeout/full, dan cancellation dites via mock; belum tes dua user aktif bersamaan di Discord. |
| 61 | PARTIAL | SSE partial disconnect dan retry policy dites; belum inject error pada upstream live. |
| 62 | PARTIAL | Tes mencakup struktur Office/ODF/EPUB, signature audio/vector, dan ZIP mutation selain corrupt ZIP, rasio 7Z, dan batas file; belum seluruh format. |
| 63 | PARTIAL | History user/partial response dipertahankan dan retry dibatasi; belum pembuktian no-loss/no-duplicate live. |
| 64 | PARTIAL | CI dan regresi lulus; staging dan promosi produksi belum dilakukan. |

## Status antrean setelah hardening terbaru

- Chat dan pesan berlampiran tidak lagi ditolak oleh cooldown minimum default; sliding-window tetap berlaku (chat 10/60 detik, file 6/300 detik), ditambah kuota harian dan global.
- Antrean pesan masuk per user/channel sekarang memiliki FIFO gate sebelum parsing lampiran, maksimal 10 waiter, timeout 300 detik, penolakan saat penuh, dan cleanup saat cancellation. Tes integrasi membuktikan pesan kedua menunggu sampai parsing serta pemrosesan pesan pertama selesai. Antrean AI lanjutan tetap memiliki guild lock tersendiri.
- Pembatalan task saat menunggu lock maupun saat sedang diproses mencoba memperbarui status Discord dan selalu membersihkan referensi lock.
- Ini bukan antrean persisten. Jika proses hosting mati mendadak sebelum handler cancellation berjalan, antrean yang menunggu tidak dipulihkan otomatis. User perlu mengirim ulang pesan yang belum selesai.

## Gerbang yang masih wajib sebelum produksi

1. **Wispbyte candidate runtime:** dependency `py7zr>=1.0,<2` sudah terpasang. Pada 2026-11-25, log Wispbyte membuktikan `git fetch` + `reset --hard` ke commit `0499632e2fd26994f1974c2d109634e793b2369d`, kandidat mencetak `[ONLINE]`, dan pengguna mengonfirmasi bot terlihat online di Discord. Ini memverifikasi startup dan koneksi Gateway, bukan seluruh fitur lampiran/arsip/streaming.
2. **Discord real uploads:** unggah 10 file campuran nyata, termasuk ZIP/7Z, PDF scan, XLSX/PPTX, gambar, dan satu file corrupt/unsupported.
3. **Reply real:** reply ke dokumen dan gambar; ulangi dengan lampiran baru pada pesan yang sama.
4. **Multi-user:** dua atau lebih user mengirim pesan hampir bersamaan; pastikan history, status, dan jawaban tidak tertukar.
5. **Stream fault injection:** putuskan koneksi setelah delta pertama dan sebelum response.completed; pastikan teks parsial terlihat dan tidak ada request/tool duplicate.
6. **Restart/shutdown:** uji `memory.json`, `view_state.json`, `global_usage.json`, persistent buttons, dan antrean yang sedang menunggu.
7. **Provider behavior:** verifikasi Xingchi, Cloudflare, Discord CDN, dan fallback image dari jaringan Wispbyte.
8. **Cost control:** kuota request global bukan billing hard cap; pasang alert/cap di provider jika tersedia.
9. **Single process:** global budget menggunakan lock proses lokal. Jangan jalankan beberapa replica dengan file budget yang sama tanpa koordinasi lintas proses.
10. **Credential/API smoke test:** workflow manual `.github/workflows/staging-smoke-test.yml` tersedia untuk memvalidasi `STAGING_DISCORD_BOT_TOKEN` dan `STAGING_XINGCHI_API_KEY`. Pengguna sebelumnya mengonfirmasi workflow ini berhasil (hijau). Tes ini hanya memvalidasi autentikasi Discord dan satu chat Xingchi, bukan startup kandidat atau Wispbyte end-to-end.

## Temuan re-audit statis sebelumnya — 2026-11-25 (sudah diperbaiki)

Audit sebelumnya terhadap blob kandidat `f66ce2290471fd1fc4294088b2d12f352b638fed` menemukan tujuh temuan. Patch kandidat sekarang ada pada commit `406a9c360e2f2d94623b2a5e2a4a88457a6d3352`; tes regresinya ditambahkan pada commit `4d4c40c58682a8a55fb0a095e5472e31b2c6b27e`.

1. **Vision duplicate — FIXED.** `select_vision_images()` mengutamakan data URL lokal; URL remote hanya dipakai sebagai fallback. Tes membuktikan satu gambar lokal tidak ditambah lagi sebagai CDN URL.
2. **Reply image reprocessing — FIXED.** `process_ai_turn()` tidak lagi memanggil `resolve_replied_images()` ulang di image tool handler; memakai `image_data_urls` yang sudah diproses ingress.
3. **Nested archive truncation — FIXED.** Nested ZIP dan ZIP di dalam 7Z menambahkan marker truncation serta jumlah karakter yang tidak dimasukkan. Anggota teks/dokumen 7Z juga menandai truncation.
4. **ZIP image skip warning — FIXED.** Kode menghitung gambar yang diterima dan benar-benar dilewati sebelum membuat peringatan.
5. **Nested archive aggregate decompression budget — FIXED.** ZIP utama dan nested ZIP berbagi satu batas byte dekompresi; ZIP dalam 7Z menggunakan sisa budget hasil ekstraksi 7Z. Tes baru membuktikan ZIP bersarang kedua ditolak ketika budget gabungan habis.
6. **Status message reply failure — FIXED.** Jika reply status ke pesan sumber gagal (misalnya pesan sumber hilang), bot mencoba mengirim status langsung ke channel agar giliran tidak langsung gugur.
7. **Retained image context cap — FIXED.** Konteks gambar edit yang disimpan di RAM sekarang dideduplikasi dan dibatasi ke `MAX_VISION_IMAGES_PER_TURN`, sama dengan batas gambar vision.

Ketujuh perbaikan diuji pada suite kandidat: tes sumber vision, ZIP image budget, nested ZIP truncation, nested ZIP-in-7Z truncation, dan 7Z text truncation. Tidak ada live Discord/provider test dalam run ini.

## Perbaikan antrean ingress — 2026-11-25

Temuan bahwa parsing lampiran berlangsung sebelum lock percakapan telah diperbaiki pada kandidat:

- `on_message()` memperoleh `ingress_lock` per scope user/channel sebelum mulai mengunduh atau mem-parse lampiran.
- Lock memakai fairness FIFO `asyncio.Lock`, membatasi 10 waiter, menerapkan timeout antrean, dan membersihkan reference/lock setelah selesai atau dibatalkan.
- Setelah tahap ingress, `process_ai_turn()` tetap menggunakan `guild_lock` yang sudah ada; antrean AI dan jalur slash/regenerate tidak digabung atau diganti.
- Tes integrasi menahan parsing lampiran pesan pertama dan memastikan pesan kedua tidak mulai mem-parse sebelum pesan pertama selesai; tes antrean penuh dan cancellation juga lulus.
- Ini menjamin urutan antar-pesan `on_message` pada scope yang sama di satu proses bot. Ini bukan antrean persisten lintas restart atau koordinasi multi-replica; perilaku Discord Gateway live tetap perlu diuji.

## Verifikasi CI dan runtime terbaru

- Run GitHub Actions `45565802138`: seluruh job selesai dengan `success` pada commit `886af9936e9c601407bd59dd300c5dad51db6255`.
- Candidate suite: **85/85 PASS**; regresi lama: **76/76 PASS** (161 tes total). Tes tambahan mencakup struktur container Office, mimetype ODF/EPUB, corpus ZIP mutation, matriks signature audio/vector, isolasi/expiry konteks gambar, pie chart XLSX/PPTX, dan metadata inti DOCX/PPTX/XLSX.
- `py_compile`, `pip check`, Bandit medium/high, dan `pip-audit --strict` selesai sukses pada workflow.
- Wispbyte runtime terakhir yang dikonfirmasi masih mengambil commit `0499632e2fd26994f1974c2d109634e793b2369d`; kandidat terbaru (kode blob `da46080de4a8b9f3921bc44ead480ae2d819fa53`, dites pada commit `886af9936e9c601407bd59dd300c5dad51db6255`) belum di-deploy. File `main (4).py` pada `main` tetap SHA `ffffd0c17169b29504f4a8713dca396cf61ddeff`.
- Tes manual attachment sebelumnya bukan pengganti tes ulang setelah commit terbaru; kuota API CLOUZER saat ini tidak dipakai untuk tes tambahan.

## Keputusan

Kandidat saat ini telah melewati compile, pip check, Bandit medium/high severity, pip-audit tanpa kerentanan yang diketahui, 79 tes khusus kandidat, dan 76 tes regresi lama. Itu bukti kuat untuk gerbang otomatis, **bukan bukti bahwa 64 butir semuanya sudah selesai atau bot siap produksi**.

Jangan menimpa `main.py` sampai semua tes staging di atas selesai.

## Artefak audit

- Kandidat: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/revisi_audit_final_main.py
- Tes kandidat: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/tests/test_audit_candidate_archives.py
- Tes regresi lama: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/test_audit_regressions.py
- Dependensi staging: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/requirements-audit.txt
- Perintah staging: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/STARTUP_COMMANDS.txt
- Workflow CI: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/.github/workflows/audit-candidate-check.yml
