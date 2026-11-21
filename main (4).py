import os
import io
import re
import json
import time
import logging
import base64
import random
import urllib.parse
import ipaddress
import socket
import asyncio
import math
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from collections import defaultdict, OrderedDict
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from PIL import Image
from dotenv import load_dotenv

try:
    import pymupdf as fitz  # PyMuPDF untuk baca teks & render halaman PDF
except ImportError:
    fitz = None

try:
    import docx  # python-docx untuk baca file .docx
except ImportError:
    docx = None

try:
    from pptx import Presentation  # python-pptx untuk baca file .pptx
except ImportError:
    Presentation = None

try:
    import openpyxl  # openpyxl untuk baca file .xlsx
except ImportError:
    openpyxl = None

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
XINGCHI_API_KEY = os.getenv("XINGCHI_API_KEY")
XINGCHI_BASE_URL = os.getenv("XINGCHI_BASE_URL", "https://api.xingchiapi.com/v1").rstrip("/")
XINGCHI_MODEL = os.getenv("XINGCHI_MODEL", "gpt-6.1-sol")
XINGCHI_AUDIO_MODEL = os.getenv("XINGCHI_AUDIO_MODEL", "whisper-1").strip()

CF_COOLDOWN_SECONDS = float(os.getenv("CF_COOLDOWN_SECONDS", "45"))
IMAGE_CONTEXT_TTL_SECONDS = float(os.getenv("IMAGE_CONTEXT_TTL_SECONDS", "1800"))
IMAGE_CONTEXT_MAX_ENTRIES = int(os.getenv("IMAGE_CONTEXT_MAX_ENTRIES", "30"))
MAX_OUTPUT_IMAGE_BYTES = int(os.getenv("MAX_OUTPUT_IMAGE_BYTES", str(12 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", str(25_000_000)))
DISCORD_MSG_CHUNK_LIMIT = min(1990, max(500, int(os.getenv("DISCORD_MSG_CHUNK_LIMIT", "1900"))))
MEMORY_PROMPT_MAX_LEN = int(os.getenv("MEMORY_PROMPT_MAX_LEN", "160"))
MAX_VISION_IMAGES_PER_TURN = 5
# Defensive limits for ZIP-based office/e-book formats. These cap decompressed
# work, not just the uploaded compressed size.
ZIP_MAX_ENTRIES = 2000
ZIP_MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024
ZIP_MAX_ENTRY_UNCOMPRESSED = 25 * 1024 * 1024
ZIP_MAX_COMPRESSION_RATIO = 200

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

TEXT_EDIT_NOTE = (
    "Catatan: untuk revisi teks atau huruf yang butuh presisi tinggi, "
    "lebih aman dirapikan manual di **Canva** atau **Photopea**."
)

DATA_URL_BASE64_RE = re.compile(r"data:[a-zA-Z0-9.+/-]+;base64,[A-Za-z0-9+/=]+")
LONG_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{256,}(?![A-Za-z0-9+/=])")
EXPLICIT_RATIO_RE = re.compile(r"\b(16:9|9:16|4:3|3:4|3:2|2:3|4:5|5:4|21:9|1:1)\b", re.IGNORECASE)

ASPECT_RATIO_DIMENSIONS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
    "3:2": (960, 640),
    "2:3": (640, 960),
    "4:5": (768, 960),
    "5:4": (960, 768),
    "21:9": (1024, 448),
}


class _SimpleHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str):
        cleaned = re.sub(r"\s+", " ", data).strip()
        if cleaned:
            self._chunks.append(cleaned)

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def normalize_cf_json_str(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        s = s[1:-1].strip()
    if '\\"' in s and not s.startswith("["):
        s = s.replace('\\"', '"')
    elif '\\"' in s and s.startswith("["):
        try:
            json.loads(s)
        except Exception:
            s = s.replace('\\"', '"')
    return s


def load_cf_accounts() -> list[dict[str, str]]:
    raw_json = normalize_cf_json_str(os.getenv("CF_ACCOUNTS_JSON", ""))
    if not raw_json:
        return []
    try:
        parsed = json.loads(raw_json)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    accounts: list[dict[str, str]] = []
    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            continue
        acc_id = str(item.get("account_id", "")).strip()
        token = str(item.get("api_token", "")).strip()
        label = str(item.get("id") or f"Akun {idx}").strip()[:24]
        if acc_id and token:
            accounts.append({"id": acc_id, "token": token, "label": label})
    return accounts


CF_ACCOUNTS: list[dict[str, str]] = load_cf_accounts()

CF_IMAGE_MODEL = os.getenv("CF_IMAGE_MODEL", "@cf/leonardo/lucid-origin").strip()
CF_EDIT_MODEL = os.getenv("CF_EDIT_MODEL", "@cf/black-forest-labs/flux-2-klein-4b").strip()
CF_AUDIO_MODEL = os.getenv("CF_AUDIO_MODEL", "@cf/openai/whisper-large-v3-turbo").strip()

MEMORY_FILE = os.path.join(BASE_DIR, "memory.json")
MAX_RECENT_MESSAGES = 10
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".webm", ".aac", ".opus")

OFFICE_DOC_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".rtf",
    ".odt", ".ods", ".odp", ".epub",
    ".svg", ".eps", ".ai",
}

ALL_SUPPORTED_EXTS = {
    ".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv", ".md",
    ".xml", ".yml", ".yaml", ".ini", ".env", ".log", ".sql", ".sh",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".webm", ".aac", ".opus",
} | OFFICE_DOC_EXTS

FULL_CONTENT_EXTS = {
    ".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv", ".md",
    ".xml", ".yml", ".yaml", ".ini", ".env", ".log", ".sql", ".sh",
} | OFFICE_DOC_EXTS

EDIT_INTENT_KEYWORDS = (
    "ganti", "ubah", "edit", "hapus", "tambah", "replace", "change",
    "recolor", "warnai", "mewarnai", "perbaiki", "koreksi", "retouch", "modif",
    "rasio", "aspect ratio", "perlebar", "perpanjang", "crop"
)

CREATE_INTENT_KEYWORDS = (
    "buatkan", "bikin", "buat", "lukis", "draw",
    "generate", "create", "render"
)

ANALYSIS_ONLY_KEYWORDS = (
    "jelaskan", "analisis", "analisa", "ringkas", "baca", "arti", "makna",
    "apa ini", "ini apa", "ada apa", "teks apa", "tulisan apa", "berapa", "kenapa",
    "mengapa", "bagaimana", "summarize", "explain", "read", "ocr", "transkrip",
    "menurut lu", "menurut kamu", "menurutmu", "terlihat apa", "kelihatan apa",
    "isinya apa", "maksudnya", "gimana maksud", "describe", "what is in"
)

BASE_SYSTEM_PROMPT = f"""Kamu adalah OpenAI GPT 6.1 SOL, asisten AI cerdas di Discord yang berjalan di Clouzer AI.

IDENTITAS MUTLAK (WAJIB DIPATUHI TANPA PENGECUALIAN):
- Nama & Model Resmi: OpenAI GPT 6.1 SOL (Model ID: {XINGCHI_MODEL})
- Penyedia API / Endpoint: xingchiapi.com (Xingchi API)
- Platform: Clouzer AI
- Pembuat / Pengembang: clouzerd

ATURAN MUTLAK PENELUSURAN WEB & JAWABAN (ANTI-DENIAL):
1. Kamu memiliki akses penelusuran web langsung (web_search). Kamu DILARANG KERAS menolak menjawab, DILARANG bilang "tidak punya akses kalender", "tidak punya akses web", atau "tidak bisa mengecek jadwal".
2. Jika pengguna menanyakan informasi apa pun yang bersifat terkini (jadwal bola, liga, skor, berita, cuaca, kurs, harga, event, tanggal, dll), gunakan tool web_search untuk mencari data terbarunya dan cantumkan selengkap-lengkapnya ke pengguna!
3. PEMBUATAN & PENGEDITAN GAMBAR (`generate_image`):
  - Jika pengguna meminta dibuatkan ilustrasi/foto baru dari nol, panggil `generate_image` dengan `use_attached_image: false` dan tulis `prompt` deskripsi visual detail berbahasa Inggris (panjang lega sekitar 300–900 karakter, aman dari konten NSFW/gore eksplisit).
  - Jika pengguna melampirkan gambar (atau merujuk gambar yang baru dikirim) lalu meminta MENGEDIT, MENGUBAH TEKS, MENGGANTI WARNA, MENGUBAH ASPEK RASIO, atau MEMODIFIKASI gambar tersebut, wajib panggil `generate_image` dengan `use_attached_image: true` dan tulis instruksi edit yang jelas serta presisi di `prompt`.
  - Isi parameter `aspect_ratio` sesuai permintaan pengguna (`1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`, `4:5`, `5:4`, `21:9`), atau isi `auto` jika pengguna tidak menyebutkan rasio khusus (saat edit akan otomatis mengikuti rasio gambar asli, saat buat baru akan default ke `1:1`).
  - Jika pengguna hanya bertanya atau minta dijelaskan isi gambar/PDF/dokumen/presentasi/spreadsheet/desain/audio tanpa minta dibuatkan atau diedit gambarnya, JANGAN panggil `generate_image`.
  - Saat menyertai gambar dengan teks, sampaikan ringkasannya secara singkat dan santai tanpa menjelaskan proses penyusunan prompt.

ATURAN GAYA BAHASA:
- Gunakan bahasa gaul, santai, natural, dan tidak terlalu baku layaknya teman ngobrol yang pintar.
- Jangan pernah menyapa kosong jika user mengirim file, dokumen, slide, spreadsheet, file desain, atau audio; langsung baca teks atau transkripnya dan bahas isinya!"""


def format_memory_image_summary(engine_used: str, prompt: str, max_len: int = MEMORY_PROMPT_MAX_LEN) -> str:
    compact = re.sub(r"\s+", " ", (prompt or "").strip())
    if len(compact) > max_len:
        compact = compact[:max_len].rstrip() + "..."
    return f"[Gambar diproses ({engine_used}): {compact}]"


def normalize_aspect_ratio_str(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = str(raw).strip().lower().replace("x", ":").replace("/", ":")
    if cleaned in ASPECT_RATIO_DIMENSIONS:
        return cleaned
    return None


def extract_aspect_ratio_from_text(text: str) -> str | None:
    if not text:
        return None
    m = EXPLICIT_RATIO_RE.search(text)
    if m:
        return m.group(1)
    low = text.lower()
    if any(k in low for k in ("widescreen", "layar lebar", "thumbnail youtube", "memanjang ke samping", "memanjang kesamping")):
        return "16:9"
    if any(k in low for k in ("ultrawide", "sinematik lebar")):
        return "21:9"
    if any(k in low for k in ("wallpaper hp", "story ig", "instagram story", "reels", "tiktok", "vertikal", "memanjang ke atas", "memanjang keatas")):
        return "9:16"
    return None


def resolve_image_dimensions(
    aspect_ratio_hint: str | None = None,
    prompt_text: str = "",
    source_images: list[str] | None = None,
) -> tuple[int, int, str]:
    normalized_hint = normalize_aspect_ratio_str(aspect_ratio_hint)
    if normalized_hint:
        w, h = ASPECT_RATIO_DIMENSIONS[normalized_hint]
        return w, h, normalized_hint

    detected = extract_aspect_ratio_from_text(prompt_text)
    if detected and detected in ASPECT_RATIO_DIMENSIONS:
        w, h = ASPECT_RATIO_DIMENSIONS[detected]
        return w, h, detected

    if source_images:
        try:
            _, b64_data = parse_data_url(source_images[0])
            raw_bytes = base64.b64decode(b64_data, validate=True)
            with Image.open(io.BytesIO(raw_bytes)) as img:
                orig_w, orig_h = img.size
            if orig_w > 0 and orig_h > 0:
                scale = 1024.0 / float(max(orig_w, orig_h))
                target_w = max(448, min(1024, int(round((orig_w * scale) / 16.0)) * 16))
                target_h = max(448, min(1024, int(round((orig_h * scale) / 16.0)) * 16))
                for label, (pw, ph) in ASPECT_RATIO_DIMENSIONS.items():
                    if abs((target_w / target_h) - (pw / ph)) < 0.03:
                        return pw, ph, label
                return target_w, target_h, f"{target_w}:{target_h}"
        except Exception:
            pass

    return 1024, 1024, "1:1"


def should_allow_image_tool(user_text: str, has_file_or_pdf: bool, has_image: bool) -> bool:
    low = (user_text or "").lower().strip()
    has_edit = any(k in low for k in EDIT_INTENT_KEYWORDS)
    has_create = any(k in low for k in CREATE_INTENT_KEYWORDS)
    has_analysis = any(k in low for k in ANALYSIS_ONLY_KEYWORDS)

    if has_edit or has_create:
        return True
    if has_file_or_pdf and not has_edit and not has_create:
        return False
    if has_image and has_analysis and not has_edit and not has_create:
        return False
    return True


def scrub_base64_text(text: str) -> str:
    if not text:
        return ""
    cleaned = DATA_URL_BASE64_RE.sub("[data-url-base64-dihapus]", text)
    cleaned = LONG_BASE64_RE.sub("[base64-dihapus]", cleaned)
    return cleaned


def scrub_nested_base64(value):
    if isinstance(value, str):
        return scrub_base64_text(value)
    if isinstance(value, list):
        return [scrub_nested_base64(item) for item in value]
    if isinstance(value, dict):
        return {str(k): scrub_nested_base64(v) for k, v in value.items()}
    return value


def clean_response_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\ue200.*?\ue201", "", text, flags=re.DOTALL)
    text = re.sub(r"\ue200[^\ue201]*$", "", text)
    text = re.sub(r"[\ue200-\ue20f]", "", text)
    text = re.sub(r" +([.,!?;:])", r"\1", text)
    return text


def split_discord_chunks(text: str, limit: int = DISCORD_MSG_CHUNK_LIMIT) -> list[str]:
    safe_limit = min(1990, max(100, limit))
    text = text.strip()
    if len(text) <= safe_limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > safe_limit:
        slice_part = remaining[:safe_limit]
        split_idx = slice_part.rfind("\n\n")
        if split_idx < safe_limit // 2:
            split_idx = slice_part.rfind("\n")
        if split_idx < safe_limit // 2:
            split_idx = slice_part.rfind(" ")
        if split_idx <= 0:
            split_idx = safe_limit

        chunks.append(remaining[:split_idx].rstrip())
        remaining = remaining[split_idx:].lstrip()

    if remaining:
        chunks.append(remaining)
    return chunks


def parse_data_url(data_url: str) -> tuple[str, str]:
    if data_url.startswith("data:") and ";base64," in data_url:
        header, b64_data = data_url.split(";base64,", 1)
        mime_type = header.replace("data:", "").strip() or "image/jpeg"
        return mime_type, b64_data
    return "image/jpeg", data_url


async def read_limited_stream(content, max_bytes: int, chunk_size: int = 65536) -> bytes:
    """Read an aiohttp stream with a hard memory ceiling."""
    if max_bytes < 0 or chunk_size <= 0:
        raise ValueError("Batas ukuran stream tidak valid")
    data = bytearray()
    async for chunk in content.iter_chunked(min(chunk_size, max(1, max_bytes + 1))):
        if len(data) + len(chunk) > max_bytes:
            raise ValueError(f"Respons melewati batas {max_bytes} byte")
        data.extend(chunk)
    return bytes(data)


def sanitize_image_prompt(prompt: str, max_len: int = 1500) -> str:
    cleaned = re.sub(r"\s+", " ", prompt).strip()
    return cleaned[:max_len].strip()


def validate_and_normalize_image_bytes(raw_bytes: bytes) -> tuple[bytes, str, str]:
    if not raw_bytes:
        raise RuntimeError("Data gambar kosong (0 byte)")
    if len(raw_bytes) > MAX_OUTPUT_IMAGE_BYTES:
        max_mb = MAX_OUTPUT_IMAGE_BYTES / (1024 * 1024)
        raise RuntimeError(f"Ukuran gambar melebihi batas maksimal ({max_mb:.0f} MB)")

    try:
        with Image.open(io.BytesIO(raw_bytes)) as probe:
            probe.verify()
            fmt = (probe.format or "").upper()
            width, height = probe.size
    except Exception as exc:
        raise RuntimeError(f"File gambar rusak atau format tidak dikenali: {exc}") from exc

    if width <= 0 or height <= 0 or (width * height) > MAX_IMAGE_PIXELS:
        raise RuntimeError(f"Dimensi gambar tidak valid ({width}x{height})")

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()
            if fmt in ("PNG", "JPEG", "WEBP", "GIF"):
                ext = "jpg" if fmt == "JPEG" else fmt.lower()
                mime = f"image/{'jpeg' if fmt == 'JPEG' else fmt.lower()}"
                out_buf = io.BytesIO()
                save_img = img.convert("RGB") if fmt == "JPEG" else img
                save_img.save(out_buf, format=fmt)
                normalized = out_buf.getvalue()
                if len(normalized) > MAX_OUTPUT_IMAGE_BYTES:
                    raise RuntimeError("Hasil normalisasi gambar melewati batas ukuran output.")
                return normalized, f"clouzer_image.{ext}", mime

            png_buf = io.BytesIO()
            img.convert("RGBA").save(png_buf, format="PNG")
            normalized = png_buf.getvalue()
            if len(normalized) > MAX_OUTPUT_IMAGE_BYTES:
                raise RuntimeError("Hasil normalisasi gambar melewati batas ukuran output.")
            return normalized, "clouzer_image.png", "image/png"
    except Exception as exc:
        raise RuntimeError(f"Gagal memverifikasi/menyiapkan gambar: {exc}") from exc


def build_failure_tool_continuation(calls: list[dict], reason: str) -> list[dict]:
    items: list[dict] = []
    for fc in calls:
        if not isinstance(fc, dict):
            continue
        call_id = str(fc.get("call_id") or "").strip()
        if call_id:
            items.append(fc)
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({
                    "success": False,
                    "reason": reason,
                }, ensure_ascii=False),
            })
    return items


