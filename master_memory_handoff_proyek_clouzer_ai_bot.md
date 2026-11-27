# MASTER MEMORY & HANDOFF PROYEK — CLOUZER AI BOT

Dokumen ini adalah referensi tunggal (*single source of truth*) untuk melanjutkan pengembangan, audit, dan pemeliharaan **Clouzer AI Bot**. Seluruh instruksi, struktur kode, status pengujian, dan batasan di bawah ini wajib dipatuhi pada sesi berikutnya.

---

## 1. Identitas, Sejarah & Tujuan Proyek

* **Nama Platform / Bot:** Clouzer AI Bot (`Clouzer AI`)
* **Persona & Model Utama:** OpenAI GPT 6.1 SOL (`XINGCHI_MODEL = gpt-6.1-sol`)
* **Developer / Pemilik:** `clouzerd`
* **Hosting Produksi:** Wispbyte (panel berbasis Pterodactyl, menjalankan `main.py` dengan konfigurasi `/.env`)
* **Tujuan & Evolusi Proyek:**
  1. **Fase 1 — Asisten Chat & Reasoning Discord:** Dibangun menggunakan `discord.py` dan `aiohttp` yang terhubung ke Xingchi API (`/v1/responses`) dengan dukungan *streaming* teks *real-time* (SSE), mode berpikir cepat (`/fast`) dan mendalam (`/full-thinking`), *built-in web search*, serta penyimpanan riwayat percakapan per *scope* di `memory.json`.
  2. **Fase 2 — Integrasi Visual & Rotasi Multi-Akun Cloudflare:** Ditambahkan *custom function calling* `generate_image` dan perintah `/imagine` yang memakai rotasi 3 akun Cloudflare Workers AI (`CF_ACCOUNTS_JSON`) untuk *text-to-image* (`@cf/leonardo/lucid-origin`) dan *image-to-image editing* (`@cf/black-forest-labs/flux-2-klein-4b`), serta fallback generasi gambar ke Pollinations FLUX.
  3. **Fase 3 — Multi-Format Reader & Transkripsi Audio (STT):** Diperluas agar mampu membaca dokumen Office, PDF (teks & hasil scan), arsip e-book, file desain vektor, file kode/teks, serta mentranskripsi file audio (termasuk `.aac`) melalui Cloudflare Workers AI (`@cf/openai/whisper-large-v3-turbo`) sebelum dianalisis oleh `gpt-6.1-sol`.

---

## 2. Arsitektur Teknis (Discord, Xingchi API, Cloudflare Workers AI)

### A. Layer Discord & Manajemen Konkurensi (`discord.py`)
* **Intents & Prefix:** `discord.Intents.default()` dengan `intents.message_content = True`, prefix `!` (fokus utama pada pesan di *channel* yang diizinkan dan *slash commands*).
* **Kontrol Izin Channel:** Disimpan di `memory.json` pada `guild_<guild_id>_ch_0_user_0` di bawah array `allowed_channels`. Mendukung *text channel* maupun *thread* (mengecek `channel.parent_id`).
* **Locking & Antrean:**
  * `self.guild_locks[scope_key]` (`asyncio.Lock`) per `guild_<id>_ch_<id>_user_<id>` untuk mencegah tabrakan *turn* percakapan.
  * `self.user_image_locks[user_id]` + `self.image_busy_users` untuk mengantrekan permintaan generasi/edit gambar per pengguna.
  * `self.memory_lock` + *atomic write* (`memory.json.tmp` $\rightarrow$ `os.replace`) saat menyimpan `memory.json`.

