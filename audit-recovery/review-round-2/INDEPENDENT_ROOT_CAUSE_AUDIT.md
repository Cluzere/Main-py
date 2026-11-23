# Independent Root-Cause Audit — Hardened Candidate

Tanggal pembaruan: 2026-11-23
Repository: Cluzere/Main-py
Branch: audit-recovery-gpt-2026-11-23

## Keputusan

**Static hardening + CI: PASS. Live production readiness: BELUM DISETUJUI.**

File sumber asli audit-recovery/main_final.py tetap tidak disentuh. Semua perubahan dilakukan langsung pada kandidat yang sudah ada:

- audit-recovery/review-round-2/main_final_hardened_candidate.py
- audit-recovery/review-round-2/test_audit_regressions.py
- Laporan ini diperbarui di tempat.

CI terakhir:
- Run: https://github.com/Cluzere/Main-py/actions/runs/45085155614
- Commit kandidat: c87f515cb6f63d9d235ee39f1eb6728fba0b1ce1
Commit tes: def0a416ff8055608ca3930d163bfcb01ecb1b4b
- CPython: 3.12.16
- py_compile: PASS
- Regression tests: **25/25 PASS**

Tes adalah unit/regression terisolasi dan beberapa metode diekstrak via AST. Ini bukan live integration test Discord, Xingchi, Cloudflare, Pollinations, atau hosting. Tes view-state juga belum mencakup seluruh siklus persistensi dan interaksi Discord.

## Temuan dan perubahan yang diterapkan

### F-01 — Syntax blocker pada RateLimiter
Severity: P1 / startup blocker
- Kesalahan sintaks pada versi sumber sebelumnya telah diperbaiki pada kandidat.
- Verifikasi: kandidat berhasil py_compile pada CI Python 3.12.16.

### F-02 — File memori rusak berpotensi ditimpa state kosong
Severity: P1 / data-loss risk
- load_memory() sekarang fail-closed jika file yang sudah ada terlalu besar, JSON rusak, root bukan object, atau struktur guilds salah.
- Startup dihentikan agar file lama tidak tertimpa otomatis.
- Format lama yang valid tetap dimigrasikan.
- Penulisan memori menggunakan temporary file, flush/fsync, replace, dan permission 0600.

### F-03 — view_state.json dibaca tanpa batas ukuran yang memadai
Severity: P2 / startup resource exhaustion
- Batas default file: 16 MB (MAX_VIEW_STATE_FILE_MB).
- Validasi versi/root/entries dilakukan sebelum state dipakai.
- File rusak atau oversize menghentikan startup, bukan dikosongkan lalu ditimpa.
- Entri kedaluwarsa, jumlah entri, snapshot, dan media dibatasi melalui ViewStateStore._enforce_limits().

### F-04 — Log redaction berpotensi fail-open
Severity: P1 / secret disclosure
- SecretRedactionFilter menyensor pesan, traceback, dan stack info.
- Jika scrubber gagal, record diganti pesan generik; args dan detail exception mentah dibuang.
- Tes simulasi redaction failure memastikan string rahasia tidak diteruskan.

### F-05 — Pembacaan attachment tidak cukup ketat sebelum read
Severity: P2 / memory pressure
- Metadata ukuran nol/tidak valid, ekstensi tanpa batas, dan ukuran yang melebihi limit ditolak sebelum attachment.read().
- Ukuran aktual dan signature file tetap diperiksa setelah dibaca.
- Batas umum default: maksimal 4 attachment per pesan, total 40 MB, dan 2 proses attachment paralel.
- Batas decompressed ZIP: 2.000 entries, 100 MB total, 25 MB per entry, compression ratio 200.

### F-06 — Endpoint diagnostik dapat menghabiskan API
Severity: P2 / cost-abuse exposure
- /cek-tool admin-only secara default (DIAGNOSTIC_ADMIN_ONLY=1).
- Akses memerlukan Manage Server atau Administrator pada server.
- Pemilik dapat membuka akses dengan DIAGNOSTIC_ADMIN_ONLY=0, tetapi ini memperbesar risiko biaya.
- Diagnostik memakai anggaran global Responses dan memesan dua request karena probe menjalankan dua request.

### F-07 — Rate limit per-user tidak melindungi total biaya global dan reset saat restart
Severity: P1 / cost-abuse exposure
- Sebelumnya pembatas utama hanya per pengguna dan disimpan di RAM.
- Ditambahkan GlobalDailyBudget, file global_usage.json, lock dalam proses, penulisan atomic, permission 0600, serta fail-closed jika file rusak.
- Kuota global bertahan setelah restart selama file global_usage.json tidak dihapus/rusak.
- Default kuota harian berbasis tanggal UTC:

| Kategori | Batas default | Yang dihitung |
|---|---:|---|
| Responses API | 600 / hari | Setiap request Responses, termasuk continuation |
| Image generation/edit | 40 / hari | Satu operasi generate/edit; fallback provider tetap satu operasi |
| Audio transcription | 80 / hari | Request transkripsi yang benar-benar akan dikirim |

