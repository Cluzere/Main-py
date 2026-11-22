# Independent Root-Cause Audit — Hardened Candidate

Tanggal: 2026-11-23
Repository: Cluzere/Main-py
Branch: audit-recovery-gpt-2026-11-23

## Executive decision

**Candidate hardening: CI PASS. Production deployment: NOT YET APPROVED.**

File asli audit-recovery/main_final.py tidak diubah. Kandidat perbaikan dibuat terpisah:

- main_final_hardened_candidate.py
- test_audit_regressions.py
- .github/workflows/audit-recovery.yml

GitHub Actions run terakhir:
- Run: https://github.com/Cluzere/Main-py/actions/runs/45081946460
- Commit: d36aa73f815bd554dcee60023f8bf9feb1d3da24
- py_compile: PASS
- Regression tests: **15/15 PASS**
- Runtime target: GitHub Actions, CPython 3.12.16

Ini membuktikan kandidat dapat dikompilasi dan 15 regresi terfokus lulus. Ini bukan bukti 174/174 suite lama, bukan full integration test Discord/provider, dan bukan bukti seluruh 51 mutation tests.

## Perubahan akar masalah yang diterapkan

### F-01 — Syntax error pada RateLimiter
Severity: **P1 / startup blocker**
- Kode asli menggabungkan events.append(now) dan if daily is not None: dalam satu baris tanpa pemisah.
- Dampak: file tidak dapat dikompilasi; bot tidak dapat startup.
- Perbaikan: pisahkan append event dan increment counter harian ke baris yang benar.
- Verifikasi: py_compile pada kandidat PASS.

### F-02 — Memory corruption dapat berubah menjadi penghapusan data diam-diam
Severity: **P1 / data-loss risk**
- Sebelumnya load_memory() menangkap error JSON/read dan mengembalikan {"guilds": {}}.
- Bot kemudian dapat berjalan dan save_memory() menulis state kosong ke memory.json, menggantikan file yang gagal dibaca.
- Root JSON yang bukan object atau field guilds yang salah tipe juga perlu diperlakukan sebagai file rusak, bukan state kosong.
- Perbaikan: jika file sudah ada tetapi gagal dibaca/validasi, startup dihentikan dengan RuntimeError. File asli tidak ditimpa otomatis. File yang memang belum ada tetap mulai dari state kosong.
- Verifikasi: tes valid format baru, format legacy, file hilang, JSON rusak, root salah tipe, guilds salah tipe, dan file oversize.

### F-03 — view_state.json tidak memiliki batas ukuran sebelum parsing
Severity: **P2 / startup resource exhaustion**
- Sebelumnya ViewStateStore._load() menjalankan json.load() pada seluruh file sebelum batas jumlah entry diterapkan.
- File yang membengkak dapat menghabiskan RAM saat startup. Jika parsing gagal, state dikosongkan dan save berikutnya dapat mengganti file yang rusak.
- Perbaikan:
  - batas file default 16 MB, dapat diatur dengan MAX_VIEW_STATE_FILE_MB;
  - validasi root dan versi format;
  - file yang oversize/rusak membuat startup berhenti, bukan menghapus state diam-diam.
- Verifikasi: file hilang, JSON rusak, root salah, dan file oversize diuji.

### F-04 — Log redaction bersifat fail-open
Severity: **P1 / potential secret disclosure**
- Sebelumnya SecretRedactionFilter.filter() menangkap error scrubber, lalu tetap mengembalikan True. Artinya pesan/traceback asli dapat diteruskan jika proses redaksi gagal.
- Perbaikan: jika redaksi melempar exception, record diganti pesan generik dan args, exception info, exception text, serta stack info dibuang.
- Verifikasi: simulasi scrubber gagal; tes memastikan pesan dan traceback mentah tidak lolos.

### F-05 — Validasi ukuran lampiran dilakukan tidak cukup defensif sebelum read
Severity: **P2 / memory pressure**
- Batas per jenis dan total sudah diperiksa oleh caller menggunakan metadata Discord. Namun helper pembaca masih menerima metadata ukuran nol dan hanya membandingkan ukuran aktual setelah seluruh attachment dibaca ke RAM.
- Perbaikan: helper sekarang menolak metadata ukuran tidak valid, ekstensi yang tidak punya batas, dan ukuran di atas batas per jenis sebelum memanggil attachment.read(). Ukuran aktual tetap diperiksa setelah read.
- Verifikasi: tes membuktikan metadata nol dan file teks di atas batas ditolak sebelum method read() dipanggil; attachment kecil valid lolos.