### B. Layer LLM & Reasoning (`Xingchi API /v1/responses`)
* **Endpoint & Header:** `POST {XINGCHI_BASE_URL}/responses` (`https://api.xingchiapi.com/v1/responses`) dengan `Authorization: Bearer {XINGCHI_API_KEY}`, `User-Agent: opencode/1.0.0`, `X-Client-Name: OpenCode`.
* **Streaming SSE:** Memproses event `response.output_text.delta`, `response.web_search_call.*`, `response.output_item.done`, dan `response.completed`. Pesan Discord diedit bertahap tiap $\ge 0.8$ detik dengan batas potong `DISCORD_MSG_CHUNK_LIMIT` (default `1900`, maks `1990` karakter).
* **Mode Berpikir:**
  * `fast`: `reasoning.effort = "low"`, `max_output_tokens = 4096`, timeout total `180s`.
  * `full`: `reasoning.effort = "high"`, `max_output_tokens = 8192`, timeout total `300s`.
* **Tool Calling & Guardrail:**
  * `web_search` selalu aktif saat `allow_tools=True`.
  * `generate_image` hanya disertakan jika `should_allow_image_tool()` mengembalikan `True` (diblokir otomatis jika user hanya meminta analisis/penjelasan dokumen atau gambar).
  * Setiap `function_call` wajib dilanjutkan dengan pasangan `function_call` + `function_call_output` via `extra_input`.

### C. Layer Media & Audio (`Cloudflare Workers AI` + Fallback)
* **Endpoint:** `POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model_id}`.
* **Load Balancing & Cooldown:** Rotasi *round-robin* pada `CF_ACCOUNTS_JSON` (3 akun). Jika akun mengembalikan status $\neq 200$ atau *exception*, akun masuk *cooldown* selama `CF_COOLDOWN_SECONDS` (default `45` detik).
* **Image Generation:** `@cf/leonardo/lucid-origin` (JSON payload) atau model Flux-2 (multipart `FormData`), dengan fallback otomatis ke Pollinations FLUX (`https://image.pollinations.ai/prompt/...`) jika seluruh akun Cloudflare gagal/cooldown.
* **Image Editing:** `@cf/black-forest-labs/flux-2-klein-4b` (multipart `FormData` dengan `input_image_0..3`, maks 4 gambar sumber, thumbnail `768x768`). Mengambil gambar dari lampiran pesan, pesan yang di-*reply*, atau cache konteks terakhir `(guild_id, channel_id, user_id)` (TTL `1800s`, maks `30` entri LRU). **Jika edit gagal, bot dilarang melakukan fallback ke generator gambar baru.**
* **Audio Transcription (STT):** `@cf/openai/whisper-large-v3-turbo` menerima payload JSON `{"audio": "<base64_ascii>"}` untuk file audio hingga `25 MB`.

---

## 3. Struktur Kode `main.py`

1. **Inisialisasi & Konfigurasi (`load_dotenv()`):**
   * Wajib: `DISCORD_TOKEN`, `XINGCHI_API_KEY`.
   * Utama & Cloudflare: `XINGCHI_BASE_URL`, `XINGCHI_MODEL`, `CF_ACCOUNTS_JSON`, `CF_IMAGE_MODEL`, `CF_EDIT_MODEL`, `CF_AUDIO_MODEL`.
   * Tuning: `CF_COOLDOWN_SECONDS`, `IMAGE_CONTEXT_TTL_SECONDS`, `IMAGE_CONTEXT_MAX_ENTRIES`, `MAX_OUTPUT_IMAGE_BYTES`, `MAX_IMAGE_PIXELS`, `DISCORD_MSG_CHUNK_LIMIT`, `MEMORY_PROMPT_MAX_LEN`.
2. **Utilitas & Sanitasi:**
   * `normalize_cf_json_str()`, `load_cf_accounts()` (parsing toleran escape/quote pada JSON env).
   * `normalize_aspect_ratio_str()`, `extract_aspect_ratio_from_text()`, `resolve_image_dimensions()` (10 rasio aspek + deteksi rasio gambar asli).
   * `should_allow_image_tool()`, `scrub_base64_text()`, `scrub_nested_base64()`, `clean_response_text()`, `split_discord_chunks()`, `validate_and_normalize_image_bytes()`, `build_failure_tool_continuation()`.