- Nilai dapat diubah lewat GLOBAL_DAILY_RESPONSES_LIMIT, GLOBAL_DAILY_IMAGES_LIMIT, dan GLOBAL_DAILY_AUDIO_LIMIT.
- Audio yang kosong, tidak memiliki konfigurasi Cloudflare, atau tidak memiliki akun siap tidak menghabiskan kuota audio.
- Jika kuota habis, request baru ditolak sampai hari UTC berikutnya.
- Jika file budget korup atau gagal ditulis, request ditolak (fail-closed).

**Batas penting:** ini adalah kuota jumlah operasi/request, bukan batas uang dalam rupiah/USD atau batas token. Biaya per request bisa berbeda. Lock/file ini ditujukan untuk satu proses bot aktif pada satu host; jangan jalankan beberapa instance bersamaan dengan file yang sama karena lock tidak lintas proses. Menghapus global_usage.json akan mengatur ulang penghitung.

### F-08 — Konkurensi image/audio sebelumnya belum dibatasi lintas pengguna
Severity: P2 / resource exhaustion
- Queue gambar sebelumnya hanya mengunci per-user; banyak pengguna berbeda dapat memanggil provider gambar serentak.
- Ditambahkan semaphore global terpisah untuk image dan audio, masing-masing default 2 operasi bersamaan dan dapat diatur melalui MAX_CONCURRENT_IMAGE_CALLS / MAX_CONCURRENT_AUDIO_CALLS.

### F-09 — Gambar beresolusi tinggi dapat didekode sebelum batas piksel diperiksa
Severity: P2 / memory exhaustion
- Helper kompresi gambar sebelumnya memanggil convert sebelum memeriksa jumlah piksel eksplisit. Gambar terkompresi kecil dengan dimensi besar dapat meningkatkan pemakaian RAM saat decode.
- Sekarang dimensi dicek sebelum verify/decode/convert dan dicek ulang setelah dibuka kembali. Batas default 25.000.000 piksel.
- Ditambahkan regresi yang memastikan gambar di atas batas ditolak sebelum verify maupun convert.

### F-10 — Regresi pengujian pembatas dan state belum cukup
Severity: P2 / test coverage
- Ditambahkan tes langsung untuk cooldown, sliding window, kuota rolling 24 jam, pengecualian user, pembatas jumlah/retensi view-state, dan kuota global persisten.
- CI sekarang menjalankan 25 tes dan seluruhnya lulus pada run terakhir.
- Beberapa test harness masih terisolasi; kelulusan tidak membuktikan seluruh aplikasi berjalan benar saat terhubung ke Discord/provider.

### F-11 — Kuota harian per pengguna sebelumnya bukan sliding window
Severity: P2 / akurasi rate limit
- Audit lanjutan menemukan implementasi lama menghitung 24 jam dari request pertama, lalu mereset seluruh hitungan sekaligus. Itu fixed window, bukan rolling/sliding 24 jam seperti yang dinyatakan.
- Kandidat sekarang menyimpan timestamp request dan mengeluarkan setiap hitungan setelah tepat 24 jam; request yang lebih baru tidak ikut ter-reset lebih awal.
- Ditambahkan regresi yang menguji batas tepat 24 jam untuk tiap request.
- Dampak memori: timestamp dibatasi oleh kuota per pengguna; konfigurasi default tetap 400 hitungan per pengguna per sliding 24 jam.

## Limit aktual yang tertanam di kandidat

Semua angka di bawah adalah default; environment dapat mengubah nilai kategori yang mendukung override.

| Fitur | Cooldown | Maksimum per jendela |
|---|---:|---:|
| Chat | 4 detik | 10 / 60 detik |
| File | 12 detik | 6 / 300 detik |
| Image | 20 detik | 8 / 600 detik |
| Regenerate | 8 detik | 6 / 120 detik |
| Summarize | 60 detik | 3 / 600 detik |
| Diagnostic | 120 detik | 3 / 3.600 detik |
| DM copy button | 5 detik | 5 / 120 detik |

- Kuota per pengguna: 400 hitungan per sliding 24 jam yang sebenarnya (RATE_DAILY_LIMIT_PER_USER=400); setiap hitungan kedaluwarsa 24 jam setelah request masing-masing.
- File dan DM copy tidak mengurangi kuota harian user (counts_daily=False), tetapi tetap terkena cooldown/jendela kategori.
- Kuota per-user ini masih di RAM: restart meresetnya. RATE_LIMIT_EXEMPT_USER_IDS dapat mengecualikan ID tertentu dari rate limiter per-user; kuota global tetap berlaku.
- RATE_LIMIT_ENABLED=0 menonaktifkan rate limiter per-user, bukan kuota global.
- Konkurensi panggilan AI: 4 (MAX_CONCURRENT_AI_CALLS=4).
- Konkurensi image provider: 2 (MAX_CONCURRENT_IMAGE_CALLS=2).
- Konkurensi audio provider: 2 (MAX_CONCURRENT_AUDIO_CALLS=2).
- Konkurensi attachment: 2 (MAX_CONCURRENT_ATTACHMENTS=2).
- Maksimum gambar vision per turn: 5.
- Maksimum dimensi gambar: 25.000.000 piksel; output gambar default maksimal 12 MB.
- Batas riwayat prompt: 240.000 karakter.
- Batas Responses SSE: 20 MB total dan 1 MB per baris.