class AIResponseView(discord.ui.View):
    def __init__(
        self,
        bot_instance,
        original_messages: list,
        mode: str,
        *,
        source_images: list[str] | None = None,
        user_prompt_text: str = "",
        allow_image_tool: bool = True,
        owner_id: int,
    ):
        super().__init__(timeout=600)
        self.bot_instance = bot_instance
        self.owner_id = int(owner_id)
        self.original_messages = original_messages
        self.mode = mode
        self.source_images = list(source_images or [])
        self.user_prompt_text = user_prompt_text
        self.allow_image_tool = allow_image_tool

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⚠️ Tombol ini hanya bisa digunakan oleh pengguna yang memulai percakapan tersebut.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="\U0001f504 Regenerate", style=discord.ButtonStyle.secondary, custom_id="ai_regenerate")
    async def regenerate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⚠️ Tombol ini hanya bisa digunakan oleh pengguna yang memulai percakapan tersebut.",
                ephemeral=True,
            )
            return
        try:
            await interaction.response.edit_message(content="🔄 *Sedang menyusun ulang jawaban...*", view=self)
        except Exception:
            try:
                await interaction.response.defer()
            except Exception:
                pass

        scope_key = self.bot_instance.get_scope_key(
            interaction.guild, interaction.user.id, interaction.channel_id
        )
        guild_id = interaction.guild.id if interaction.guild is not None else 0
        channel_id = interaction.channel_id or 0
        user_id = interaction.user.id

        try:
            async with self.bot_instance.guild_locks[scope_key]:
                new_reply, function_calls = await self.bot_instance.run_responses_ai(
                    self.original_messages,
                    interaction.message,
                    self.mode,
                    allow_tools=True,
                    allow_image_tool=self.allow_image_tool,
                )

                img_calls = [fc for fc in function_calls if fc.get("name") == "generate_image"]
                unsupported_calls = [fc for fc in function_calls if fc.get("name") != "generate_image"]
                if unsupported_calls:
                    rejected_items = build_failure_tool_continuation(
                        function_calls,
                        "Function tool tidak didukung oleh bot ini. Tool yang tersedia hanya generate_image; web_search ditangani provider.",
                    )
                    if rejected_items:
                        try:
                            follow_text, _ = await self.bot_instance.run_responses_ai(
                                self.original_messages,
                                interaction.message,
                                mode=self.mode,
                                allow_tools=False,
                                allow_image_tool=False,
                                extra_input=rejected_items,
                            )
                            if follow_text:
                                new_reply = follow_text
                        except Exception as exc:
                            logging.exception("Continuation unsupported tool gagal saat Regenerate (%s)", scope_key)
                            new_reply = f"⚠️ Tool dari model tidak didukung dan kelanjutan gagal: {type(exc).__name__}."
                    function_calls = []
                    img_calls = []

                if img_calls and not self.allow_image_tool:
                    blocked_items = build_failure_tool_continuation(
                        img_calls,
                        "Pengguna hanya meminta analisis/penjelasan, bukan pembuatan atau pengeditan gambar.",
                    )
                    if blocked_items:
                        try:
                            blocked_text, _ = await self.bot_instance.run_responses_ai(
                                self.original_messages,
                                interaction.message,
                                mode=self.mode,
                                allow_tools=False,
                                allow_image_tool=False,
                                extra_input=blocked_items,
                            )
                            if blocked_text:
                                new_reply = blocked_text
                        except Exception:
                            logging.exception("Continuation blocked image call gagal saat Regenerate (%s)", scope_key)
                    img_calls = []

                if img_calls and self.allow_image_tool:
                    if self.bot_instance.is_user_image_busy(user_id) and interaction.message is not None:
                        try:
                            await interaction.message.edit(content="⏳ *Menunggu antrean proses gambar sebelumnya selesai...*", view=self)
                        except Exception:
                            pass

                    async with self.bot_instance.user_image_queue(user_id):
                        unresolved_img_calls = list(img_calls)
                        is_edit = False
                        try:
                            primary_call = img_calls[0]
                            extra_calls = img_calls[1:]

                            try:
                                args_dict = json.loads(primary_call.get("arguments", "{}"))
                            except Exception:
                                args_dict = {}

                            img_prompt = (args_dict.get("prompt") or self.user_prompt_text or "Create a detailed image").strip()
                            use_attached = bool(args_dict.get("use_attached_image", False))
                            req_ratio = args_dict.get("aspect_ratio") or extract_aspect_ratio_from_text(self.user_prompt_text)
                            wants_edit = use_attached or any(k in (self.user_prompt_text or "").lower() for k in EDIT_INTENT_KEYWORDS)

                            source_imgs = (
                                self.source_images
                                or self.bot_instance.get_last_user_image(guild_id, channel_id, user_id)
                            ) if (use_attached or wants_edit) else None

                            if wants_edit and not source_imgs:
                                fail_items = build_failure_tool_continuation(
                                    unresolved_img_calls, "Gambar sumber untuk edit tidak ditemukan."
                                )
                                unresolved_img_calls = []
                                if fail_items:
                                    try:
                                        follow_text, _ = await self.bot_instance.run_responses_ai(
                                            self.original_messages,
                                            interaction.message,
                                            mode=self.mode,
                                            allow_tools=False,
                                            allow_image_tool=False,
                                            extra_input=fail_items,
                                        )
                                        if follow_text:
                                            new_reply = follow_text
                                    except Exception:
                                        logging.exception("Continuation missing source image gagal saat Regenerate (%s)", scope_key)
                                err_content = "⚠️ Regenerate edit gambar gagal: gambar sumber tidak ditemukan."
                                if interaction.message is not None:
                                    await interaction.message.edit(content=err_content, view=self)
                                else:
                                    await interaction.edit_original_response(content=err_content, view=self)
                                return

                            is_edit = bool(source_imgs)
                            if interaction.message is not None:
                                await interaction.message.edit(
                                    content="🖌️ *Sedang mengedit ulang gambar...*" if is_edit else "🎨 *Sedang membuat ulang gambar...*",
                                    view=self,
                                )

                            img_bytes, out_filename, out_mime, engine_used = await self.bot_instance.generate_or_edit_image(
                                img_prompt, source_images=source_imgs, aspect_ratio=req_ratio
                            )

                            continuation_items: list[dict] = []
                            p_call_id = primary_call.get("call_id")
                            if p_call_id:
                                continuation_items.append(primary_call)
                                continuation_items.append({
                                    "type": "function_call_output",
                                    "call_id": p_call_id,
                                    "output": json.dumps({
                                        "success": True,
                                        "operation": "edit_image" if is_edit else "generate_image",
                                        "engine": engine_used,
                                        "filename": out_filename,
                                    }, ensure_ascii=False),
                                })

                            for extra_fc in extra_calls:
                                e_call_id = extra_fc.get("call_id")
                                if e_call_id:
                                    continuation_items.append(extra_fc)
                                    continuation_items.append({
                                        "type": "function_call_output",
                                        "call_id": e_call_id,
                                        "output": json.dumps({
                                            "success": False,
                                            "skipped": True,
                                            "reason": "Hanya satu panggilan gambar utama yang diproses per giliran."
                                        }, ensure_ascii=False),
                                    })

                            unresolved_img_calls = []
                            if continuation_items:
                                try:
                                    follow_text, _ = await self.bot_instance.run_responses_ai(
                                        self.original_messages,
                                        interaction.message,
                                        mode=self.mode,
                                        allow_tools=False,
                                        allow_image_tool=False,
                                        extra_input=continuation_items,
                                    )
                                    if follow_text:
                                        new_reply = follow_text
                                except Exception:
                                    logging.exception("Continuation sukses gambar gagal saat Regenerate (%s)", scope_key)

                            if channel_id:
                                context_image = await asyncio.to_thread(
                                    self.bot_instance._compress_image_bytes_to_data_url, img_bytes
                                )
                                self.bot_instance.set_last_user_image(
                                    guild_id, channel_id, user_id, [context_image]
                                )

                            file = discord.File(fp=io.BytesIO(img_bytes), filename=out_filename)
                            embed_title = "🖌️ Clouzer AI Image Editor" if is_edit else "🎨 Clouzer AI Image Generator"
                            embed_desc = f"**Prompt:** {img_prompt[:3600]}"
                            if is_edit:
                                embed_desc = f"{embed_desc[:3450]}\n\n{TEXT_EDIT_NOTE}"

                            embed = discord.Embed(
                                title=embed_title,
                                description=embed_desc,
                                color=discord.Color.blurple(),
                            )
                            embed.set_image(url=f"attachment://{out_filename}")
                            embed.set_footer(text=f"Created by clouzerd | API: {engine_used}")

                            extra_note = f"\n\n{TEXT_EDIT_NOTE}" if is_edit else ""
                            combined = f"**Hasil Regenerate:**\n\n{(new_reply or '').strip()}{extra_note}".strip()
                            final_content = combined[:DISCORD_MSG_CHUNK_LIMIT] if combined else None

                            if interaction.message is not None:
                                await interaction.message.edit(content=final_content, embed=embed, attachments=[file], view=self)
                            else:
                                await interaction.edit_original_response(content=final_content, embed=embed, attachments=[file], view=self)
                            return
                        except Exception as img_exc:
                            if unresolved_img_calls:
                                fail_items = build_failure_tool_continuation(
                                    unresolved_img_calls, f"Proses gambar gagal: {img_exc}"
                                )
                                unresolved_img_calls = []
                                if fail_items:
                                    try:
                                        follow_text, _ = await self.bot_instance.run_responses_ai(
                                            self.original_messages,
                                            interaction.message,
                                            mode=self.mode,
                                            allow_tools=False,
                                            allow_image_tool=False,
                                            extra_input=fail_items,
                                        )
                                        if follow_text:
                                            new_reply = follow_text
                                    except Exception:
                                        logging.exception("Continuation exception gambar gagal saat Regenerate (%s)", scope_key)
                            if is_edit:
                                err_content = f"⚠️ Regenerate edit gambar gagal ({img_exc})."[:1990]
                                if interaction.message is not None:
                                    await interaction.message.edit(content=err_content, view=self)
                                else:
                                    await interaction.edit_original_response(content=err_content, view=self)
                                return
                            raise

            final_content = f"**Hasil Regenerate:**\n\n{(new_reply or 'Tidak ada respons.')}"[:DISCORD_MSG_CHUNK_LIMIT]
            if interaction.message is not None:
                await interaction.message.edit(content=final_content, view=self)
            else:
                await interaction.edit_original_response(content=final_content, view=self)
        except Exception as exc:
            logging.exception("Regenerate gagal untuk scope %s", scope_key)
            err_content = f"⚠️ Regenerate gagal: `{type(exc).__name__}: {exc}`"[:1990]
            if interaction.message is not None:
                await interaction.message.edit(content=err_content, view=self)
            else:
                await interaction.edit_original_response(content=err_content, view=self)

    @discord.ui.button(label="\U0001f4cb Kirim Teks via DM", style=discord.ButtonStyle.primary, custom_id="ai_dm_text")
    async def dm_text_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⚠️ Tombol ini hanya bisa digunakan oleh pengguna yang memulai percakapan tersebut.",
                ephemeral=True,
            )
            return
        try:
            msg_content = interaction.message.content
            if not msg_content and interaction.message.embeds:
                msg_content = interaction.message.embeds[0].description or "(Embed Gambar)"
            await interaction.user.send(f"📥 **Salinan Teks Bot:**\n\n{scrub_base64_text(msg_content or '')}"[:1990])
            await interaction.response.send_message("✅ Berhasil dikirim ke DM lu!", ephemeral=True)
        except Exception:
            await interaction.response.send_message("⚠️ Gagal kirim DM. Pastikan DM Discord lu tidak dikunci!", ephemeral=True)