3. **Komponen UI (`AIResponseView`):**
   * Tombol `🔄 Regenerate` (`ai_regenerate`) dan `📋 Kirim Teks via DM` (`ai_dm_text`) dengan `timeout=600`.
4. **Core Bot (`ClouzerBot`):**
   * Memori & State: `load_memory()`, `save_memory()`, `_normalize_scope_state()`, `_sanitize_memory_for_disk()`, `get_scope_key()`, `get_guild_state()`, `is_channel_allowed()`, `has_any_allowed_channel()`.
   * Ekstraktor File (`encode_attachment()`): Gambar (`.png/.jpg/.jpeg/.webp/.gif`), PDF (`extract_pdf_content` via PyMuPDF + render PNG fallback), Office (`extract_docx_content`, `extract_pptx_content`, `extract_xlsx_content`, `extract_rtf_content`), Arsip (`extract_zip_office_or_epub_content` untuk `.odt/.ods/.odp/.epub`), Desain (`extract_design_file_content` untuk `.svg/.eps/.ai`), Audio (`transcribe_audio_bytes` untuk `.mp3/.wav/.ogg/.m4a/.flac/.webm/.aac/.opus`), serta file teks/kode.
   * Pipeline AI: `build_messages_payload()`, `to_responses_input()`, `run_responses_ai()`, `process_ai_turn()`.
   * Lifecycle: `setup_hook()`, `_run_background_sync()`, `schedule_command_sync()`, `close()`.
5. **Slash Commands & Events:**
   * `/channel` (`channel_perm_cmd`), `/fast` (`fast_cmd`), `/full-thinking` (`full_thinking_cmd`), `/summarize` (`summarize_cmd`), `/imagine` (`imagine_cmd`), `/cek-tool` (`cek_tool_cmd`).
   * Event `on_ready()`, `on_message()`, dan entrypoint `main()`.

---

## 4. Fitur Berjalan vs Riwayat Kegagalan & Audit

### A. Fitur yang Sudah Berjalan
* **Startup & Koneksi Cloudflare Multi-Akun:** Log konsol menampilkan `[ONLINE] Clouzer AI Bot aktif (Cloudflare AI: OK (3 akun) | Gen: @cf/leonardo/lucid-origin | Edit: @cf/black-forest-labs/flux-2-klein-4b | Audio: @cf/openai/whisper-large-v3-turbo | ...)`
* **Percakapan Teks & Streaming di Channel Diizinkan:** Pesan teks biasa di `#general` dibalas dengan *streaming* SSE dan dilengkapi tombol `Regenerate` serta `Kirim Teks via DM`.
* **Transkripsi Audio `.aac`:** Pengiriman file `.aac` lewat *attachment* Discord berhasil ditranskripsi oleh Cloudflare Whisper dan dianalisis oleh `gpt-6.1-sol`.

### B. Masalah yang Pernah Gagal & Penyebabnya
1. **Error Audio Awal (`HTTP 404` & `CF_ACCOUNTS_JSON belum siap`):**
   * *Penyebab:* Sebelum menggunakan Cloudflare Workers AI, audio diarahkan ke endpoint Xingchi yang mengembalikan `HTTP 404`. Saat migrasi ke Cloudflare Whisper, nilai `CF_ACCOUNTS_JSON` di `/.env` sempat kosong/hilang sehingga status startup menjadi `Cloudflare AI: MISSING`.
   * *Perbaikan:* Mengisi ulang seluruh variabel wajib & Cloudflare di `/.env` Wispbyte dengan JSON array 1 baris valid dan me-restart server.
2. **Insiden Terhapusnya Isi `/.env` di Panel Wispbyte:**
   * *Penyebab:* Tab **Environment** di Wispbyte menyunting file `/.env` secara langsung. Menghapus baris variabel di tab tersebut menghapus seluruh konfigurasi saat disimpan.
   * *Perbaikan:* Pengisian ulang variabel inti (`DISCORD_TOKEN`, `XINGCHI_API_KEY`, `XINGCHI_BASE_URL`, `XINGCHI_MODEL`, `CF_ACCOUNTS_JSON`, `CF_AUDIO_MODEL`).