## Akses publik dan permission

Kebijakan mengikuti keputusan pemilik: **bot publik**.
- DM terbuka untuk semua pengguna Discord yang dapat menghubungi bot.
- Di server, bot hanya merespons channel yang diizinkan admin melalui /channel.
- **Semua pesan non-bot di channel yang diizinkan memicu AI, bukan hanya pesan yang mention bot.** Pesan teks biasa, attachment, dan reply dapat memakan kuota. Jangan izinkan channel umum kecuali memang ingin bot auto-reply di seluruh percakapan channel itu.
- /channel memeriksa Manage Channels atau Administrator.
- /cek-tool admin-only secara default dan memeriksa Manage Server atau Administrator.
- Command chat, summarize, dan imagine dapat digunakan oleh anggota pada channel server yang sudah diizinkan.
- Ini bukan allowlist pengguna global. DM publik berarti pengguna mana pun dapat memakai kuota global bot; kuota global baru ditambahkan untuk membatasi dampaknya.

## Proteksi lain yang diperiksa

Static review:
- Fetch image URL hanya HTTPS, menolak credentials/IP literal/private DNS, mem-pin hasil DNS publik, dan mematikan redirect.
- HTTP response dan SSE dibaca dengan batas ukuran dan timeout pada jalur utama.
- ZIP Office/EPUB diperiksa sebelum parser mengekstrak data.
- XML entity eksternal/parameter dan pola entity expansion berbahaya ditolak.
- Tombol Regenerate/DM memverifikasi pemilik pesan.
- Scope memory dipisahkan per guild/channel/user dan DM user/channel.
- Secret scrub diterapkan pada prompt, lampiran, log, dan pesan error di sejumlah jalur.
- Bot menggunakan AllowedMentions.none() agar respons tidak memicu mention massal.

## Risiko yang masih terbuka

1. **Belum ada live integration test.** Belum dibuktikan pada runtime hosting bahwa Discord gateway, permission, command sync, tombol persistent, Xingchi SSE, Cloudflare image/edit/audio, Pollinations fallback, dan shutdown berjalan end-to-end.
2. **Kuota global bukan billing hard cap.** Batas 600/40/80 membatasi jumlah operasi, bukan nominal biaya atau token. Pasang billing cap/alert pada provider bila tersedia.
3. **Satu instance saja.** GlobalDailyBudget menggunakan thread lock lokal proses; beberapa proses/replica dapat race dan melewati batas bersama.
4. **Rate limit per-user reset saat restart.** Kuota global tetap tersimpan, tetapi counter per-user tidak.
5. **Penyimpanan percakapan sensitif.** memory.json, view_state.json, dan global_usage.json harus tetap lokal, permission ketat, dibackup, dan jangan pernah commit .env atau data runtime.
6. **Secret scanning heuristik.** Bukan DLP/antivirus; tidak menjamin dapat mengenali semua secret, terutama yang ada dalam screenshot/gambar.
7. **Dependency hosting belum diuji penuh.** CI tidak memasang seluruh dependency produksi dan tidak menguji versi library yang terpasang di Termux/hosting.
8. **Kebijakan DM publik meningkatkan exposure biaya.** Ini sesuai keputusan pemilik, tetapi siapa pun dapat mengonsumsi kuota global.

## Klaim pengujian lama

- Klaim suite lama 174/174 dan mutation test 51 tidak memiliki bukti yang cukup pada arsip yang diperiksa; tetap UNVERIFIED.
- Tes terverifikasi pada CI saat ini: 25/25 PASS.
- Tes tersebut tidak boleh disamakan dengan 24 skenario live Discord/provider.

## Langkah wajib sebelum deploy

1. Jalankan kandidat di staging/instance terpisah dengan environment secrets asli; jangan menaruh token dalam source.
2. Uji DM publik, server channel allowlist, /channel, /fast, /full-thinking, /summarize, /imagine, /cek-tool, tombol Regenerate/DM, restart, dan shutdown.
3. Uji provider sukses/gagal, timeout, rate limit, malformed SSE, stream terputus, dan fallback image.
4. Pastikan global_usage.json ikut dipertahankan di persistent storage dan tidak dibagikan/di-commit.
5. Pastikan billing alert/cap di provider aktif. Kuota request bot bukan jaminan nominal tagihan.
6. Setelah staging lolos, baru jadikan kandidat ini file produksi. Backup file dan data lama sebelum mengganti file hosting.

## Link

- Kandidat hardened: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/main_final_hardened_candidate.py
- Tes regresi: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/review-round-2/test_audit_regressions.py
- Workflow CI: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/.github/workflows/audit-recovery.yml
- CI terakhir: https://github.com/Cluzere/Main-py/actions/runs/45084617517
- File asli yang tidak diubah: https://github.com/Cluzere/Main-py/blob/audit-recovery-gpt-2026-11-23/audit-recovery/main_final.py