class ClouzerBot(commands.Bot):
    _EMBEDDED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession | None = None
        self.guild_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.last_channel_images: OrderedDict[tuple[int, int, int], tuple[float, list[str]]] = OrderedDict()
        self.cf_rr_index: int = 0
        self.cf_cooldowns: dict[str, float] = {}
        self.image_busy_users: set[int] = set()
        self.user_image_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.memory_lock = asyncio.Lock()
        self.last_memory_save_error: str | None = None
        self._sync_task: asyncio.Task | None = None
        self.memory_data = self.load_memory()

    def is_user_image_busy(self, user_id: int) -> bool:
        return user_id in self.image_busy_users

    def user_image_queue(self, user_id: int):
        class _QueueCtx:
            def __init__(self, bot_ref: "ClouzerBot", uid: int):
                self.bot_ref = bot_ref
                self.uid = uid
                self.lock = bot_ref.user_image_locks[uid]

            async def __aenter__(self):
                acquired = False
                try:
                    await self.lock.acquire()
                    acquired = True
                    # Set mutation is atomic on the event loop; no second await
                    # is needed after acquiring the per-user lock.
                    self.bot_ref.image_busy_users.add(self.uid)
                    return self
                except BaseException:
                    if acquired and self.lock.locked():
                        self.lock.release()
                    raise

            async def __aexit__(self, exc_type, exc, tb):
                # Keep cleanup synchronous so cancellation cannot strand either
                # the busy marker or the per-user lock.
                self.bot_ref.image_busy_users.discard(self.uid)
                if self.lock.locked():
                    self.lock.release()

        return _QueueCtx(self, user_id)

    def _cf_account_key(self, acc: dict[str, str]) -> str:
        return f"{acc.get('id', '')}:{acc.get('label', '')}"

    def _cleanup_channel_images(self) -> None:
        now = time.monotonic()
        expired = [
            k for k, (ts, _) in self.last_channel_images.items()
            if (now - ts) > IMAGE_CONTEXT_TTL_SECONDS
        ]
        for k in expired:
            self.last_channel_images.pop(k, None)
        while len(self.last_channel_images) > max(1, IMAGE_CONTEXT_MAX_ENTRIES):
            self.last_channel_images.popitem(last=False)

    def set_last_user_image(self, guild_id: int, channel_id: int, user_id: int, images: list[str]) -> None:
        if not images or not channel_id:
            return
        self._cleanup_channel_images()
        key = (guild_id, channel_id, user_id)
        self.last_channel_images[key] = (time.monotonic(), images)
        self.last_channel_images.move_to_end(key, last=True)
        self._cleanup_channel_images()

    def get_last_user_image(self, guild_id: int, channel_id: int, user_id: int) -> list[str] | None:
        if not channel_id:
            return None
        self._cleanup_channel_images()
        key = (guild_id, channel_id, user_id)
        item = self.last_channel_images.get(key)
        if item is None:
            return None
        self.last_channel_images.move_to_end(key, last=True)
        return item[1]

    def _get_rotated_cf_accounts(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        total = len(CF_ACCOUNTS)
        if total == 0:
            return [], []
        start = self.cf_rr_index % total
        self.cf_rr_index = (self.cf_rr_index + 1) % total
        rotated = CF_ACCOUNTS[start:] + CF_ACCOUNTS[:start]
        now = time.monotonic()
        ready = [acc for acc in rotated if now >= self.cf_cooldowns.get(self._cf_account_key(acc), 0.0)]
        cooling = [acc for acc in rotated if now < self.cf_cooldowns.get(self._cf_account_key(acc), 0.0)]
        return ready, cooling

    def _normalize_scope_state(self, raw_state: object) -> dict:
        base = {
            "allowed_channels": [],
            "active_threads": [],
            "auto_channels": [],
            "thread_modes": {},
            "summary": "",
            "messages": [],
            "turn_count": 0,
            "users": {},
        }
        if not isinstance(raw_state, dict):
            return base

        merged = dict(base)
        for k in ["allowed_channels", "active_threads", "auto_channels", "messages"]:
            val = raw_state.get(k)
            if isinstance(val, list):
                merged[k] = scrub_nested_base64(val)

        for k in ["thread_modes", "users"]:
            val = raw_state.get(k)
            if isinstance(val, dict):
                merged[k] = scrub_nested_base64(val)

        # Memory files may outlive code versions or be edited/corrupted. Keep
        # only supported chat roles and well-formed text/image content blocks.
        safe_messages = []
        for message in merged.get("messages", []):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                safe_content = scrub_base64_text(content)
            elif isinstance(content, list):
                safe_content = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text" and isinstance(block.get("text"), str):
                        safe_content.append({"type": "text", "text": scrub_base64_text(block["text"])})
                    elif block_type == "image_url":
                        image_obj = block.get("image_url")
                        image_url = image_obj.get("url") if isinstance(image_obj, dict) else None
                        if isinstance(image_url, str) and image_url.startswith(("https://", "http://", "data:image/")):
                            safe_content.append({"type": "image_url", "image_url": {"url": image_url}})
            else:
                continue
            safe_messages.append({"role": role, "content": safe_content})
        merged["messages"] = safe_messages[-MAX_RECENT_MESSAGES:]

        summary_val = raw_state.get("summary")
        if isinstance(summary_val, str):
            merged["summary"] = scrub_base64_text(summary_val)

        turn_val = raw_state.get("turn_count")
        if isinstance(turn_val, int) and not isinstance(turn_val, bool):
            merged["turn_count"] = max(0, turn_val)

        return merged

    def _sanitize_memory_for_disk(self) -> dict:
        sanitized_guilds: dict = {}
        for scope_key, state in self.memory_data.get("guilds", {}).items():
            copied_state = self._normalize_scope_state(state)
            clean_msgs = []
            for m in copied_state.get("messages", []):
                if not isinstance(m, dict):
                    continue
                content = m.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    img_count = 0
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and block.get("text"):
                            text_parts.append(scrub_base64_text(str(block["text"])))
                        elif block.get("type") == "image_url":
                            img_count += 1
                    merged = "\n".join(text_parts).strip()
                    if img_count:
                        merged = f"{merged}\n[{img_count} gambar dilampirkan]".strip()
                    clean_msgs.append({"role": m.get("role", "user"), "content": scrub_base64_text(merged)})
                else:
                    clean_msgs.append({"role": m.get("role", "user"), "content": scrub_base64_text(str(content))})

            copied_state["messages"] = clean_msgs[-MAX_RECENT_MESSAGES:]
            sanitized_guilds[str(scope_key)] = copied_state
        return {"guilds": sanitized_guilds}

    def load_memory(self) -> dict:
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    raw_guilds = data.get("guilds") if isinstance(data.get("guilds"), dict) else data
                    if isinstance(raw_guilds, dict):
                        migrated: dict = {}
                        for scope_key, st in raw_guilds.items():
                            if str(scope_key) == "guilds" and not isinstance(st, dict):
                                continue
                            migrated[str(scope_key)] = self._normalize_scope_state(st)
                        return {"guilds": migrated}
            except Exception as exc:
                logging.exception("Gagal memuat memory dari %s: %s", MEMORY_FILE, exc)
        return {"guilds": {}}

    async def save_memory(self):
        async with self.memory_lock:
            tmp_file = f"{MEMORY_FILE}.tmp"
            try:
                disk_payload = self._sanitize_memory_for_disk()
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(disk_payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, MEMORY_FILE)
                self.last_memory_save_error = None
            except Exception as exc:
                self.last_memory_save_error = f"{type(exc).__name__}: {exc}"
                logging.exception("Gagal menyimpan memory ke %s", MEMORY_FILE)
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except Exception:
                    pass

    def get_scope_key(
        self,
        guild: discord.Guild | None,
        user_id: int | None = None,
        channel_id: int | None = None,
    ) -> str:
        ch_part = channel_id or 0
        if guild is not None:
            return f"guild_{guild.id}_ch_{ch_part}_user_{user_id or 0}"
        return f"dm_{user_id or 0}_ch_{ch_part}"

    def get_guild_state(
        self,
        guild: discord.Guild | None,
        user_id: int | None = None,
        channel_id: int | None = None,
    ) -> dict:
        key = self.get_scope_key(guild, user_id, channel_id)
        state = self.memory_data["guilds"].get(key)
        if not isinstance(state, dict):
            state = self._normalize_scope_state(state if state is not None else {})
            self.memory_data["guilds"][key] = state
        return state

    def is_channel_allowed(self, channel: discord.abc.Messageable | None, guild: discord.Guild | None) -> bool:
        if guild is None:
            return True
        if channel is None:
            return False
        state = self.get_guild_state(guild)
        base_id = channel.parent_id if isinstance(channel, discord.Thread) else getattr(channel, "id", None)
        return base_id in state["allowed_channels"]

    def has_any_allowed_channel(self, guild: discord.Guild | None) -> bool:
        if guild is None:
            return True
        state = self.get_guild_state(guild)
        return any(guild.get_channel(cid) is not None for cid in state["allowed_channels"])

    async def generate_cloudflare_image(
        self,
        prompt: str,
        source_images: list[str] | None = None,
        aspect_ratio: str | None = None,
    ) -> tuple[bytes, str, str, str]:
        if not CF_ACCOUNTS:
            raise RuntimeError("CF_ACCOUNTS_JSON belum diatur di Environment")

        model_id = CF_EDIT_MODEL if source_images else CF_IMAGE_MODEL
        safe_prompt = sanitize_image_prompt(prompt, max_len=1500)
        target_w, target_h, ratio_label = resolve_image_dimensions(aspect_ratio, prompt, source_images)
        ready_accounts, _ = self._get_rotated_cf_accounts()
        candidates = ready_accounts
        if not candidates:
            wait_left = min(
                (max(0.0, until - time.monotonic()) for until in self.cf_cooldowns.values()),
                default=0.0,
            )
            raise RuntimeError(
                f"Semua akun Cloudflare sedang cooldown (fail-fast aktif). Coba lagi sekitar {wait_left:.0f} detik."
            )
        last_err = ""

        for acc in candidates:
            acc_key = self._cf_account_key(acc)
            url = f"https://api.cloudflare.com/client/v4/accounts/{acc['id']}/ai/run/{model_id}"
            auth_headers = {"Authorization": f"Bearer {acc['token']}"}

            if source_images:
                if len(source_images) > 1 and "flux-2" not in model_id.lower():
                    raise RuntimeError("Model edit yang dipilih hanya menerima satu gambar sumber. Atur CF_EDIT_MODEL ke model Flux 2 untuk multi-gambar.")
                normalized_sources = []
                is_flux2 = "flux-2" in model_id.lower()
                source_max_size = (768, 768) if is_flux2 else (1536, 1536)
                for source_index, source_url in enumerate(source_images[:4]):
                    _, b64_data = parse_data_url(source_url)
                    raw_bytes = base64.b64decode(b64_data, validate=True)
                    if len(raw_bytes) > 12 * 1024 * 1024:
                        raise RuntimeError(f"Gambar sumber ke-{source_index + 1} melebihi 12 MB.")
                    with Image.open(io.BytesIO(raw_bytes)) as src_probe:
                        src_probe.verify()
                    with Image.open(io.BytesIO(raw_bytes)) as pil_src:
                        pil_img = pil_src.convert("RGB")
                    pil_img.thumbnail(source_max_size)
                    png_buf = io.BytesIO()
                    pil_img.save(png_buf, format="PNG")
                    normalized_sources.append(png_buf.getvalue())

                if is_flux2:
                    form = aiohttp.FormData()
                    form.add_field("prompt", safe_prompt)
                    form.add_field("width", str(target_w))
                    form.add_field("height", str(target_h))
                    for source_index, png_bytes in enumerate(normalized_sources):
                        form.add_field(f"input_image_{source_index}", png_bytes, filename=f"source_{source_index + 1}.png", content_type="image/png")
                    req_kwargs = {"headers": auth_headers, "data": form}
                else:
                    pil_img = Image.open(io.BytesIO(normalized_sources[0])).convert("RGB")
                    pil_small = pil_img.copy()
                    pil_small.thumbnail((768, 768))
                    small_buf = io.BytesIO()
                    pil_small.save(small_buf, format="PNG")
                    payload = {
                        "prompt": safe_prompt,
                        "image": list(small_buf.getvalue()),
                        "width": target_w,
                        "height": target_h,
                        "strength": 0.65,
                        "num_steps": 20,
                    }
                    req_kwargs = {
                        "headers": {**auth_headers, "Content-Type": "application/json"},
                        "json": payload,
                    }
            else:
                if "flux-2" in model_id.lower():
                    form = aiohttp.FormData()
                    form.add_field("prompt", safe_prompt)
                    form.add_field("width", str(target_w))
                    form.add_field("height", str(target_h))
                    form.add_field("steps", "20")
                    req_kwargs = {"headers": auth_headers, "data": form}
                elif "lucid-origin" in model_id.lower():
                    payload = {
                        "prompt": safe_prompt,
                        "width": target_w,
                        "height": target_h,
                        "steps": 25,
                        "guidance": 4.5,
                    }
                    req_kwargs = {
                        "headers": {**auth_headers, "Content-Type": "application/json"},
                        "json": payload,
                    }
                else:
                    payload = {
                        "prompt": safe_prompt,
                        "width": target_w,
                        "height": target_h,
                        "steps": 6,
                    }
                    req_kwargs = {
                        "headers": {**auth_headers, "Content-Type": "application/json"},
                        "json": payload,
                    }

            try:
                async with self.http_session.post(
                    url,
                    timeout=aiohttp.ClientTimeout(total=120),
                    **req_kwargs,
                ) as resp:
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if resp.status != 200:
                        err_bytes = await read_limited_stream(resp.content, 64 * 1024)
                        err_text = err_bytes.decode("utf-8", errors="replace")
                        self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                        last_err = f"{acc['label']} HTTP {resp.status}: {err_text[:350]}"
                        continue

                    response_limit = 20 * 1024 * 1024 if "application/json" in content_type else MAX_OUTPUT_IMAGE_BYTES
                    response_body = await read_limited_stream(resp.content, response_limit)
                    if "application/json" in content_type:
                        data = json.loads(response_body.decode("utf-8"))
                        b64_img = data.get("result", {}).get("image")
                        if not b64_img:
                            self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                            last_err = f"{acc['label']} JSON tanpa gambar"
                            continue
                        raw_out = base64.b64decode(b64_img)
                    else:
                        raw_out = response_body

                    valid_bytes, out_filename, out_mime = validate_and_normalize_image_bytes(raw_out)
                    self.cf_cooldowns.pop(acc_key, None)
                    short_model = model_id.split("/")[-1]
                    label = f"Cloudflare AI [{acc['label']}] ({short_model}{' Edit' if source_images else ''} | {ratio_label})"
                    return valid_bytes, out_filename, out_mime, label
            except Exception as exc:
                self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                last_err = f"{acc['label']} Error: {exc}"
                continue

        raise RuntimeError(last_err or "Semua akun Cloudflare gagal")

    async def generate_pollinations_flux(self, prompt: str, width: int = 1024, height: int = 1024) -> tuple[bytes, str, str]:
        compact_prompt = sanitize_image_prompt(prompt, max_len=450)
        q = urllib.parse.quote(compact_prompt)
        seed = random.randint(1, 999999999)
        image_url = f"https://image.pollinations.ai/prompt/{q}?model=flux&width={width}&height={height}&seed={seed}&nologo=true"
        async with self.http_session.get(
            image_url,
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Pollinations FLUX HTTP {resp.status}")
            raw_bytes = await read_limited_stream(resp.content, MAX_OUTPUT_IMAGE_BYTES)
        valid_bytes, out_filename, out_mime = validate_and_normalize_image_bytes(raw_bytes)
        return valid_bytes, out_filename, out_mime

    async def generate_or_edit_image(
        self,
        prompt: str,
        source_images: list[str] | None = None,
        aspect_ratio: str | None = None,
    ) -> tuple[bytes, str, str, str]:
        if source_images:
            try:
                return await self.generate_cloudflare_image(prompt, source_images=source_images, aspect_ratio=aspect_ratio)
            except Exception as exc:
                raise RuntimeError(f"Edit gambar (Klein 4B) gagal di semua akun Cloudflare: {exc}") from exc

        cf_error = ""
        if CF_ACCOUNTS:
            try:
                return await self.generate_cloudflare_image(prompt, source_images=None, aspect_ratio=aspect_ratio)
            except Exception as exc:
                cf_error = f"{exc}"

        target_w, target_h, ratio_label = resolve_image_dimensions(aspect_ratio, prompt, None)
        img_bytes, out_filename, out_mime = await self.generate_pollinations_flux(prompt, width=target_w, height=target_h)
        note = f" (CF fallback: {cf_error[:220]})" if cf_error else " (tanpa CF Token)"
        return img_bytes, out_filename, out_mime, f"Pollinations FLUX [{ratio_label}]{note}"

    def _wrap_doc_content(self, label: str, filename: str, body: str) -> str:
        return f"\n\n--- KONTEN {label} ({filename}) ---\n{body}\n------------------------------------------\n"

    def _validate_zip_archive(self, raw_bytes: bytes) -> zipfile.ZipFile:
        """Open and preflight a ZIP archive before any parser decompresses it.

        Caller owns and must close the returned ZipFile. Raises ValueError or
        BadZipFile for malformed, encrypted, or resource-excessive archives.
        """
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
        try:
            infos = zf.infolist()
            if len(infos) > ZIP_MAX_ENTRIES:
                raise ValueError(f"arsip memiliki terlalu banyak entri ({len(infos)} > {ZIP_MAX_ENTRIES})")
            total_size = 0
            for info in infos:
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise ValueError("arsip ZIP terenkripsi tidak didukung")
                if info.file_size > ZIP_MAX_ENTRY_UNCOMPRESSED:
                    raise ValueError(f"entri ZIP terlalu besar: {info.filename[:100]}")
                total_size += info.file_size
                if total_size > ZIP_MAX_TOTAL_UNCOMPRESSED:
                    raise ValueError("total ukuran hasil ekstraksi ZIP melewati batas keamanan")
                if info.file_size > 1024 * 1024:
                    ratio = info.file_size / max(info.compress_size, 1)
                    if ratio > ZIP_MAX_COMPRESSION_RATIO:
                        raise ValueError(f"rasio kompresi ZIP mencurigakan pada {info.filename[:100]}")
            return zf
        except Exception:
            zf.close()
            raise

    def _extract_zip_media_images(
        self,
        raw_bytes: bytes,
        prefix_dirs: tuple[str, ...],
        max_images: int = MAX_VISION_IMAGES_PER_TURN,
    ) -> list[str]:
        images_out: list[str] = []
        try:
            with self._validate_zip_archive(raw_bytes) as zf:
                media_infos = [
                    info for info in zf.infolist()
                    if not info.is_dir()
                    and info.filename.lower().startswith(prefix_dirs)
                    and info.filename.lower().endswith(self._EMBEDDED_IMAGE_EXTS)
                    and info.file_size <= 15 * 1024 * 1024
                ]
                for info in media_infos[:max_images]:
                    try:
                        with zf.open(info, "r") as stream:
                            mbytes = stream.read(15 * 1024 * 1024 + 1)
                        if not mbytes or len(mbytes) > 15 * 1024 * 1024:
                            continue
                        data_url = self._compress_image_bytes_to_data_url(mbytes)
                        images_out.append(data_url)
                    except Exception as exc:
                        logging.warning("Embedded image skipped (%s): %s", info.filename[:100], type(exc).__name__)
        except Exception as exc:
            logging.warning("Embedded ZIP images unavailable: %s", type(exc).__name__)
        return images_out

    def extract_docx_content(self, docx_bytes: bytes, filename: str) -> tuple[str, list[str]]:
        try:
            with self._validate_zip_archive(docx_bytes):
                pass
        except Exception as exc:
            return f"\n\n[DOCX {filename} ditolak: arsip tidak valid atau melewati batas keamanan ({type(exc).__name__}: {exc}).]\n", []
        media_imgs = self._extract_zip_media_images(docx_bytes, ("word/media/",))
        if docx is None:
            warn = (
                f"\n\n[File DOCX: {filename} terdeteksi, tetapi library 'python-docx' belum terpasang di server. "
                f"Tambahkan 'python-docx' ke requirements.txt lalu restart untuk membaca isi dokumen.]\n"
            )
            return warn, media_imgs

        doc = docx.Document(io.BytesIO(docx_bytes))
        parts: list[str] = []
        total_chars = 0
        max_chars = 25000

        for p in doc.paragraphs:
            txt = (p.text or "").strip()
            if not txt:
                continue
            if total_chars + len(txt) + 2 > max_chars:
                rem = max_chars - total_chars
                if rem > 120:
                    parts.append(txt[:rem] + "\n...(teks dipotong karena panjang)")
                break
            parts.append(txt)
            total_chars += len(txt) + 2

        if total_chars < max_chars and doc.tables:
            for t_idx, table in enumerate(doc.tables[:15], start=1):
                rows_txt: list[str] = []
                for row in table.rows[:40]:
                    cells = [re.sub(r"\s+", " ", (c.text or "").strip()) for c in row.cells]
                    if any(cells):
                        rows_txt.append(" | ".join(cells))
                if rows_txt:
                    tbl_block = f"[Tabel {t_idx}]\n" + "\n".join(rows_txt)
                    if total_chars + len(tbl_block) + 2 > max_chars:
                        rem = max_chars - total_chars
                        if rem > 120:
                            parts.append(tbl_block[:rem] + "\n...(tabel dipotong karena panjang)")
                        break
                    parts.append(tbl_block)
                    total_chars += len(tbl_block) + 2

        extracted = "\n\n".join(parts).strip()
        if media_imgs:
            note = f"\n[Terdapat {len(media_imgs)} gambar di dalam DOCX yang ikut dikirim sebagai lampiran visual.]"
            extracted = (extracted + note).strip() if extracted else note.strip()

        if extracted:
            return self._wrap_doc_content("DOCX", filename, extracted), media_imgs
        return self._wrap_doc_content("DOCX", filename, "[File DOCX terbaca namun tidak memiliki teks paragraf atau tabel yang dapat diekstrak.]"), media_imgs

    def extract_pptx_content(self, pptx_bytes: bytes, filename: str) -> tuple[str, list[str]]:
        try:
            with self._validate_zip_archive(pptx_bytes):
                pass
        except Exception as exc:
            return f"\n\n[PPTX {filename} ditolak: arsip tidak valid atau melewati batas keamanan ({type(exc).__name__}: {exc}).]\n", []
        media_imgs = self._extract_zip_media_images(pptx_bytes, ("ppt/media/",))
        max_chars = 25000
        parts: list[str] = []
        total_chars = 0

        if Presentation is not None:
            prs = Presentation(io.BytesIO(pptx_bytes))
            for s_idx, slide in enumerate(prs.slides, start=1):
                slide_lines: list[str] = []
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False) and shape.text_frame:
                        stxt = (shape.text_frame.text or "").strip()
                        if stxt:
                            slide_lines.append(stxt)
                    if getattr(shape, "has_table", False) and shape.table:
                        for row in shape.table.rows[:30]:
                            cells = [re.sub(r"\s+", " ", (c.text or "").strip()) for c in row.cells]
                            if any(cells):
                                slide_lines.append(" | ".join(cells))
                if slide_lines:
                    block = f"[Slide {s_idx}]\n" + "\n".join(slide_lines)
                    if total_chars + len(block) + 2 > max_chars:
                        rem = max_chars - total_chars
                        if rem > 120:
                            parts.append(block[:rem] + "\n...(teks dipotong karena panjang)")
                        break
                    parts.append(block)
                    total_chars += len(block) + 2
        else:
            try:
                with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as zf:
                    slide_names = sorted(
                        [n for n in zf.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
                        key=lambda x: int(re.search(r"(\d+)", x).group(1)),
                    )
                    for idx, sname in enumerate(slide_names, start=1):
                        root = ET.fromstring(zf.read(sname))
                        texts = [el.text.strip() for el in root.iter() if el.tag.endswith("}t") and el.text and el.text.strip()]
                        if texts:
                            block = f"[Slide {idx}]\n" + "\n".join(texts)
                            if total_chars + len(block) + 2 > max_chars:
                                rem = max_chars - total_chars
                                if rem > 120:
                                    parts.append(block[:rem] + "\n...(teks dipotong karena panjang)")
                                break
                            parts.append(block)
                            total_chars += len(block) + 2
            except Exception as exc:
                return f"\n\n[File PPTX {filename} terdeteksi, namun gagal dibaca ({type(exc).__name__}: {exc}). Tambahkan 'python-pptx' ke requirements.txt untuk hasil optimal.]\n", media_imgs

        extracted = "\n\n".join(parts).strip()
        if media_imgs:
            note = f"\n[Terdapat {len(media_imgs)} gambar di dalam PPTX yang ikut dikirim sebagai lampiran visual.]"
            extracted = (extracted + note).strip() if extracted else note.strip()

        if extracted:
            return self._wrap_doc_content("PPTX", filename, extracted), media_imgs
        return self._wrap_doc_content("PPTX", filename, "[File PPTX terbaca namun tidak mengandung teks slide yang dapat diekstrak.]"), media_imgs

    def extract_xlsx_content(self, xlsx_bytes: bytes, filename: str) -> tuple[str, list[str]]:
        try:
            with self._validate_zip_archive(xlsx_bytes):
                pass
        except Exception as exc:
            return f"\n\n[XLSX {filename} ditolak: arsip tidak valid atau melewati batas keamanan ({type(exc).__name__}: {exc}).]\n", []
        media_imgs = self._extract_zip_media_images(xlsx_bytes, ("xl/media/",))
        if openpyxl is None:
            warn = (
                f"\n\n[File XLSX: {filename} terdeteksi, tetapi library 'openpyxl' belum terpasang di server. "
                f"Tambahkan 'openpyxl' ke requirements.txt lalu restart untuk membaca isi spreadsheet.]\n"
            )
            return warn, media_imgs

        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
        try:
            parts: list[str] = []
            total_chars = 0
            max_chars = 25000

            for sheet_name in wb.sheetnames[:15]:
                ws = wb[sheet_name]
                rows_out: list[str] = []
                for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    if r_idx > 120:
                        rows_out.append("...(baris lanjutan dipotong)")
                        break
                    cells = [re.sub(r"\s+", " ", str(v).strip()) for v in row[:25] if v is not None and str(v).strip() != ""]
                    if cells:
                        rows_out.append(f"B{r_idx}: " + " | ".join(cells))
                if rows_out:
                    block = f"[Sheet: {sheet_name}]\n" + "\n".join(rows_out)
                    if total_chars + len(block) + 2 > max_chars:
                        rem = max_chars - total_chars
                        if rem > 120:
                            parts.append(block[:rem] + "\n...(data sheet dipotong karena panjang)")
                        break
                    parts.append(block)
                    total_chars += len(block) + 2

            extracted = "\n\n".join(parts).strip()
            if media_imgs:
                note = f"\n[Terdapat {len(media_imgs)} gambar di dalam XLSX yang ikut dikirim sebagai lampiran visual.]"
                extracted = (extracted + note).strip() if extracted else note.strip()

            if extracted:
                return self._wrap_doc_content("XLSX", filename, extracted), media_imgs
            return self._wrap_doc_content("XLSX", filename, "[File XLSX terbaca namun semua sheet kosong.]"), media_imgs
        finally:
            wb.close()

    def extract_rtf_content(self, rtf_bytes: bytes, filename: str) -> str:
        raw = rtf_bytes.decode("utf-8", errors="ignore")
        text = re.sub(r"\\par[d]?\b", "\n", raw)
        text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
        text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", text)
        text = text.replace("{", "").replace("}", "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()[:25000]
        if text:
            return self._wrap_doc_content("RTF", filename, text)
        return self._wrap_doc_content("RTF", filename, "[File RTF tidak mengandung teks yang dapat dibaca.]")

    def extract_zip_office_or_epub_content(self, raw_bytes: bytes, filename: str, ext: str) -> str:
        parts: list[str] = []
        total_chars = 0
        max_chars = 25000

        try:
            with self._validate_zip_archive(raw_bytes) as zf:
                if ext in {".odt", ".ods", ".odp"}:
                    target_xml = "content.xml"
                    if target_xml in zf.namelist():
                        root = ET.fromstring(zf.read(target_xml))
                        texts = [el.text.strip() for el in root.iter() if el.text and el.text.strip()]
                        joined = " | ".join(texts)
                        if joined:
                            parts.append(joined[:max_chars])
                elif ext == ".epub":
                    html_files = sorted([n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))])
                    for hname in html_files[:20]:
                        parser = _SimpleHTMLTextExtractor()
                        with zf.open(hname, "r") as stream:
                            html_bytes = stream.read(2 * 1024 * 1024 + 1)
                        if len(html_bytes) > 2 * 1024 * 1024:
                            continue
                        parser.feed(html_bytes.decode("utf-8", errors="ignore"))
                        htxt = parser.get_text().strip()
                        if htxt:
                            block = f"[{hname}]\n{htxt}"
                            if total_chars + len(block) + 2 > max_chars:
                                rem = max_chars - total_chars
                                if rem > 120:
                                    parts.append(block[:rem] + "\n...(konten dipotong karena panjang)")
                                break
                            parts.append(block)
                            total_chars += len(block) + 2
        except Exception as exc:
            return f"\n\n[Gagal membaca arsip {filename} ({ext}): {type(exc).__name__}: {exc}]\n"

        extracted = "\n\n".join(parts).strip()
        if extracted:
            return self._wrap_doc_content(ext.lstrip(".").upper(), filename, extracted)
        return self._wrap_doc_content(ext.lstrip(".").upper(), filename, f"[File {filename} terbaca namun tidak memiliki teks yang dapat diekstrak.]")

    def extract_design_file_content(self, raw_bytes: bytes, filename: str, ext: str) -> str:
        if ext == ".svg":
            try:
                root = ET.fromstring(raw_bytes)
                texts = [re.sub(r"\s+", " ", el.text).strip() for el in root.iter() if el.text and el.text.strip()]
                w = root.attrib.get("width", "?")
                h = root.attrib.get("height", "?")
                vb = root.attrib.get("viewBox", "?")
                summary_lines = [f"Dimensi SVG: width={w}, height={h}, viewBox={vb}"]
                if texts:
                    summary_lines.append("Teks di dalam SVG:\n" + "\n".join(texts[:150]))
                else:
                    summary_lines.append("[Tidak ada elemen teks di dalam SVG; file berisi elemen vektor/grafis.]")
                return self._wrap_doc_content("SVG", filename, "\n\n".join(summary_lines)[:25000])
            except Exception as exc:
                return f"\n\n[Gagal memparse file SVG {filename}: {type(exc).__name__}: {exc}]\n"

        if fitz is not None and ext in {".ai", ".eps"}:
            try:
                doc = fitz.open(stream=raw_bytes, filetype="pdf" if ext == ".ai" else "eps")
                try:
                    pages_txt = []
                    for i, page in enumerate(doc[:10], start=1):
                        pt = page.get_text("text").strip()
                        if pt:
                            pages_txt.append(f"[Halaman/Layer {i}]\n{pt}")
                    if pages_txt:
                        return self._wrap_doc_content(ext.lstrip(".").upper(), filename, "\n\n".join(pages_txt)[:25000])
                finally:
                    doc.close()
            except Exception:
                pass

        printable = re.findall(r"[A-Za-z0-9_.,:;!?()/@#&+\-\s]{4,}", raw_bytes.decode("latin-1", errors="ignore"))
        cleaned_lines = [re.sub(r"\s+", " ", s).strip() for s in printable if len(re.sub(r"\s+", " ", s).strip()) >= 4]
        filtered = [ln for ln in cleaned_lines if not ln.startswith(("%%", "/Subtype", "/Type", "/Filter", "endobj", "stream"))][:80]
        body = f"Info file desain {filename} ({ext}, {round(len(raw_bytes) / 1024, 1)} KB)."
        if filtered:
            body += "\nCuplikan string/label terbaca:\n" + "\n".join(filtered)
        else:
            body += "\n[Konten grafis vektor/Adobe biner terdeteksi; teks editable tidak ditemukan secara langsung.]"
        return self._wrap_doc_content(ext.lstrip(".").upper(), filename, body[:25000])

    async def transcribe_audio_bytes(self, audio_bytes: bytes, filename: str) -> str:
        if not audio_bytes:
            return f"\n\n[File audio {filename} kosong.]\n"
        if not CF_ACCOUNTS or self.http_session is None:
            return f"\n\n[File audio {filename} terdeteksi, tetapi CF_ACCOUNTS_JSON belum siap untuk transkripsi.]\n"

        model_id = (CF_AUDIO_MODEL or "@cf/openai/whisper-large-v3-turbo").strip()
        ready_accounts, _ = self._get_rotated_cf_accounts()
        if not ready_accounts:
            wait_left = min(
                (max(0.0, until - time.monotonic()) for until in self.cf_cooldowns.values()),
                default=0.0,
            )
            return (
                f"\n\n[File audio {filename} terdeteksi, tetapi semua akun Cloudflare sedang cooldown. "
                f"Coba lagi sekitar {wait_left:.0f} detik.]\n"
            )

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        payload = {"audio": audio_b64}
        last_err = ""

        for acc in ready_accounts:
            acc_key = self._cf_account_key(acc)
            url = f"https://api.cloudflare.com/client/v4/accounts/{acc['id']}/ai/run/{model_id}"
            headers = {
                "Authorization": f"Bearer {acc['token']}",
                "Content-Type": "application/json",
            }
            logging.info(
                "CF Audio transcription request: account=%s model=%s filename=%s size=%d",
                acc["label"],
                model_id,
                filename,
                len(audio_bytes),
            )
            try:
                async with self.http_session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    response_bytes = await read_limited_stream(resp.content, 1024 * 1024)
                    raw_text = response_bytes.decode("utf-8", errors="replace")
                    if resp.status != 200:
                        self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                        last_err = f"{acc['label']} HTTP {resp.status}: {raw_text[:250]}"
                        continue

                    transcript = ""
                    try:
                        parsed = json.loads(raw_text)
                        res_obj = parsed.get("result") if isinstance(parsed, dict) else None
                        if isinstance(res_obj, dict):
                            transcript = str(res_obj.get("text") or "").strip()
                        elif isinstance(parsed, dict):
                            transcript = str(parsed.get("text") or "").strip()
                    except Exception:
                        transcript = raw_text.strip()

                    self.cf_cooldowns.pop(acc_key, None)
                    if transcript:
                        return (
                            f"\n\n--- TRANSKRIP AUDIO ({filename} | {acc['label']}) ---\n"
                            f"{transcript[:25000]}\n"
                            f"------------------------------------------\n"
                        )
                    return f"\n\n[File audio {filename} diproses di {acc['label']}, tetapi hasil transkripsi kosong.]\n"
            except Exception as exc:
                self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                last_err = f"{acc['label']} Error: {type(exc).__name__}: {exc}"
                continue

        return f"\n\n[Gagal mentranskripsi audio {filename} di semua akun Cloudflare: {last_err or 'unknown error'}]\n"

    def extract_pdf_content(self, pdf_bytes: bytes, filename: str) -> tuple[str, list[str]]:
        if fitz is None:
            warn = (
                f"\n\n[File PDF: {filename} terdeteksi, tetapi library 'pymupdf' belum terpasang di server. "
                f"Isi PDF tidak dapat dibaca otomatis saat ini.]\n"
            )
            return warn, []

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            return f"\n\n[Gagal membuka PDF {filename}: {type(exc).__name__}: {exc}]\n", []

        try:
            total_pages = len(doc)
            if total_pages == 0:
                return f"\n\n[PDF {filename} kosong dan tidak memiliki halaman.]\n", []

            text_parts: list[str] = []
            rendered_pages: list[str] = []
            total_chars = 0
            max_text_pages = min(total_pages, 25)
            max_render_pages = min(total_pages, MAX_VISION_IMAGES_PER_TURN)
            rendered_page_numbers: list[int] = []
            skipped_render_pages = 0

            for i in range(max_text_pages):
                page = doc[i]
                page_text = (page.get_text("text") or "").strip()
                # A tiny text layer on an otherwise scanned page is not enough
                # to consider the page fully represented; send that page visually.
                if len(page_text) >= 40:
                    block = f"[Halaman {i + 1}]\n{page_text}"
                    remaining = 25000 - total_chars
                    if remaining <= 0:
                        skipped_render_pages += max_text_pages - i
                        break
                    if len(block) > remaining:
                        text_parts.append(block[:remaining] + "\n...(teks dipotong karena panjang)")
                        total_chars = 25000
                        skipped_render_pages += max_text_pages - i - 1
                        break
                    text_parts.append(block)
                    total_chars += len(block) + 2
                elif len(rendered_pages) < max_render_pages:
                    try:
                        rect = page.rect
                        page_width = float(rect.width)
                        page_height = float(rect.height)
                        max_render_pixels = min(MAX_IMAGE_PIXELS, 8_000_000)
                        if (
                            not math.isfinite(page_width)
                            or not math.isfinite(page_height)
                            or page_width <= 0
                            or page_height <= 0
                        ):
                            raise ValueError("ukuran halaman PDF tidak valid")
                        render_scale = min(
                            130 / 72,
                            math.sqrt(max_render_pixels / (page_width * page_height)),
                            8192 / max(page_width, page_height),
                        )
                        if not math.isfinite(render_scale) or render_scale <= 0:
                            raise ValueError("skala render PDF tidak valid")
                        pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale), alpha=False)
                        if pix.width <= 0 or pix.height <= 0 or pix.width * pix.height > max_render_pixels:
                            raise ValueError("hasil render PDF melewati batas piksel")
                        data_url = self._compress_image_bytes_to_data_url(pix.tobytes("png"))
                        rendered_pages.append(data_url)
                        rendered_page_numbers.append(i + 1)
                    except Exception as exc:
                        skipped_render_pages += 1
                        logging.warning("PDF page render failed (%s, page %d): %s", filename, i + 1, type(exc).__name__)
                else:
                    skipped_render_pages += 1

            # Pages beyond the text-page scan cap are explicitly reported rather
            # than silently implying the entire PDF was processed.
            if total_pages > max_text_pages:
                skipped_render_pages += total_pages - max_text_pages

            extracted_text = "\n\n".join(text_parts)
            status_parts = [f"Total {total_pages} halaman"]
            if rendered_page_numbers:
                status_parts.append("halaman scan/visual yang dikirim: " + ", ".join(map(str, rendered_page_numbers)))
            if skipped_render_pages:
                status_parts.append(f"{skipped_render_pages} halaman belum diproses karena batas pemindaian")

            if extracted_text or rendered_pages:
                details = "\n".join(status_parts)
                formatted_text = (
                    f"\n\n--- KONTEN PDF ({filename} | {'; '.join(status_parts)}) ---\n"
                    f"{extracted_text}\n"
                    f"[Catatan ekstraksi: teks diambil per halaman; halaman scan/teks sangat minim dikirim sebagai gambar. {details}]\n"
                    f"------------------------------------------\n"
                )
            else:
                formatted_text = (
                    f"\n\n--- KONTEN PDF ({filename} | {'; '.join(status_parts)}) ---\n"
                    f"[Tidak ada teks yang berhasil diekstrak dan tidak ada halaman yang berhasil dirender. Batas halaman mungkin tercapai atau PDF berisi halaman yang gagal diproses.]\n"
                    f"------------------------------------------\n"
                )
            return formatted_text, rendered_pages
        finally:
            doc.close()

    def _compress_image_bytes_to_data_url(self, raw_bytes: bytes) -> str:
        with Image.open(io.BytesIO(raw_bytes)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(raw_bytes)) as raw_img:
            img = raw_img.convert("RGB")
        img.thumbnail((800, 800))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

    async def download_image_url_to_data_url(self, image_url: str) -> str | None:
        if not image_url or self.http_session is None:
            return None
        try:
            parsed = urllib.parse.urlsplit(image_url)
            if (
                parsed.scheme.lower() != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):
                return None
            host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
            if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
                return None
            try:
                ipaddress.ip_address(host)
                # Reject IP-literal URLs; only DNS names with pinned public answers are fetched.
                return None
            except ValueError:
                pass

            loop = asyncio.get_running_loop()
            resolved = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            pinned_addresses: list[tuple[int, str]] = []
            seen_addresses: set[str] = set()
            for result in resolved:
                family, address = result[0], result[4][0].split("%", 1)[0]
                ip_obj = ipaddress.ip_address(address)
                if not ip_obj.is_global:
                    return None
                if address not in seen_addresses:
                    pinned_addresses.append((family, address))
                    seen_addresses.add(address)
            if not pinned_addresses:
                return None

            class _PinnedResolver(aiohttp.abc.AbstractResolver):
                async def resolve(self, hostname, port=0, family=socket.AF_INET):
                    if hostname.rstrip(".").lower() != host:
                        raise OSError("Hostname mismatch in pinned image resolver")
                    records = []
                    for addr_family, address in pinned_addresses:
                        if family not in (socket.AF_UNSPEC, addr_family):
                            continue
                        records.append({
                            "hostname": hostname,
                            "host": address,
                            "port": port,
                            "family": addr_family,
                            "proto": 0,
                            "flags": 0,
                        })
                    if not records:
                        raise OSError("No vetted public address for image host")
                    return records

                async def close(self):
                    return None

            connector = aiohttp.TCPConnector(resolver=_PinnedResolver(), use_dns_cache=False)
            async with aiohttp.ClientSession(connector=connector) as safe_session:
                async with safe_session.get(
                    image_url,
                    timeout=aiohttp.ClientTimeout(total=25),
                    allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        return None
                    raw_bytes = await read_limited_stream(resp.content, 15 * 1024 * 1024)
            if not raw_bytes:
                return None
            return await asyncio.to_thread(self._compress_image_bytes_to_data_url, raw_bytes)
        except Exception:
            return None

    async def encode_attachment(self, attachment: discord.Attachment) -> tuple[str | None, list[str], list[str], str]:
        filename = attachment.filename.lower()
        ext = os.path.splitext(filename)[1]

        if ext in IMAGE_EXTS and attachment.size <= 15 * 1024 * 1024:
            try:
                data = await attachment.read()
                local_data_url = await asyncio.to_thread(self._compress_image_bytes_to_data_url, data)
                http_urls = [attachment.url] if attachment.url and attachment.url.startswith(("http://", "https://")) else []
                return None, [local_data_url], http_urls, "image"
            except Exception:
                return f"\n\n[File gambar {attachment.filename} rusak atau gagal dibaca.]\n", [], [], "none"

        elif ext == ".pdf" and attachment.size <= 20 * 1024 * 1024:
            try:
                data = await attachment.read()
                pdf_text, pdf_images = await asyncio.to_thread(self.extract_pdf_content, data, attachment.filename)
                return pdf_text, pdf_images, [], "pdf"
            except Exception as e:
                return f"\n\n[Gagal membaca file PDF {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "document"

        elif ext == ".docx" and attachment.size <= 15 * 1024 * 1024:
            try:
                data = await attachment.read()
                docx_text, docx_imgs = await asyncio.to_thread(self.extract_docx_content, data, attachment.filename)
                return docx_text, docx_imgs, [], "docx"
            except Exception as e:
                return f"\n\n[Gagal membaca file DOCX {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "document"

        elif ext == ".pptx" and attachment.size <= 20 * 1024 * 1024:
            try:
                data = await attachment.read()
                pptx_text, pptx_imgs = await asyncio.to_thread(self.extract_pptx_content, data, attachment.filename)
                return pptx_text, pptx_imgs, [], "pptx"
            except Exception as e:
                return f"\n\n[Gagal membaca file PPTX {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "document"

        elif ext == ".xlsx" and attachment.size <= 20 * 1024 * 1024:
            try:
                data = await attachment.read()
                xlsx_text, xlsx_imgs = await asyncio.to_thread(self.extract_xlsx_content, data, attachment.filename)
                return xlsx_text, xlsx_imgs, [], "xlsx"
            except Exception as e:
                return f"\n\n[Gagal membaca file XLSX {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "document"

        elif ext == ".rtf" and attachment.size <= 10 * 1024 * 1024:
            try:
                data = await attachment.read()
                rtf_text = await asyncio.to_thread(self.extract_rtf_content, data, attachment.filename)
                return rtf_text, [], [], "rtf"
            except Exception as e:
                return f"\n\n[Gagal membaca file RTF {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "document"

        elif ext in {".odt", ".ods", ".odp", ".epub"} and attachment.size <= 20 * 1024 * 1024:
            try:
                data = await attachment.read()
                zip_text = await asyncio.to_thread(self.extract_zip_office_or_epub_content, data, attachment.filename, ext)
                return zip_text, [], [], ext.lstrip(".")
            except Exception as e:
                return f"\n\n[Gagal membaca file {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "document"

        elif ext in {".svg", ".eps", ".ai"} and attachment.size <= 20 * 1024 * 1024:
            try:
                data = await attachment.read()
                design_text = await asyncio.to_thread(self.extract_design_file_content, data, attachment.filename, ext)
                return design_text, [], [], ext.lstrip(".")
            except Exception as e:
                return f"\n\n[Gagal membaca file desain {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "document"

        elif ext in AUDIO_EXTS and attachment.size <= 25 * 1024 * 1024:
            try:
                data = await attachment.read()
                audio_text = await self.transcribe_audio_bytes(data, attachment.filename)
                return audio_text, [], [], "audio"
            except Exception as e:
                return f"\n\n[Gagal memproses file audio {attachment.filename}: {type(e).__name__}: {e}]\n", [], [], "audio"

        elif ext in (FULL_CONTENT_EXTS - OFFICE_DOC_EXTS) and attachment.size <= 2 * 1024 * 1024:
            try:
                data = await attachment.read()
                text_content = data.decode("utf-8", errors="ignore")
                formatted = f"\n\n--- KONTEN FILE ({attachment.filename}) ---\n{text_content}\n------------------------------------------\n"
                return formatted, [], [], "text"
            except Exception:
                return f"\n\n[Gagal membaca konten teks dari file {attachment.filename}.]\n", [], [], "none"

        elif ext in ALL_SUPPORTED_EXTS:
            return (
                f"\n\n[Metadata file: nama={attachment.filename}, ukuran={round(attachment.size / (1024*1024), 2)} MB. "
                f"Catatan: parser isi penuh tersedia untuk gambar, PDF, DOCX, PPTX, XLSX, RTF, ODT/ODS/ODP/EPUB, SVG/EPS/AI, audio, dan file teks/kode.]\n",
                [],
                [],
                "metadata_only",
            )

        return None, [], [], "none"

    async def resolve_replied_images(self, message: discord.Message) -> tuple[list[str], list[str]]:
        ref = message.reference
        if not ref or not ref.message_id:
            return [], []
        replied_msg = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if replied_msg is None and hasattr(message.channel, "fetch_message"):
            try:
                replied_msg = await message.channel.fetch_message(ref.message_id)
            except Exception:
                replied_msg = None
        if not replied_msg:
            return [], []

        local_imgs: list[str] = []
        http_urls: list[str] = []
        for att in replied_msg.attachments:
            if os.path.splitext(att.filename.lower())[1] not in IMAGE_EXTS:
                continue
            _, local_res, http_res, ftype = await self.encode_attachment(att)
            if ftype == "image":
                local_imgs.extend(local_res)
                http_urls.extend(http_res)

        if not local_imgs and replied_msg.embeds:
            for emb in replied_msg.embeds:
                candidate_urls: list[str] = []
                for media_obj in (getattr(emb, "image", None), getattr(emb, "thumbnail", None)):
                    if media_obj is None:
                        continue
                    for attr_name in ("url", "proxy_url"):
                        u = getattr(media_obj, attr_name, None)
                        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in candidate_urls:
                            candidate_urls.append(u)
                for cand_url in candidate_urls:
                    downloaded_data_url = await self.download_image_url_to_data_url(cand_url)
                    if downloaded_data_url:
                        local_imgs.append(downloaded_data_url)
                        if cand_url not in http_urls:
                            http_urls.append(cand_url)
                        break
                if local_imgs:
                    break

        return local_imgs, http_urls

    def build_messages_payload(
        self,
        guild: discord.Guild | None,
        user_id: int,
        channel_id: int | None = None,
        current_turn_images: list[str] | None = None,
    ) -> list[dict]:
        guild_state = self.get_guild_state(guild, user_id, channel_id)
        messages = [{"role": "system", "content": BASE_SYSTEM_PROMPT}]
        messages.extend(guild_state["messages"])

        vision_imgs = [
            u for u in (current_turn_images or [])
            if isinstance(u, str) and u.startswith(("http://", "https://", "data:image/"))
        ][:MAX_VISION_IMAGES_PER_TURN]

        if vision_imgs and messages and messages[-1].get("role") == "user":
            last_msg = messages[-1]
            existing_content = last_msg.get("content", "")
            if isinstance(existing_content, list):
                existing_urls = {
                    b.get("image_url", {}).get("url")
                    for b in existing_content
                    if isinstance(b, dict) and b.get("type") == "image_url"
                }
                new_blocks = list(existing_content)
                for u in vision_imgs:
                    if u not in existing_urls:
                        new_blocks.append({"type": "image_url", "image_url": {"url": u}})
                        existing_urls.add(u)
                capped_blocks = []
                image_count = 0
                for block in new_blocks:
                    if isinstance(block, dict) and block.get("type") == "image_url":
                        if image_count >= MAX_VISION_IMAGES_PER_TURN:
                            continue
                        image_count += 1
                    capped_blocks.append(block)
                messages[-1] = {"role": "user", "content": capped_blocks}
            else:
                blocks = [{"type": "text", "text": str(existing_content)}] + [
                    {"type": "image_url", "image_url": {"url": u}} for u in vision_imgs
                ]
                messages[-1] = {"role": "user", "content": blocks}

        return messages

    def to_responses_input(self, messages: list[dict]) -> tuple[str, list[dict]]:
        instructions = BASE_SYSTEM_PROMPT
        input_items = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                instructions = str(content)
                continue
            if role == "assistant":
                input_items.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": str(content)}],
                })
            else:
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        btype = block.get("type")
                        if btype == "text":
                            parts.append({"type": "input_text", "text": block.get("text", "")})
                        elif btype == "image_url":
                            img_url = block.get("image_url", {}).get("url", "")
                            if img_url and img_url.startswith(("http://", "https://", "data:image/")):
                                parts.append({"type": "input_image", "image_url": img_url})
                    if not parts:
                        parts.append({"type": "input_text", "text": ""})
                    input_items.append({"role": "user", "content": parts})
                else:
                    input_items.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": str(content)}],
                    })
        return instructions, input_items

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

    async def _run_background_sync(self):
        try:
            await asyncio.sleep(1.5)
            await self.tree.sync()
            for guild in list(self.guilds):
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    await asyncio.sleep(0.35)
                except Exception:
                    logging.exception("Gagal sync command ke guild %s", getattr(guild, "id", "?"))
        except Exception:
            logging.exception("Gagal sinkronisasi slash commands di background")

    def schedule_command_sync(self):
        if self._sync_task is not None and not self._sync_task.done():
            return
        self._sync_task = asyncio.create_task(self._run_background_sync())

    async def sync_commands_to_guilds(self):
        await self._run_background_sync()

    async def close(self):
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
        await self.save_memory()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    async def run_responses_ai(
        self,
        messages: list[dict],
        target_msg: discord.Message | None,
        mode: str = "fast",
        *,
        allow_tools: bool = True,
        allow_image_tool: bool = True,
        extra_input: list[dict] | None = None,
    ) -> tuple[str, list[dict]]:
        url = f"{XINGCHI_BASE_URL}/responses"
        headers = {
            "Authorization": f"Bearer {XINGCHI_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "opencode/1.0.0",
            "X-Client-Name": "OpenCode",
        }
        effort = "high" if mode == "full" else "low"
        max_tokens = 8192 if mode == "full" else 4096
        timeout = aiohttp.ClientTimeout(total=300, sock_read=180) if mode == "full" else aiohttp.ClientTimeout(total=180, sock_read=120)

        instructions, input_items = self.to_responses_input(messages)
        tools: list[dict] = [{"type": "web_search"}]
        if allow_image_tool:
            tools.append({
                "type": "function",
                "name": "generate_image",
                "description": "Membuat gambar baru dari teks ATAU mengedit/memodifikasi gambar yang dilampirkan pengguna (seperti mengganti teks, warna, objek, aspek rasio, atau gaya). Panggil HANYA jika pengguna meminta dibuatkan atau diedit gambar.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Deskripsi visual gambar baru (sekitar 300-900 karakter) atau instruksi detail pengeditan gambar dalam bahasa Inggris.",
                        },
                        "use_attached_image": {
                            "type": "boolean",
                            "description": "Set true jika pengguna meminta mengedit/mengubah gambar yang dilampirkan atau baru saja dikirim; set false jika membuat gambar baru dari nol.",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "description": "Rasio aspek gambar yang diinginkan ('1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3', '4:5', '5:4', '21:9', atau 'auto' untuk mengikuti rasio gambar sumber saat edit / 1:1 saat buat baru).",
                        },
                    },
                    "required": ["prompt", "use_attached_image", "aspect_ratio"],
                },
            })

        if extra_input:
            for item in extra_input:
                if not isinstance(item, dict):
                    raise ValueError("Item lanjutan Responses harus berupa object.")
                item_type = item.get("type")
                if item_type == "function_call":
                    call_id = str(item.get("call_id") or "").strip()
                    name = str(item.get("name") or "").strip()
                    arguments = item.get("arguments", "{}")
                    if not call_id or not name:
                        raise ValueError("Function call lanjutan tidak memiliki call_id atau name.")
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    input_items.append({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    })
                elif item_type == "function_call_output":
                    call_id = str(item.get("call_id") or "").strip()
                    if not call_id or "output" not in item:
                        raise ValueError("Function call output tidak lengkap.")
                    output = item["output"]
                    if not isinstance(output, str):
                        output = json.dumps(output, ensure_ascii=False)
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    })
                else:
                    raise ValueError(f"Tipe item lanjutan tidak didukung: {item_type!r}")

        payload = {
            "model": XINGCHI_MODEL,
            "instructions": instructions,
            "input": input_items,
            "tools": tools if allow_tools else [],
            "tool_choice": "auto" if allow_tools else "none",
            "reasoning": {"effort": effort},
            "max_output_tokens": max_tokens,
            "stream": True,
        }

        raw_full_text = ""
        function_calls: list[dict] = []
        response_completed = False

        def append_function_call(item: dict) -> None:
            if not isinstance(item, dict):
                raise RuntimeError("Responses mengirim function_call dengan format tidak valid.")
            call_id = str(item.get("call_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not call_id or not name:
                raise RuntimeError("Responses function_call tidak memiliki call_id atau name.")
            existing = next((fc for fc in function_calls if fc.get("call_id") == call_id), None)
            if existing is not None:
                if existing.get("name") != name or existing.get("arguments", "{}") != item.get("arguments", "{}"):
                    raise RuntimeError(f"Responses mengirim function_call duplikat dengan payload berbeda: {call_id}")
                return
            function_calls.append(item)

        last_edit = asyncio.get_event_loop().time()
        web_badge_shown = False

        async with self.http_session.post(url, headers=headers, json=payload, timeout=timeout) as resp:
            if resp.status != 200:
                err_bytes = await read_limited_stream(resp.content, 64 * 1024)
                err_text = err_bytes.decode("utf-8", errors="replace")
                raise RuntimeError(f"Responses HTTP {resp.status}: {err_text[:300]}")

            while True:
                line = await resp.content.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="ignore").strip()
                if not decoded.startswith("data: "):
                    continue
                data_str = decoded[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except Exception:
                    continue

                etype = event.get("type", "")

                if etype in ("response.web_search_call.in_progress", "response.web_search_call.searching") and not raw_full_text and not web_badge_shown:
                    web_badge_shown = True
                    try:
                        if target_msg is not None:
                            await target_msg.edit(content="🌐 *Sedang menelusuri web secara live...*")
                    except Exception:
                        pass

                elif etype == "response.output_text.delta":
                    delta = event.get("delta", "")
                    if delta and (raw_full_text or delta.strip()):
                        raw_full_text += delta
                        cleaned_live = clean_response_text(raw_full_text)
                        now = asyncio.get_event_loop().time()
                        if now - last_edit >= 0.8 and cleaned_live and len(cleaned_live) <= DISCORD_MSG_CHUNK_LIMIT:
                            try:
                                if target_msg is not None:
                                    await target_msg.edit(content=cleaned_live + " ▌")
                                last_edit = now
                            except Exception:
                                pass

                elif etype == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        append_function_call(item)
                    elif item.get("type") == "message" and not raw_full_text:
                        for part in item.get("content", []):
                            if part.get("type") in ("output_text", "text"):
                                raw_full_text += part.get("text", "")

                elif etype == "response.completed":
                    response_completed = True
                    resp_obj = event.get("response", {})
                    for item in resp_obj.get("output", []):
                        if item.get("type") == "function_call":
                            append_function_call(item)
                        elif item.get("type") == "message" and not raw_full_text:
                            for part in item.get("content", []):
                                if part.get("type") in ("output_text", "text"):
                                    raw_full_text += part.get("text", "")

                elif etype == "response.incomplete":
                    resp_obj = event.get("response", {})
                    details = resp_obj.get("incomplete_details") or {}
                    reason = details.get("reason") or resp_obj.get("status") or "unknown"
                    raise RuntimeError(f"Responses stream incomplete: {reason}")

                elif etype in ("error", "response.failed"):
                    err_msg = event.get("error", {}).get("message") or str(event)[:250]
                    raise RuntimeError(f"Responses Stream Error: {err_msg}")

        if not response_completed:
            raise RuntimeError("Responses stream berakhir sebelum event response.completed diterima.")

        return clean_response_text(raw_full_text).strip(), function_calls

    async def process_ai_turn(
        self,
        target_channel: discord.abc.Messageable,
        guild: discord.Guild | None,
        user: discord.User | discord.Member,
        clean_content: str,
        image_data_urls: list[str],
        llm_image_urls: list[str] | None = None,
        additional_file_text: str = "",
        reply_to_message: discord.Message | None = None,
    ):
        channel_id = getattr(target_channel, "id", 0)
        guild_id = guild.id if guild is not None else 0
        scope_key = self.get_scope_key(guild, user.id, channel_id)
        http_img_urls = [u for u in (llm_image_urls or []) if u.startswith(("http://", "https://"))]
        current_turn_images: list[str] = []
        for u in (image_data_urls or []) + http_img_urls:
            if isinstance(u, str) and u.startswith(("http://", "https://", "data:image/")) and u not in current_turn_images:
                current_turn_images.append(u)
                if len(current_turn_images) >= MAX_VISION_IMAGES_PER_TURN:
                    break

        allow_img_tool = should_allow_image_tool(
            clean_content,
            has_file_or_pdf=bool(additional_file_text),
            has_image=bool(current_turn_images),
        )

        async with self.guild_locks[scope_key]:
            guild_state = self.get_guild_state(guild, user.id, channel_id)
            mode = guild_state["thread_modes"].get(str(target_channel.id), "fast")
            history_msgs = guild_state["messages"]

            if image_data_urls and channel_id:
                self.set_last_user_image(guild_id, channel_id, user.id, image_data_urls)

            full_user_prompt = clean_content
            if additional_file_text:
                full_user_prompt += f"\n\n{additional_file_text}\n(Tolong baca dan analisis file serta gambar yang disertakan di atas!)"

            user_display_text = f"{user.display_name}: {full_user_prompt}"

            if http_img_urls:
                blocks = [{"type": "text", "text": user_display_text}] + [
                    {"type": "image_url", "image_url": {"url": u}} for u in http_img_urls[:MAX_VISION_IMAGES_PER_TURN]
                ]
                history_msgs.append({"role": "user", "content": blocks})
            else:
                history_msgs.append({"role": "user", "content": user_display_text})

            for m in history_msgs[:-1]:
                if isinstance(m.get("content"), list):
                    text_parts = []
                    for block in m["content"]:
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "image_url":
                            text_parts.append("[Gambar]")
                    m["content"] = "\n".join(text_parts).strip() or "[Gambar]"

            if len(history_msgs) > MAX_RECENT_MESSAGES:
                guild_state["messages"] = history_msgs[-MAX_RECENT_MESSAGES:]
                history_msgs = guild_state["messages"]

            payload_messages = self.build_messages_payload(
                guild, user.id, channel_id, current_turn_images=current_turn_images
            )

            if additional_file_text or current_turn_images:
                status_badge = "🔍 *Sedang membaca dan menganalisis file...*"
            elif mode == "full":
                status_badge = "🧠 *Sedang berpikir mendalam untuk menyusun jawaban...*"
            else:
                status_badge = "💬 *Sedang memproses pesan...*"

            status_msg = await (
                reply_to_message.reply(status_badge, mention_author=False)
                if reply_to_message
                else target_channel.send(status_badge)
            )

            try:
                reply_text, function_calls = await self.run_responses_ai(
                    payload_messages,
                    status_msg,
                    mode=mode,
                    allow_tools=True,
                    allow_image_tool=allow_img_tool,
                )

                img_calls = [fc for fc in function_calls if fc.get("name") == "generate_image"]
                unsupported_calls = [fc for fc in function_calls if fc.get("name") != "generate_image"]
                if unsupported_calls:
                    rejected_items = build_failure_tool_continuation(
                        function_calls,
                        "Function tool tidak didukung oleh bot ini. Tool yang tersedia hanya generate_image; web_search ditangani provider.",
                    )
                    if rejected_items:
                        try:
                            follow_text, _ = await self.run_responses_ai(
                                payload_messages,
                                status_msg,
                                mode=mode,
                                allow_tools=False,
                                allow_image_tool=False,
                                extra_input=rejected_items,
                            )
                            if follow_text:
                                reply_text = follow_text
                        except Exception as exc:
                            logging.exception("Continuation unsupported tool gagal (%s)", scope_key)
                            reply_text = f"⚠️ Tool dari model tidak didukung dan kelanjutan gagal: {type(exc).__name__}."
                    function_calls = []
                    img_calls = []

                if img_calls and not allow_img_tool:
                    blocked_items = build_failure_tool_continuation(
                        img_calls,
                        "Pengguna hanya meminta analisis/penjelasan, bukan pembuatan atau pengeditan gambar.",
                    )
                    if blocked_items:
                        try:
                            blocked_text, _ = await self.run_responses_ai(
                                payload_messages,
                                status_msg,
                                mode=mode,
                                allow_tools=False,
                                allow_image_tool=False,
                                extra_input=blocked_items,
                            )
                            if blocked_text:
                                reply_text = blocked_text
                        except Exception:
                            logging.exception("Continuation blocked image call gagal (%s)", scope_key)
                    img_calls = []

                if img_calls:
                    if self.is_user_image_busy(user.id):
                        await status_msg.edit(content="⏳ *Menunggu antrean proses gambar sebelumnya selesai...*")

                    async with self.user_image_queue(user.id):
                        unresolved_img_calls = list(img_calls)
                        is_edit = False
                        try:
                            primary_call = img_calls[0]
                            extra_calls = img_calls[1:]

                            try:
                                args_dict = json.loads(primary_call.get("arguments", "{}"))
                            except Exception:
                                args_dict = {}

                            img_prompt = (args_dict.get("prompt") or clean_content).strip()
                            use_attached = bool(args_dict.get("use_attached_image", False))
                            req_ratio = args_dict.get("aspect_ratio") or extract_aspect_ratio_from_text(clean_content)

                            replied_imgs: list[str] = []
                            if reply_to_message is not None:
                                replied_imgs, _ = await self.resolve_replied_images(reply_to_message)

                            wants_edit = use_attached or any(w in clean_content.lower() for w in EDIT_INTENT_KEYWORDS)

                            source_imgs: list[str] | None = None
                            if use_attached:
                                source_imgs = (
                                    image_data_urls
                                    or replied_imgs
                                    or self.get_last_user_image(guild_id, channel_id, user.id)
                                )
                            elif image_data_urls or replied_imgs:
                                if wants_edit:
                                    source_imgs = image_data_urls or replied_imgs
                            elif wants_edit:
                                source_imgs = self.get_last_user_image(guild_id, channel_id, user.id)

                            if wants_edit and not source_imgs:
                                fail_items = build_failure_tool_continuation(
                                    unresolved_img_calls,
                                    "Gambar sumber untuk edit gambar tidak ditemukan.",
                                )
                                unresolved_img_calls = []
                                if fail_items:
                                    try:
                                        await self.run_responses_ai(
                                            payload_messages,
                                            status_msg,
                                            mode=mode,
                                            allow_tools=False,
                                            allow_image_tool=False,
                                            extra_input=fail_items,
                                        )
                                    except Exception:
                                        logging.exception("Continuation missing source image gagal (%s)", scope_key)
                                fail_msg = (
                                    "⚠️ Gagal memproses edit gambar: tidak ada gambar sumber yang ditemukan "
                                    "(lampirkan gambar, *reply* ke pesan bergambar, atau pastikan konteks gambar sebelumnya masih tersedia)."
                                )
                                history_msgs.append({"role": "assistant", "content": fail_msg})
                                guild_state["turn_count"] = guild_state.get("turn_count", 0) + 1
                                await self.save_memory()
                                await status_msg.edit(content=fail_msg[:1990])
                                return

                            is_edit = bool(source_imgs)
                            action_badge = "🖌️ *Sedang mengedit gambar...*" if is_edit else "🎨 *Sedang membuat gambar...*"
                            await status_msg.edit(content=action_badge)

                            img_bytes, out_filename, out_mime, engine_used = await self.generate_or_edit_image(
                                img_prompt, source_images=source_imgs, aspect_ratio=req_ratio
                            )

                            continuation_items: list[dict] = []
                            p_call_id = primary_call.get("call_id")
                            if p_call_id:
                                continuation_items.append(primary_call)
                                continuation_items.append({
                                    "type": "function_call_output",
                                    "call_id": p_call_id,
                                    "output": json.dumps({
                                        "success": True,
                                        "operation": "edit_image" if is_edit else "generate_image",
                                        "engine": engine_used,
                                        "filename": out_filename,
                                        "detail": "Gambar berhasil dibuat dan dilampirkan ke pesan Discord."
                                    }, ensure_ascii=False),
                                })

                            for extra_fc in extra_calls:
                                e_call_id = extra_fc.get("call_id")
                                if e_call_id:
                                    continuation_items.append(extra_fc)
                                    continuation_items.append({
                                        "type": "function_call_output",
                                        "call_id": e_call_id,
                                        "output": json.dumps({
                                            "success": False,
                                            "skipped": True,
                                            "reason": "Hanya satu panggilan gambar utama yang diproses per giliran."
                                        }, ensure_ascii=False),
                                    })

                            unresolved_img_calls = []
                            if continuation_items:
                                try:
                                    final_text, _ = await self.run_responses_ai(
                                        payload_messages,
                                        status_msg,
                                        mode=mode,
                                        allow_tools=False,
                                        allow_image_tool=False,
                                        extra_input=continuation_items,
                                    )
                                    if final_text:
                                        reply_text = final_text
                                except Exception as follow_exc:
                                    logging.exception("Continuation sukses gambar gagal (%s)", scope_key)
                                    reply_text = (reply_text + f"\n\nGambar berhasil diproses. (Catatan: model tidak menyelesaikan respons lanjutan: {follow_exc})").strip()

                            if channel_id:
                                context_image = await asyncio.to_thread(
                                    self._compress_image_bytes_to_data_url, img_bytes
                                )
                                self.set_last_user_image(guild_id, channel_id, user.id, [context_image])

                            file = discord.File(fp=io.BytesIO(img_bytes), filename=out_filename)

                            embed_title = "🖌️ Clouzer AI Image Editor" if is_edit else "🎨 Clouzer AI Image Generator"
                            embed_desc = f"**Prompt:** {img_prompt[:3600]}"
                            if is_edit:
                                embed_desc = f"{embed_desc[:3450]}\n\n{TEXT_EDIT_NOTE}"

                            embed = discord.Embed(
                                title=embed_title,
                                description=embed_desc,
                                color=discord.Color.blurple(),
                            )
                            embed.set_image(url=f"attachment://{out_filename}")
                            embed.set_footer(text=f"Created by clouzerd | API: {engine_used}")

                            extra_note = f"\n\n{TEXT_EDIT_NOTE}" if is_edit else ""
                            combined_text = f"{reply_text}{extra_note}".strip() if reply_text else (TEXT_EDIT_NOTE if is_edit else "")
                            caption_chunks = split_discord_chunks(combined_text, limit=DISCORD_MSG_CHUNK_LIMIT) if combined_text else []
                            first_caption = caption_chunks[0] if caption_chunks else None

                            mem_summary = format_memory_image_summary(engine_used, img_prompt)
                            mem_content = f"{reply_text}\n{mem_summary}".strip() if reply_text else mem_summary
                            history_msgs.append({"role": "assistant", "content": mem_content})
                            guild_state["turn_count"] = guild_state.get("turn_count", 0) + 1
                            await self.save_memory()

                            await status_msg.edit(content=first_caption, embed=embed, attachments=[file])
                            for extra_chunk in caption_chunks[1:]:
                                await target_channel.send(extra_chunk)
                            return
                        except Exception as img_exc:
                            if unresolved_img_calls:
                                fail_items = build_failure_tool_continuation(
                                    unresolved_img_calls,
                                    f"Proses gambar gagal: {img_exc}",
                                )
                                unresolved_img_calls = []
                                if fail_items:
                                    try:
                                        await self.run_responses_ai(
                                            payload_messages,
                                            status_msg,
                                            mode=mode,
                                            allow_tools=False,
                                            allow_image_tool=False,
                                            extra_input=fail_items,
                                        )
                                    except Exception:
                                        logging.exception("Continuation exception gambar gagal (%s)", scope_key)
                            if is_edit:
                                fail_msg = (
                                    f"⚠️ Edit gambar gagal dilakukan ({img_exc}). "
                                    f"Gambar tidak diubah dan tidak dialihkan ke generator gambar baru."
                                )[:1990]
                                history_msgs.append({"role": "assistant", "content": fail_msg})
                                guild_state["turn_count"] = guild_state.get("turn_count", 0) + 1
                                await self.save_memory()
                                await status_msg.edit(content=fail_msg)
                                return
                            raise

                reply_text = reply_text or "Maaf, tidak ada respon."
                history_msgs.append({"role": "assistant", "content": reply_text})
                guild_state["turn_count"] = guild_state.get("turn_count", 0) + 1
                await self.save_memory()

                ui_view = AIResponseView(
                    self,
                    payload_messages,
                    mode,
                    source_images=image_data_urls,
                    user_prompt_text=clean_content,
                    allow_image_tool=allow_img_tool,
                    owner_id=user.id,
                )
                chunks = split_discord_chunks(reply_text, limit=DISCORD_MSG_CHUNK_LIMIT)
                await status_msg.edit(content=chunks[0], view=ui_view)
                for chunk in chunks[1:]:
                    await target_channel.send(chunk)
            except Exception as e:
                if history_msgs:
                    history_msgs.pop()
                err_msg = f"⚠️ Error: `{type(e).__name__}: {e}`"[:1990]
                await status_msg.edit(content=err_msg)


bot = ClouzerBot()


def get_permission_warning(guild: discord.Guild | None) -> str:
    if not bot.has_any_allowed_channel(guild):
        return "⚠️ **Setup Awal:** Bot belum diizinkan. Admin ketik `/channel` → **Izinkan Channel Ini**!"
    return "❌ Channel ini belum diizinkan. Atur lewat `/channel`."


@bot.tree.command(name="channel", description="Atur izin channel bot (Khusus Admin)")
@app_commands.describe(aksi="Pilih tindakan", target="Channel tujuan")
@app_commands.choices(
    aksi=[
        app_commands.Choice(name="Izinkan Channel Ini", value="izinkan"),
        app_commands.Choice(name="Tolak / Hapus Izin Channel", value="tolak"),
        app_commands.Choice(name="Lihat Daftar Channel Diizinkan", value="list"),
    ]
)
@app_commands.default_permissions(manage_channels=True)
async def channel_perm_cmd(interaction: discord.Interaction, aksi: app_commands.Choice[str], target: discord.TextChannel | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("Perintah khusus server.", ephemeral=True)
        return
    member_perms = getattr(interaction.user, "guild_permissions", None)
    if not member_perms or not (member_perms.manage_channels or member_perms.administrator):
        await interaction.response.send_message(
            "⛔ Perintah ini hanya untuk admin dengan izin Manage Channels.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    guild_state = bot.get_guild_state(interaction.guild)

    if aksi.value == "list":
        allowed_ids = [cid for cid in guild_state["allowed_channels"] if interaction.guild.get_channel(cid) is not None]
        if not allowed_ids:
            await interaction.followup.send("📋 Belum ada channel diizinkan.", ephemeral=True)
        else:
            mentions = "\n".join(f"• <#{cid}>" for cid in allowed_ids)
            await interaction.followup.send(f"📋 **Channel Diizinkan:**\n{mentions}", ephemeral=True)
        return

    ch = target or (interaction.channel.parent if isinstance(interaction.channel, discord.Thread) else interaction.channel)
    if aksi.value == "izinkan":
        if ch.id not in guild_state["allowed_channels"]:
            guild_state["allowed_channels"].append(ch.id)
            await bot.save_memory()
        await interaction.followup.send(f"✅ Izin diberikan di {ch.mention}!", ephemeral=True)
    elif aksi.value == "tolak":
        if ch.id in guild_state["allowed_channels"]:
            guild_state["allowed_channels"].remove(ch.id)
            await bot.save_memory()
        await interaction.followup.send(f"🚫 Izin dicabut di {ch.mention}!", ephemeral=True)


async def handle_mode_command(interaction: discord.Interaction, mode: str, prompt: str | None):
    if not bot.is_channel_allowed(interaction.channel, interaction.guild):
        await interaction.response.send_message(get_permission_warning(interaction.guild), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    mode_badge = "🧠 **[Full Thinking]**" if mode == "full" else "⚡ **[Fast Mode]**"
    ch_id = getattr(interaction.channel, "id", 0)
    guild_state = bot.get_guild_state(interaction.guild, interaction.user.id, ch_id)
    channel = interaction.channel

    if interaction.guild is not None and not isinstance(channel, discord.Thread) and isinstance(channel, discord.TextChannel):
        thread_title = f"GPT 6.1 SOL | {interaction.user.display_name}"[:100]
        try:
            thread = await channel.create_thread(name=thread_title, type=discord.ChannelType.public_thread, auto_archive_duration=1440)
            if thread.id not in guild_state["active_threads"]:
                guild_state["active_threads"].append(thread.id)
            bot.get_guild_state(interaction.guild, interaction.user.id, thread.id)["thread_modes"][str(thread.id)] = mode
            await bot.save_memory()

            await interaction.followup.send(f"{mode_badge} Thread baru dibuat: {thread.mention}")
            if prompt and prompt.strip():
                await thread.send(f"{mode_badge} **{interaction.user.display_name}:** {prompt.strip()}")
                await bot.process_ai_turn(target_channel=thread, guild=interaction.guild, user=interaction.user, clean_content=prompt.strip(), image_data_urls=[])
            return
        except Exception:
            pass

    guild_state["thread_modes"][str(channel.id)] = mode
    await bot.save_memory()

    if prompt and prompt.strip():
        await interaction.followup.send(f"{mode_badge} **{interaction.user.display_name}:** {prompt.strip()}")
        await bot.process_ai_turn(target_channel=channel, guild=interaction.guild, user=interaction.user, clean_content=prompt.strip(), image_data_urls=[])
    else:
        await interaction.followup.send(f"{mode_badge} Mode diubah ke **{mode.upper()}**.")


@bot.tree.command(name="fast", description="Chat dengan mode cepat (/fast)")
async def fast_cmd(interaction: discord.Interaction, prompt: str | None = None):
    await handle_mode_command(interaction, mode="fast", prompt=prompt)


@bot.tree.command(name="full-thinking", description="Chat dengan mode berpikir mendalam (/full-thinking)")
async def full_thinking_cmd(interaction: discord.Interaction, prompt: str | None = None):
    await handle_mode_command(interaction, mode="full", prompt=prompt)


@bot.tree.command(name="summarize", description="Ringkas obrolan terbaru di channel ini")
@app_commands.describe(limit="Jumlah pesan terakhir yang ingin dirangkum (default: 30)")
async def summarize_cmd(interaction: discord.Interaction, limit: int = 30):
    if not bot.is_channel_allowed(interaction.channel, interaction.guild):
        await interaction.response.send_message(get_permission_warning(interaction.guild), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    scope_key = bot.get_scope_key(
        interaction.guild, interaction.user.id, interaction.channel_id
    )
    try:
        async with bot.guild_locks[scope_key]:
            messages_text = []
            async for msg in interaction.channel.history(limit=min(max(limit, 1), 100)):
                if not msg.author.bot and msg.content.strip():
                    messages_text.append(f"{msg.author.display_name}: {msg.content}")

            if not messages_text:
                await interaction.followup.send("⚠️ Tidak ada pesan teks yang bisa dirangkum di channel ini.")
                return

            chat_snippet = "\n".join(reversed(messages_text))
            sum_prompt = (
                "Tolong buatkan ringkasan singkat, padat, dan jelas dari percakapan obrolan Discord berikut ini. "
                "Sebutkan poin-poin penting atau topik yang sedang dibahas oleh member:\n\n"
                f"{chat_snippet}"
            )

            summary_result, _ = await bot.run_responses_ai(
                [{"role": "user", "content": sum_prompt}], None, "fast", allow_tools=False, allow_image_tool=False
            )
        await interaction.followup.send(
            f"📋 **Ringkasan Obrolan Channel ({len(messages_text)} pesan terakhir):**\n\n{(summary_result or 'Tidak ada ringkasan.')[:DISCORD_MSG_CHUNK_LIMIT]}"
        )
    except Exception:
        logging.exception("Summarize gagal untuk scope %s", scope_key)
        await interaction.followup.send("⚠️ Gagal merangkum obrolan. Cek log bot untuk detail error.")


@bot.tree.command(name="imagine", description="Buat gambar baru atau edit gambar menggunakan Cloudflare AI (+ Backup FLUX)")
@app_commands.describe(
    prompt="Deskripsi gambar baru atau instruksi edit (disarankan bahasa Inggris)",
    gambar="Opsional: Lampirkan gambar jika ingin diedit",
    rasio="Opsional: Pilih rasio aspek gambar (default Auto)",
)
@app_commands.choices(
    rasio=[
        app_commands.Choice(name="Auto (Ikuti Gambar Asli / 1:1)", value="auto"),
        app_commands.Choice(name="16:9 (Landscape / Widescreen)", value="16:9"),
        app_commands.Choice(name="9:16 (Portrait / Story)", value="9:16"),
        app_commands.Choice(name="4:3 (Standar Landscape)", value="4:3"),
        app_commands.Choice(name="3:4 (Standar Portrait)", value="3:4"),
        app_commands.Choice(name="3:2 (Foto Landscape)", value="3:2"),
        app_commands.Choice(name="2:3 (Foto Portrait)", value="2:3"),
        app_commands.Choice(name="4:5 (Feed Portrait)", value="4:5"),
        app_commands.Choice(name="5:4 (Feed Landscape)", value="5:4"),
        app_commands.Choice(name="21:9 (Ultrawide)", value="21:9"),
        app_commands.Choice(name="1:1 (Persegi / Square)", value="1:1"),
    ]
)
async def imagine_cmd(
    interaction: discord.Interaction,
    prompt: str,
    gambar: discord.Attachment | None = None,
    rasio: app_commands.Choice[str] | None = None,
):
    if not bot.is_channel_allowed(interaction.channel, interaction.guild):
        await interaction.response.send_message(get_permission_warning(interaction.guild), ephemeral=True)
        return

    ch_id = getattr(interaction.channel, "id", 0)
    g_id = interaction.guild.id if interaction.guild is not None else 0
    scope_key = bot.get_scope_key(interaction.guild, interaction.user.id, ch_id)
    selected_ratio = rasio.value if rasio is not None else None
    was_busy = bot.is_user_image_busy(interaction.user.id)

    initial_status = (
        "⏳ *Menunggu antrean proses gambar sebelumnya selesai...*"
        if was_busy
        else "💬 *Sedang memproses pesan...*"
    )
    await interaction.response.send_message(initial_status)

    try:
        async with bot.guild_locks[scope_key]:
            async with bot.user_image_queue(interaction.user.id):
                source_imgs: list[str] | None = None
                if gambar is not None:
                    _, imgs_res, _, ftype = await bot.encode_attachment(gambar)
                    if ftype != "image" or not imgs_res:
                        await interaction.edit_original_response(content="⚠️ Lampiran harus berupa file gambar valid (`.png`, `.jpg`, `.jpeg`, `.webp`).")
                        return
                    source_imgs = imgs_res

                is_edit = bool(source_imgs)
                action_badge = "🖌️ *Sedang mengedit gambar...*" if is_edit else "🎨 *Sedang membuat gambar...*"
                await interaction.edit_original_response(content=action_badge)

                try:
                    img_bytes, out_filename, out_mime, engine_used = await bot.generate_or_edit_image(
                        prompt, source_images=source_imgs, aspect_ratio=selected_ratio
                    )
                except Exception as img_exc:
                    if is_edit:
                        await interaction.edit_original_response(
                            content=(
                                f"⚠️ Edit gambar gagal dilakukan ({img_exc}). "
                                f"Gambar tidak diubah dan tidak dialihkan ke generator gambar baru."
                            )[:1990]
                        )
                        return
                    raise

                if ch_id:
                    context_image = await asyncio.to_thread(
                        bot._compress_image_bytes_to_data_url, img_bytes
                    )
                    bot.set_last_user_image(g_id, ch_id, interaction.user.id, [context_image])

                file = discord.File(fp=io.BytesIO(img_bytes), filename=out_filename)

                embed_title = "🖌️ Clouzer AI Image Editor" if is_edit else "🎨 Clouzer AI Image Generator"
                embed_desc = f"**Prompt:** {prompt.strip()[:3600]}"
                if is_edit:
                    embed_desc = f"{embed_desc[:3450]}\n\n{TEXT_EDIT_NOTE}"

                embed = discord.Embed(
                    title=embed_title,
                    description=embed_desc,
                    color=discord.Color.blurple(),
                )
                embed.set_image(url=f"attachment://{out_filename}")
                embed.set_footer(text=f"Created by clouzerd | API: {engine_used}")

                await interaction.edit_original_response(content=None, embed=embed, attachments=[file])
    except Exception as e:
        try:
            err_txt = f"⚠️ Terjadi kesalahan saat memproses gambar: `{type(e).__name__}: {e}`"[:1990]
            if interaction.response.is_done():
                await interaction.edit_original_response(content=err_txt)
            else:
                await interaction.response.send_message(err_txt, ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="cek-tool", description="Tes diagnostik Function Calling & Built-in Web Search (/responses)")
async def cek_tool_cmd(interaction: discord.Interaction):
    if not bot.is_channel_allowed(interaction.channel, interaction.guild):
        await interaction.response.send_message(get_permission_warning(interaction.guild), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    url = f"{XINGCHI_BASE_URL}/responses"
    headers = {
        "Authorization": f"Bearer {XINGCHI_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "opencode/1.0.0",
        "X-Client-Name": "OpenCode",
    }

    async def run_probe(payload: dict) -> tuple[int, dict | None, str]:
        try:
            async with bot.http_session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                raw = await resp.text()
                try:
                    return resp.status, json.loads(raw), raw
                except Exception:
                    return resp.status, None, raw
        except Exception as exc:
            return 0, None, f"{type(exc).__name__}: {exc}"

    payload_fc = {
        "model": XINGCHI_MODEL,
        "input": "Panggil fungsi cek_status_server dengan target 'wispbyte' sekarang juga tanpa teks tambahan.",
        "tools": [
            {
                "type": "function",
                "name": "cek_status_server",
                "description": "Mengecek status server bot Discord",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Nama server"}
                    },
                    "required": ["target"],
                },
            }
        ],
        "tool_choice": "auto",
        "stream": False,
    }

    payload_ws = {
        "model": XINGCHI_MODEL,
        "input": "Cari di web harga terbaru kurs 1 USD ke Rupiah hari ini dan sebutkan sumbernya.",
        "tools": [{"type": "web_search"}],
        "stream": False,
    }

    status_fc, data_fc, raw_fc = await run_probe(payload_fc)
    status_ws, data_ws, raw_ws = await run_probe(payload_ws)

    if status_fc == 200 and isinstance(data_fc, dict):
        outputs_fc = data_fc.get("output", [])
        types_fc = [item.get("type", "unknown") for item in outputs_fc]
        fn_calls = [item for item in outputs_fc if item.get("type") == "function_call"]
        if fn_calls:
            call_info = f"`{fn_calls[0].get('name')}` args: `{fn_calls[0].get('arguments')}`"
            fc_report = f"✅ **AKTIF (HTTP 200)**\nOutput types: `{types_fc}`\nCall: {call_info}"
        else:
            fc_report = f"⚠️ **HTTP 200 (Tool Diabaikan)**\nOutput types: `{types_fc}`\nSnippet: ```json\n{raw_fc[:450]}\n```"
    else:
        fc_report = f"❌ **GAGAL (HTTP {status_fc})**\n```json\n{raw_fc[:500]}\n```"

    if status_ws == 200 and isinstance(data_ws, dict):
        outputs_ws = data_ws.get("output", [])
        types_ws = [item.get("type", "unknown") for item in outputs_ws]
        has_ws_call = any("web_search" in t for t in types_ws)
        text_preview = ""
        for item in outputs_ws:
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") in ("output_text", "text"):
                        text_preview += part.get("text", "")
        text_preview = clean_response_text(text_preview)
        if has_ws_call:
            ws_report = f"✅ **AKTIF (HTTP 200)**\nOutput types: `{types_ws}`\nHasil: {text_preview[:450]}"
        else:
            ws_report = f"⚠️ **HTTP 200 (Web Search Diabaikan)**\nOutput types: `{types_ws}`\nTeks: {text_preview[:400] or raw_ws[:400]}"
    else:
        ws_report = f"❌ **GAGAL (HTTP {status_ws})**\n```json\n{raw_ws[:500]}\n```"

    cf_status = f"Terpasang ({len(CF_ACCOUNTS)} akun)" if CF_ACCOUNTS else "Kosong"
    embed = discord.Embed(
        title="🧪 Hasil Diagnostik Tool & Web Search (/v1/responses)",
        description=f"Model: `{XINGCHI_MODEL}` | Cloudflare AI: `{cf_status}` | Gen: `{CF_IMAGE_MODEL.split('/')[-1]}` | Edit: `{CF_EDIT_MODEL.split('/')[-1]}`",
        color=discord.Color.gold(),
    )
    embed.add_field(name="1️⃣ Custom Function Calling", value=fc_report[:1020], inline=False)
    embed.add_field(name="2️⃣ Built-in Web Search", value=ws_report[:1020], inline=False)
    await interaction.followup.send(embed=embed)


@bot.event
async def on_ready():
    bot.schedule_command_sync()
    print(f"[ONLINE] Clouzer AI Bot aktif (Cloudflare AI: {'OK (' + str(len(CF_ACCOUNTS)) + ' akun)' if CF_ACCOUNTS else 'MISSING'} | Gen: {CF_IMAGE_MODEL} | Edit: {CF_EDIT_MODEL} | Audio: {CF_AUDIO_MODEL} | Multi-Format Doc/Slide/Sheet/Design/Audio Reader + Native Web Search)!")
    print("[CHECK] Pastikan Message Content Intent juga aktif di Discord Developer Portal.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not bot.is_channel_allowed(message.channel, message.guild):
        return

    clean_content = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

    image_data_urls: list[str] = []
    llm_image_urls: list[str] = []
    additional_file_text = ""

    for att in message.attachments:
        text_res, local_res, http_res, ftype = await bot.encode_attachment(att)
        if text_res:
            additional_file_text += text_res
        if local_res:
            image_data_urls.extend(local_res)
        if http_res:
            llm_image_urls.extend(http_res)

    if not image_data_urls and message.reference is not None:
        rep_local, rep_http = await bot.resolve_replied_images(message)
        if rep_local:
            image_data_urls.extend(rep_local)
        if rep_http:
            llm_image_urls.extend(rep_http)

    if not clean_content:
        if additional_file_text:
            clean_content = "Tolong baca, analisis, dan ringkas isi file yang saya kirim ini ya."
        elif image_data_urls or llm_image_urls:
            clean_content = "Tolong jelaskan isi gambar ini."
        else:
            return

    await bot.process_ai_turn(
        target_channel=message.channel,
        guild=message.guild,
        user=message.author,
        clean_content=clean_content,
        image_data_urls=image_data_urls,
        llm_image_urls=llm_image_urls,
        additional_file_text=additional_file_text,
        reply_to_message=message,
    )


async def main():
    if not DISCORD_TOKEN or not XINGCHI_API_KEY:
        raise RuntimeError("Token belum lengkap di .env!")
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())