3. **Slash Commands (`/channel`, `/fast`) Muncul *"The application did not respond"* Tanpa Log Error:**
   * *Gejala:* Chat teks biasa berjalan normal, namun `/channel` dan `/fast` memunculkan *"The application did not respond"* (`Only you can see this`) di Discord tanpa *traceback* di konsol Wispbyte.
   * *Patch yang Sudah Diterapkan:* Menambahkan `await interaction.response.defer(ephemeral=True)` pada `channel_perm_cmd` dan memindahkan sinkronisasi command dari `on_ready()` ke `_run_background_sync()` agar tidak memblokir startup.
   * *Catatan Investigasi Lanjutan:* Karena tidak ada log error di konsol saat perintah ditekan, *interaction* tidak menyangkut di kode Python melainkan kemungkinan pada *cache* slash command di klien Discord atau ketidaksesuaian `Application ID` integrasi command di server Discord dengan `DISCORD_TOKEN` yang sedang aktif.

---

## 5. Matriks Status Fakta (Terbukti vs Dikonfirmasi vs Belum Diverifikasi)

| Kategori | Status | Rincian Teknis |
| :--- | :--- | :--- |
| **Fakta Terbukti** *(dari kode, screenshot & log)* | Terbukti | Bot menggunakan `discord.py` + `aiohttp` ke `{XINGCHI_BASE_URL}/responses` (`gpt-6.1-sol`). |
| **Fakta Terbukti** *(dari log startup)* | Terbukti | Bot berhasil `ONLINE` dengan 3 akun Cloudflare (`OK (3 akun)`), model Gen `lucid-origin`, Edit `flux-2-klein-4b`, dan Audio `whisper-large-v3-turbo`. |
| **Fakta Terbukti** *(dari screenshot Wispbyte)* | Terbukti | Panel **Environment** Wispbyte memodifikasi file `/.env` server secara langsung. |
| **Fakta Terbukti** *(dari screenshot Discord)* | Terbukti | Balasan chat biasa di `#general` berfungsi normal dan merender tombol `Regenerate` serta `Kirim Teks via DM`. |
| **Kondisi Dikonfirmasi User** | Dikonfirmasi | Seluruh token, API key, dan 3 kredensial Cloudflare di `/.env` sudah lengkap dan valid. |
| **Kondisi Dikonfirmasi User** | Dikonfirmasi | Pembacaan/transkripsi file audio `.aac` sudah berfungsi. |
| **Kondisi Dikonfirmasi User** | Dikonfirmasi | `/channel` dan `/fast` memunculkan *"The application did not respond"* di Discord sementara konsol Wispbyte tidak mencatat error apa pun. |
| **Belum Diverifikasi** | Belum Dicek | Akar pasti *not responding* pada `/channel` & `/fast` (apakah *cache* klien Discord, konflik `Application ID` bot lain di server, atau berlaku juga untuk `/full-thinking`, `/summarize`, `/imagine`, `/cek-tool`). |
| **Belum Diverifikasi** | Belum Dicek | Nilai **SHA256** eksak dari file `main.py` aktif di container Wispbyte. |
| **Belum Diverifikasi** | Belum Dicek | Stabilitas & batas timeout/RAM untuk audio `.aac` berdurasi sangat panjang / mendekati batas `25 MB`. |
| **Belum Diverifikasi** | Belum Dicek | Ketersediaan library opsional (`pymupdf`, `python-docx`, `python-pptx`, `openpyxl`) di container Wispbyte dan konsumsi harian neuron Cloudflare Workers AI. |

---

## 6. Identitas Versi & Verifikasi SHA256