### F-06 — Endpoint diagnostik berbayar terbuka secara default
Severity: **P2 / cost-abuse exposure**
- /cek-tool dapat memicu panggilan API provider. Sebelumnya DIAGNOSTIC_ADMIN_ONLY default False, sehingga akses bergantung hanya pada izin channel dan rate limit per pengguna.
- Perbaikan: default diubah menjadi admin-only. Pemilik dapat membuka akses secara eksplisit dengan DIAGNOSTIC_ADMIN_ONLY=0.
- Verifikasi: perubahan konfigurasi ditinjau statis; tidak ada live Discord permission test.

## Bagian yang diperiksa dan tampak memiliki proteksi

Status di bawah adalah static review, bukan integration test:

- SSRF image URL: HTTPS only, menolak credentials/IP literal/private DNS answers, DNS dipin ke resolver, redirect dimatikan.
- SSE Responses: batas total stream 20 MB dan baris 1 MB; respons error dibaca dengan limit.
- ZIP Office/EPUB: batas entry, ukuran per entry, total uncompressed, compression ratio, dan encrypted ZIP.
- PDF: pembatasan halaman/teks dan rendering gambar.
- Persistent view: pemilik pesan diperiksa sebelum tombol dipakai.
- Memory scope: key memisahkan guild/channel/user dan DM user/channel.
- Rate limiter: cooldown, sliding window, kuota harian dan pruning tersedia.
- Channel setup: perintah izin channel memeriksa Manage Channels/Administrator.
- Prompt/output: sejumlah jalur melakukan secret scrub dan pembatasan panjang.

## Risiko tersisa — wajib diputuskan sebelum deploy

1. **DM terbuka untuk semua pengguna secara default.** is_channel_allowed() mengembalikan true saat guild is None. Scope memori DM dipisahkan per user, tetapi siapa pun yang dapat DM akun bot bisa memakai API dan fitur bot. Kalau bot bukan untuk publik, tambahkan allowlist user ID atau matikan DM.
2. **Rate limit berada di RAM proses.** Restart mereset cooldown/kuota; multi-instance tidak berbagi counter. Ini bukan batas biaya keras.
3. **Secret scanning bersifat heuristik.** Tidak bisa dianggap antivirus/DLP; screenshot/gambar tidak dipindai untuk credential.
4. **Log filter dipasang pada root handlers yang sudah ada saat startup.** Handler/logger khusus yang ditambahkan kemudian atau tidak propagate ke root perlu diuji pada hosting.
5. **Provider/runtime belum diuji live.** Belum ada verifikasi Discord gateway, Xingchi Responses SSE real, Cloudflare image, Pollinations fallback, atau perilaku Wispbyte.
6. **Penyimpanan lokal tetap sensitif.** memory.json dan view_state.json berisi teks percakapan. Jaga permission, backup, dan jangan commit file runtime atau .env.
7. **Dependency/runtime compatibility belum dibuktikan pada hosting.** CI saat ini hanya compile file dan menjalankan unit regression terisolasi; belum menginstal dan menguji seluruh dependency produksi.

## Klaim pengujian lama 174/174 dan mutation

Laporan lama mengklaim 174/174 dari lima suite, termasuk test_merge_additions.py (6 tes). Bukti file untuk test_merge_additions.py, FINAL_TEST_RESULTS.txt, serta mutation runner/log milik paket GPT tidak tersedia di arsip Review Round 2. Runner/log Clouzer dan HydroGemini adalah proyek berbeda dan tidak dihitung.

Karena itu:
- 174/174 lama: **UNVERIFIED**.
- 51 mutation GPT: **UNVERIFIED**.
- Empat log suite lama: tidak dianggap sebagai hasil pengujian independen hanya karena ada di ZIP.
- Tes baru yang benar-benar dijalankan pada GitHub Actions: **15/15 PASS**.

## Langkah sebelum produksi

1. Jalankan kandidat ini pada staging terpisah dengan secrets yang disimpan di GitHub/Wispbyte environment, bukan di source.
2. Uji Discord: guild allowlist, DM policy, /channel, /fast, /full-thinking, /imagine, /cek-tool, tombol regenerate/DM, restart, dan shutdown.
3. Uji provider: HTTP error, malformed SSE, stream terputus, timeout, rate limit, dan response besar.
4. Pastikan backup memory.json dan view_state.json tersedia dan file rusak tidak tertimpa.
5. Putuskan apakah DM publik memang diinginkan. Jika tidak, implementasikan allowlist sebelum deploy.
6. Baru setelah checklist staging lolos, buat kandidat produksi dari file hardened ini. Jangan mengganti file hosting secara langsung tanpa backup.

## File dan link

- Kandidat hardened: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/main_final_hardened_candidate.py
- Tes regresi: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/test_audit_regressions.py
- Workflow CI: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/.github/workflows/audit-recovery.yml
- Hasil CI: https://github.com/Cluzere/Main-py/actions/runs/45081946460
- Sumber asli (tidak diubah): https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/main_final.py