* **Identitas Versi Kode:** `Clouzer AI Bot — Multi-Format & Cloudflare Audio STT Patch` (berbasis `main (8).py` dengan tambahan `defer(ephemeral=True)` pada `channel_perm_cmd` dan `_run_background_sync()`).
* **Status Hash SHA256:** Hash SHA256 file `main.py` di server Wispbyte **belum dihitung/diverifikasi**. Untuk mencatat sidik jari versi aktif di Wispbyte, jalankan perintah berikut di terminal/console server:
  ```bash
  sha256sum main.py
  ```

---

## 7. Status Deployment Wispbyte & Celah Keamanan

### Status Deployment Saat Ini
* **Runtime:** Berjalan di container Wispbyte (`main.py` online, terhubung ke Discord Gateway, Xingchi API, dan Cloudflare Workers AI).
* **File Persisten di Server:** `main.py`, `/.env` (konfigurasi rahasia), dan `memory.json` (izin channel, mode thread, dan maks 10 histori chat per scope).

### Celah Keamanan & Risiko Operasional yang Perlu Diperhatikan
1. **Penyimpanan Kredensial Plaintext (`/.env`):** `DISCORD_TOKEN`, `XINGCHI_API_KEY`, dan `CF_ACCOUNTS_JSON` (3 `account_id` + `api_token`) tersimpan plaintext di `/.env`. Jangan pernah menampilkan isi `/.env` di tangkapan layar publik atau tiket dukungan.
2. **Risiko Pembacaan File Konfigurasi oleh Bot:** Daftar `FULL_CONTENT_EXTS` mengizinkan bot membaca file `.env`, `.ini`, `.json`, `.yml`, dan `.yaml` yang diunggah ke channel. Jika ada pengguna mengunggah file sensitif ke channel yang diizinkan, isinya akan dikirim ke Xingchi API dan tersimpan di `memory.json`.
3. **Kontrol Kuota API di Channel Publik:** Perintah `/fast`, `/full-thinking`, `/summarize`, `/imagine`, dan `/cek-tool` dapat dipicu oleh siapa saja di dalam `allowed_channels` tanpa *cooldown* per pengguna, sehingga berpotensi menghabiskan kuota Xingchi API atau Cloudflare Workers AI jika dipakai berlebihan.
4. **Penggunaan RAM pada Lampiran Besar:** File audio hingga `25 MB` dan gambar hingga `15 MB` dimuat penuh ke RAM (`await attachment.read()`) dan di-encode ke *base64*. Jika beberapa pengguna mengirim file besar bersamaan di container dengan RAM terbatas, dapat memicu *Out of Memory* (OOM).
5. **View UI Non-Persisten:** `AIResponseView` menggunakan `timeout=600` dan tidak didaftarkan ulang lewat `bot.add_view()`, sehingga tombol `Regenerate` dan `Kirim Teks via DM` pada pesan lama akan berhenti merespons setelah 10 menit atau setelah bot *restart*.

---

## 8. Aturan Mutlak untuk Gemini di Sesi Berikutnya

1. **Dilarang Merombak / Menimpa `main.py` Tanpa Diminta:** Jika pengguna hanya bertanya, berdiskusi, atau meminta analisis, jawab langsung tanpa menulis ulang seluruh file. Jika diminta memperbaiki bug, ubah bagian spesifiknya saja tanpa menghapus fitur yang sudah berjalan.
2. **Dilarang Menyuruh Menghapus Variabel di Tab Environment Wispbyte:** Selalu ingatkan bahwa tab Environment Wispbyte mengedit `/.env` secara langsung; instruksikan perubahan per variabel secara spesifik.
3. **Jangan Ubah Pipeline Audio Cloudflare (`CF_AUDIO_MODEL` & Payload Base64):** Transkripsi `.aac` sudah terbukti jalan memakai `@cf/openai/whisper-large-v3-turbo` dengan payload `{"audio": audio_b64}`. Jangan kembalikan ke endpoint Xingchi `/audio/transcriptions` (terbukti `HTTP 404`).
4. **Pertahankan `build_failure_tool_continuation()` & Isolasi Mode Edit Gambar:** Jangan hapus pengiriman `function_call_output` untuk setiap `function_call` ke `/v1/responses`, dan jangan pernah mengalihkan kegagalan edit gambar (`Klein 4B`) ke generator gambar baru (`Pollinations FLUX`).
5. **Komunikasi Langsung, Teknis & Kontekstual:** Gunakan bahasa santai yang ringkas dan akurat, jangan membalikkan pertanyaan dasar yang jawabannya sudah tercatat di dokumen handoff ini, serta bedakan secara tegas antara fakta yang sudah terbukti dan dugaan yang belum diverifikasi.

---

## 9. HANDOFF AKTIF — Pengujian Pembuatan PDF Berat (28 November 2026)

**Tujuan sesi berikutnya:** Melanjutkan pengujian kemampuan CLOUZER membuat PDF panjang/berat. Ini adalah pekerjaan pengujian yang belum selesai, bukan sekadar riwayat percakapan. Sesi berikutnya wajib membaca bagian ini dan melanjutkan dari langkah berikutnya.

### A. Konteks percobaan terakhir

* Pengguna meminta pengujian PDF berat dan menyetujui pengujian langsung dengan beban besar.
* Prompt uji yang dikirim melalui Discord meminta PDF minimal **100 halaman**.
* CLOUZER membalas: `PDF hanya memiliki 2 halaman; prompt meminta minimal 100.`
* Pengguna menegaskan bahwa lonjakan resource awal saat startup / mulai menjalankan pekerjaan dianggap wajar. Fokus investigasi bukan menyimpulkan OOM hanya karena ada lonjakan sesaat.
* Percobaan berikutnya melalui perintah **Ulangi** menghasilkan kegagalan yang sama. Retry tidak memperbaiki dokumen dan hanya mengulangi proses validasi terhadap hasil 2 halaman.

### B. Bukti log dan interpretasi yang sudah didukung

Data log yang dikirim pengguna:

* Batas memori cgroup: **588.8 MB**.
* Memori cgroup awal sekitar **170 MB**, puncak sampel yang tercatat sekitar **171.8 MB** pada diagnostik awal; kemudian turun bertahap hingga sekitar **132.3 MB**.
* RSS proses sekitar **124.9 MB** pada awal pemantauan dan puncak sekitar **133.4 MB**.
* Diagnostik 131 baris: rata-rata CPU proses sekitar **0.19%**; rata-rata CPU cgroup sekitar **0.95%** pada rentang sampel yang diringkas. Terdapat lonjakan singkat saat pembuatan PDF.
* CPU quota awal tercatat `100000 100000` lalu berubah menjadi `35000 100000` (sekitar 35% dari satu core). Saat pekerjaan PDF berlangsung, CPU proses/cgroup sempat berada di kisaran **30–40%** dan terjadi throttle events. Setelah pekerjaan selesai, pemakaian kembali rendah.
* Tahap validasi PDF sangat cepat: ekstraksi teks 2 halaman **0.010 s**, scan drawing **0.004 s**, span bounds **0.001 s**, table drawing scan **0.001 s**, total validasi **0.131 s**.
* Traceback utama: `create_document_file()` → `validate_document_file()` di `clouzer_file_creation.py`, melempar `ValueError: PDF hanya memiliki 2 halaman; prompt meminta minimal 100.`
* `process_ai_turn()` mencoba ulang sekali setelah `FILE-RETRY`, tetapi percobaan kedua kembali menghasilkan 2 halaman dan ValueError yang sama.

**Kesimpulan sementara yang boleh dipakai:** kegagalan yang terbukti adalah ketidaksesuaian jumlah halaman hasil PDF (2, padahal minimal 100 diminta). Log menunjukkan lonjakan CPU/throttling singkat saat proses file, tetapi tidak menunjukkan bukti OOM atau kehabisan RAM. Jangan menyebut RAM sebagai akar masalah tanpa bukti baru. Validasi PDF sendiri bukan bottleneck yang berarti. Belum diketahui apakah penyebab hasil 2 halaman adalah instruksi/prompt yang tidak cukup mengikat, pemotongan/ketidaklengkapan konten yang dihasilkan model, atau bug pada pipeline pembuatan dokumen.

### C. Lokasi kode dan batas perubahan

* Runtime traceback menunjukkan kandidat aktif yang diuji berada di:
  * `audit-recovery/review-round-2/revisi_audit_final_main.py`
  * `audit-recovery/review-round-2/clouzer_file_creation.py`
* Fungsi relevan:
  * `process_ai_turn()` di file kandidat utama, sekitar baris 5335 dan retry sekitar 5467 menurut traceback.
  * `create_document_file()` di `clouzer_file_creation.py`, sekitar baris 3198.
  * `validate_document_file()` di `clouzer_file_creation.py`, sekitar baris 2554.
* Jangan menganggap nomor baris masih sama sebelum membaca file/branch terkini.
* **Jangan mengubah atau deploy production `main.py`, jangan restart bot, dan jangan mengubah kode apa pun tanpa izin eksplisit pengguna.** Analisis dan usulan pengujian boleh dilakukan terlebih dahulu.

### D. Rencana kerja sesi berikutnya — lanjutkan pengujian, bukan mengulang kegagalan yang sama

1. Baca Master Memory ini terlebih dahulu, lalu periksa branch kandidat dan file `clouzer_file_creation.py` yang benar-benar dipakai pada pengujian. Jangan mengasumsikan branch atau file kandidat tanpa verifikasi.
2. Telusuri alur PDF dari prompt Discord sampai data yang masuk ke `create_document_file()`: bagaimana model diminta membuat konten, bagaimana konten disimpan/diteruskan, dan bagaimana jumlah halaman target ditentukan.
3. Periksa implementasi `validate_document_file()` dan mekanisme retry. Pastikan retry tidak sekadar mengirim ulang input identik yang sudah gagal. Jangan mengubah validator untuk meloloskan PDF 2 halaman sebagai 100 halaman.
4. Rancang uji bertahap yang tetap bermakna: mulai dari PDF 10 halaman, lalu 25, 50, dan akhirnya 100 halaman; gunakan struktur bab, subbab, tabel, dan isi substantif yang jelas agar jumlah halaman tidak tercapai melalui halaman kosong atau pengulangan filler.
5. Untuk setiap uji, catat jumlah halaman aktual, ukuran file, waktu pembuatan, waktu validasi, CPU/RSS/cgroup memory, throttle events, serta apakah hasil lolos pemeriksaan isi dan tata letak.
6. Jika perlu menguji ulang target 100 halaman secara langsung, gunakan prompt Discord yang eksplisit meminta dokumen panjang dengan pembagian bab dan target isi per bab. Namun jangan menganggap prompt yang lebih panjang otomatis menyelesaikan masalah; verifikasi output PDF secara objektif.
7. Setelah akar masalah ditemukan, laporkan bukti dan usulan patch terlebih dahulu. Tunggu izin pengguna sebelum mengubah file atau deployment.

### E. Status pekerjaan

* **Belum lulus:** PDF minimal 100 halaman.
* **Hasil terakhir:** 2 halaman; ditolak validator.
* **Retry:** gagal dengan error identik.
* **RAM/OOM:** tidak terbukti sebagai penyebab.
* **CPU:** ada lonjakan dan throttling singkat ketika pekerjaan PDF berlangsung; setelahnya kembali rendah.
* **Akar masalah pipeline:** belum ditemukan.
* **Langkah aktif berikutnya:** audit alur prompt → konten dokumen → pembuatan PDF → validasi → retry, lalu jalankan pengujian bertahap.
