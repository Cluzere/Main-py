import os
import io
import re
import json
import time
import logging
import base64
import random
import urllib.parse
import asyncio
from collections import defaultdict, OrderedDict
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from PIL import Image
from dotenv import load_dotenv

try:
    import pymupdf as fitz  # PyMuPDF untuk baca teks & render halaman PDF ke gambar
except ImportError:
    fitz = None

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
XINGCHI_API_KEY = os.getenv("XINGCHI_API_KEY")
XINGCHI_BASE_URL = os.getenv("XINGCHI_BASE_URL", "https://api.xingchiapi.com/v1").rstrip("/")
XINGCHI_MODEL = os.getenv("XINGCHI_MODEL", "gpt-6.1-sol")

CF_COOLDOWN_SECONDS = float(os.getenv("CF_COOLDOWN_SECONDS", "45"))
IMAGE_CONTEXT_TTL_SECONDS = float(os.getenv("IMAGE_CONTEXT_TTL_SECONDS", "1800"))
IMAGE_CONTEXT_MAX_ENTRIES = int(os.getenv("IMAGE_CONTEXT_MAX_ENTRIES", "30"))
MAX_OUTPUT_IMAGE_BYTES = int(os.getenv("MAX_OUTPUT_IMAGE_BYTES", str(12 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", str(25_000_000)))

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

TEXT_EDIT_NOTE = (
    "Catatan: untuk revisi teks atau huruf yang butuh presisi tinggi, "
    "lebih aman dirapikan manual di **Canva** atau **Photopea**."
)

DATA_URL_BASE64_RE = re.compile(r"data:[a-zA-Z0-9.+/-]+;base64,[A-Za-z0-9+/=]+")
LONG_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{256,}(?![A-Za-z0-9+/=])")


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

MEMORY_FILE = "memory.json"
MAX_RECENT_MESSAGES = 10
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

ALL_SUPPORTED_EXTS = {
    ".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv", ".md",
    ".xml", ".yml", ".yaml", ".ini", ".env", ".log", ".sql", ".sh", ".pdf",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}

FULL_CONTENT_EXTS = {
    ".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv", ".md", ".xml", ".yml", ".yaml", ".ini", ".env", ".log", ".sql", ".sh",
    ".pdf"
}

EDIT_INTENT_KEYWORDS = (
    "ganti", "ubah", "edit", "hapus", "tambah", "replace", "change",
    "recolor", "warnai", "mewarnai", "perbaiki", "koreksi", "retouch", "modif"
)

CREATE_INTENT_KEYWORDS = (
    "buatkan", "bikin", "buat", "gambar", "ilustrasi", "lukis", "draw",
    "generate", "create", "render", "poster", "logo", "avatar", "wallpaper"
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
  - Jika pengguna melampirkan gambar (atau merujuk gambar yang baru dikirim) lalu meminta MENGEDIT, MENGUBAH TEKS, MENGGANTI WARNA, atau MEMODIFIKASI gambar tersebut, wajib panggil `generate_image` dengan `use_attached_image: true` dan tulis instruksi edit yang jelas serta presisi di `prompt`.
  - Jika pengguna hanya bertanya atau minta dijelaskan isi gambar/PDF tanpa minta dibuatkan atau diedit gambarnya, JANGAN panggil `generate_image`.
  - Saat menyertai gambar dengan teks, sampaikan ringkasannya secara singkat dan santai tanpa menjelaskan proses penyusunan prompt.

ATURAN GAYA BAHASA:
- Gunakan bahasa gaul, santai, natural, dan tidak terlalu baku layaknya teman ngobrol yang pintar.
- Jangan pernah menyapa kosong jika user mengirim file atau PDF; langsung baca teks maupun gambar di dalamnya dan bahas isinya!"""


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


def clean_response_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\ue200.*?\ue201", "", text, flags=re.DOTALL)
    text = re.sub(r"\ue200[^\ue201]*$", "", text)
    text = re.sub(r"[\ue200-\ue20f]", "", text)
    text = re.sub(r" +([.,!?;:])", r"\1", text)
    return text


def split_discord_chunks(text: str, limit: int = 1950) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        slice_part = remaining[:limit]
        split_idx = slice_part.rfind("\n\n")
        if split_idx < limit // 2:
            split_idx = slice_part.rfind("\n")
        if split_idx < limit // 2:
            split_idx = slice_part.rfind(" ")
        if split_idx <= 0:
            split_idx = limit

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
                return normalized, f"clouzer_image.{ext}", mime

            png_buf = io.BytesIO()
            img.convert("RGBA").save(png_buf, format="PNG")
            return png_buf.getvalue(), "clouzer_image.png", "image/png"
    except Exception as exc:
        raise RuntimeError(f"Gagal memverifikasi/menyiapkan gambar: {exc}") from exc


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
    ):
        super().__init__(timeout=600)
        self.bot_instance = bot_instance
        self.original_messages = original_messages
        self.mode = mode
        self.source_images = list(source_images or [])
        self.user_prompt_text = user_prompt_text
        self.allow_image_tool = allow_image_tool

    @discord.ui.button(label="🔄 Regenerate", style=discord.ButtonStyle.secondary, custom_id="ai_regenerate")
    async def regenerate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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

        try:
            async with self.bot_instance.guild_locks[scope_key]:
                new_reply, function_calls = await self.bot_instance.run_responses_ai(
                    self.original_messages,
                    interaction.message,
                    self.mode,
                    allow_tools=True,
                    allow_image_tool=self.allow_image_tool,
                )

                img_call = next((fc for fc in function_calls if fc.get("name") == "generate_image"), None)
                if img_call and self.allow_image_tool:
                    try:
                        args_dict = json.loads(img_call.get("arguments", "{}"))
                    except Exception:
                        args_dict = {}

                    img_prompt = (args_dict.get("prompt") or self.user_prompt_text or "Create a detailed image").strip()
                    use_attached = bool(args_dict.get("use_attached_image", False))
                    wants_edit = use_attached or any(k in (self.user_prompt_text or "").lower() for k in EDIT_INTENT_KEYWORDS)

                    source_imgs = (
                        self.source_images
                        or self.bot_instance.get_last_user_image(guild_id, channel_id, interaction.user.id)
                    ) if (use_attached or wants_edit) else None

                    if wants_edit and not source_imgs:
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

                    try:
                        img_bytes, out_filename, out_mime, engine_used = await self.bot_instance.generate_or_edit_image(
                            img_prompt, source_images=source_imgs
                        )
                    except Exception as img_exc:
                        if is_edit:
                            err_content = f"⚠️ Regenerate edit gambar gagal ({img_exc})."
                            if interaction.message is not None:
                                await interaction.message.edit(content=err_content, view=self)
                            else:
                                await interaction.edit_original_response(content=err_content, view=self)
                            return
                        raise

                    call_id = img_call.get("call_id")
                    if call_id:
                        tool_output = {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps({
                                "success": True,
                                "operation": "edit_image" if is_edit else "generate_image",
                                "engine": engine_used,
                                "filename": out_filename,
                            }, ensure_ascii=False),
                        }
                        try:
                            follow_text, _ = await self.bot_instance.run_responses_ai(
                                self.original_messages,
                                interaction.message,
                                mode=self.mode,
                                allow_tools=False,
                                allow_image_tool=False,
                                extra_input=[img_call, tool_output],
                            )
                            if follow_text:
                                new_reply = follow_text
                        except Exception:
                            pass

                    if channel_id:
                        b64_out = base64.b64encode(img_bytes).decode("utf-8")
                        self.bot_instance.set_last_user_image(
                            guild_id, channel_id, interaction.user.id, [f"data:{out_mime};base64,{b64_out}"]
                        )

                    file = discord.File(fp=io.BytesIO(img_bytes), filename=out_filename)
                    embed_title = "🖌️ Clouzer AI Image Editor" if is_edit else "🎨 Clouzer AI Image Generator"
                    embed_desc = f"**Prompt:** {img_prompt[:1800]}"
                    if is_edit:
                        embed_desc = f"{embed_desc[:1650]}\n\n{TEXT_EDIT_NOTE}"

                    embed = discord.Embed(
                        title=embed_title,
                        description=embed_desc,
                        color=discord.Color.blurple(),
                    )
                    embed.set_image(url=f"attachment://{out_filename}")
                    embed.set_footer(text=f"Created by clouzerd | API: {engine_used}")

                    extra_note = f"\n\n{TEXT_EDIT_NOTE}" if is_edit else ""
                    combined = f"**Hasil Regenerate:**\n\n{(new_reply or '').strip()}{extra_note}".strip()
                    final_content = combined[:1950] if combined else None

                    if interaction.message is not None:
                        await interaction.message.edit(content=final_content, embed=embed, attachments=[file], view=self)
                    else:
                        await interaction.edit_original_response(content=final_content, embed=embed, attachments=[file], view=self)
                    return

            final_content = f"**Hasil Regenerate:**\n\n{(new_reply or 'Tidak ada respons.')[:1950]}"
            if interaction.message is not None:
                await interaction.message.edit(content=final_content, view=self)
            else:
                await interaction.edit_original_response(content=final_content, view=self)
        except Exception as exc:
            logging.exception("Regenerate gagal untuk scope %s", scope_key)
            err_content = f"⚠️ Regenerate gagal: `{type(exc).__name__}: {exc}`"
            if interaction.message is not None:
                await interaction.message.edit(content=err_content, view=self)
            else:
                await interaction.edit_original_response(content=err_content, view=self)

    @discord.ui.button(label="📋 Kirim Teks via DM", style=discord.ButtonStyle.primary, custom_id="ai_dm_text")
    async def dm_text_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            msg_content = interaction.message.content
            if not msg_content and interaction.message.embeds:
                msg_content = interaction.message.embeds[0].description or "(Embed Gambar)"
            await interaction.user.send(f"📥 **Salinan Teks Bot:**\n\n{scrub_base64_text(msg_content or '')}")
            await interaction.response.send_message("✅ Berhasil dikirim ke DM lu!", ephemeral=True)
        except Exception:
            await interaction.response.send_message("⚠️ Gagal kirim DM. Pastikan DM Discord lu tidak dikunci!", ephemeral=True)


class ClouzerBot(commands.Bot):
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
        self.memory_lock = asyncio.Lock()
        self.memory_data = self.load_memory()

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
                merged[k] = val

        for k in ["thread_modes", "users"]:
            val = raw_state.get(k)
            if isinstance(val, dict):
                merged[k] = val

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
            except Exception:
                pass
        return {"guilds": {}}

    async def save_memory(self):
        async with self.memory_lock:
            try:
                disk_payload = self._sanitize_memory_for_disk()
                tmp_file = f"{MEMORY_FILE}.tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(disk_payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, MEMORY_FILE)
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
        if key not in self.memory_data["guilds"]:
            self.memory_data["guilds"][key] = self._normalize_scope_state({})
        else:
            self.memory_data["guilds"][key] = self._normalize_scope_state(self.memory_data["guilds"][key])
        return self.memory_data["guilds"][key]

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

    async def generate_cloudflare_image(self, prompt: str, source_images: list[str] | None = None) -> tuple[bytes, str, str, str]:
        if not CF_ACCOUNTS:
            raise RuntimeError("CF_ACCOUNTS_JSON belum diatur di Environment")

        model_id = CF_EDIT_MODEL if source_images else CF_IMAGE_MODEL
        safe_prompt = sanitize_image_prompt(prompt, max_len=1500)
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
                source_max_size = (511, 511) if is_flux2 else (1536, 1536)
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
                    form.add_field("steps", "20")
                    req_kwargs = {"headers": auth_headers, "data": form}
                elif "lucid-origin" in model_id.lower():
                    payload = {
                        "prompt": safe_prompt,
                        "steps": 25,
                        "guidance": 4.5,
                    }
                    req_kwargs = {
                        "headers": {**auth_headers, "Content-Type": "application/json"},
                        "json": payload,
                    }
                else:
                    payload = {"prompt": safe_prompt, "steps": 6}
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
                        err_text = await resp.text()
                        self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                        last_err = f"{acc['label']} HTTP {resp.status}: {err_text[:350]}"
                        continue

                    if "application/json" in content_type:
                        data = await resp.json()
                        b64_img = data.get("result", {}).get("image")
                        if not b64_img:
                            self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                            last_err = f"{acc['label']} JSON tanpa gambar"
                            continue
                        raw_out = base64.b64decode(b64_img)
                    else:
                        raw_out = await resp.read()

                    valid_bytes, out_filename, out_mime = validate_and_normalize_image_bytes(raw_out)
                    self.cf_cooldowns.pop(acc_key, None)
                    short_model = model_id.split("/")[-1]
                    label = f"Cloudflare AI [{acc['label']}] ({short_model}{' Edit' if source_images else ''})"
                    return valid_bytes, out_filename, out_mime, label
            except Exception as exc:
                self.cf_cooldowns[acc_key] = time.monotonic() + CF_COOLDOWN_SECONDS
                last_err = f"{acc['label']} Error: {exc}"
                continue

        raise RuntimeError(last_err or "Semua akun Cloudflare gagal")

    async def generate_pollinations_flux(self, prompt: str) -> tuple[bytes, str, str]:
        compact_prompt = sanitize_image_prompt(prompt, max_len=450)
        q = urllib.parse.quote(compact_prompt)
        seed = random.randint(1, 999999999)
        image_url = f"https://image.pollinations.ai/prompt/{q}?model=flux&width=1024&height=1024&seed={seed}&nologo=true"
        async with self.http_session.get(
            image_url,
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Pollinations FLUX HTTP {resp.status}")
            raw_bytes = await resp.read()
        valid_bytes, out_filename, out_mime = validate_and_normalize_image_bytes(raw_bytes)
        return valid_bytes, out_filename, out_mime

    async def generate_or_edit_image(self, prompt: str, source_images: list[str] | None = None) -> tuple[bytes, str, str, str]:
        if source_images:
            try:
                return await self.generate_cloudflare_image(prompt, source_images=source_images)
            except Exception as exc:
                raise RuntimeError(f"Edit gambar (Klein 4B) gagal di semua akun Cloudflare: {exc}") from exc

        cf_error = ""
        if CF_ACCOUNTS:
            try:
                return await self.generate_cloudflare_image(prompt, source_images=None)
            except Exception as exc:
                cf_error = f"{exc}"

        img_bytes, out_filename, out_mime = await self.generate_pollinations_flux(prompt)
        note = f" (CF fallback: {cf_error[:220]})" if cf_error else " (tanpa CF Token)"
        return img_bytes, out_filename, out_mime, f"Pollinations FLUX{note}"

    def extract_pdf_content(self, pdf_bytes: bytes, filename: str) -> tuple[str, list[str]]:
        if fitz is None:
            warn = (
                f"\n\n[File PDF: {filename} terdeteksi, tetapi library 'pymupdf' belum terpasang di server. "
                f"Isi PDF tidak dapat dibaca otomatis saat ini.]\n"
            )
            return warn, []

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            total_pages = len(doc)
            text_parts: list[str] = []
            total_chars = 0
            max_text_pages = min(total_pages, 25)

            for i in range(max_text_pages):
                page_text = doc[i].get_text("text").strip()
                if page_text:
                    block = f"[Halaman {i + 1}]\n{page_text}"
                    if total_chars + len(block) > 25000:
                        remaining = 25000 - total_chars
                        if remaining > 120:
                            text_parts.append(block[:remaining] + "\n...(teks dipotong karena panjang)")
                        break
                    text_parts.append(block)
                    total_chars += len(block)

            candidate_indices = list(range(min(total_pages, 5)))
            if total_pages > 5:
                visual_pages = [
                    i for i in range(total_pages)
                    if len(doc[i].get_images()) > 0 or len(doc[i].get_text("text").strip()) < 50
                ]
                if visual_pages:
                    merged: list[int] = []
                    for idx in [0] + visual_pages:
                        if idx not in merged:
                            merged.append(idx)
                        if len(merged) >= 5:
                            break
                    candidate_indices = merged

            page_images: list[str] = []
            for idx in candidate_indices:
                pix = doc[idx].get_pixmap(dpi=140)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                img.thumbnail((1024, 1024))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=82)
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                page_images.append(f"data:image/jpeg;base64,{b64}")

            rendered_note = f"Dirender {len(page_images)} halaman sebagai gambar (maks. 5 halaman; tanpa OCR lokal terpisah)."
            extracted_text = "\n\n".join(text_parts)
            if extracted_text:
                formatted_text = (
                    f"\n\n--- KONTEN PDF ({filename} | Total {total_pages} halaman | {rendered_note}) ---\n"
                    f"{extracted_text}\n"
                    f"------------------------------------------\n"
                )
            else:
                formatted_text = (
                    f"\n\n--- KONTEN PDF ({filename} | Total {total_pages} halaman | {rendered_note}) ---\n"
                    f"[PDF ini tidak memiliki teks seleksi atau berupa hasil scan/gambar. Baca dan analisis langsung dari gambar halaman PDF yang dilampirkan.]\n"
                    f"------------------------------------------\n"
                )
            return formatted_text, page_images
        finally:
            doc.close()

    async def encode_attachment(self, attachment: discord.Attachment) -> tuple[str | None, list[str], str]:
        filename = attachment.filename.lower()
        ext = os.path.splitext(filename)[1]

        if ext in IMAGE_EXTS and attachment.size <= 15 * 1024 * 1024:
            try:
                data = await attachment.read()
                with Image.open(io.BytesIO(data)) as probe:
                    probe.verify()
                with Image.open(io.BytesIO(data)) as raw_img:
                    img = raw_img.convert("RGB")
                img.thumbnail((1280, 1280))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return None, [f"data:image/jpeg;base64,{b64}"], "image"
            except Exception:
                return f"\n\n[File gambar {attachment.filename} rusak atau gagal dibaca.]\n", [], "none"

        elif ext == ".pdf" and attachment.size <= 20 * 1024 * 1024:
            try:
                data = await attachment.read()
                pdf_text, pdf_images = await asyncio.to_thread(self.extract_pdf_content, data, attachment.filename)
                return pdf_text, pdf_images, "pdf"
            except Exception as e:
                return f"\n\n[Gagal membaca file PDF {attachment.filename}: {type(e).__name__}: {e}]\n", [], "document"

        elif ext in (FULL_CONTENT_EXTS - {".pdf"}) and attachment.size <= 2 * 1024 * 1024:
            try:
                data = await attachment.read()
                text_content = data.decode("utf-8", errors="ignore")
                formatted = f"\n\n--- KONTEN FILE ({attachment.filename}) ---\n{text_content}\n------------------------------------------\n"
                return formatted, [], "text"
            except Exception:
                return f"\n\n[Gagal membaca konten teks dari file {attachment.filename}.]\n", [], "none"

        elif ext in ALL_SUPPORTED_EXTS:
            return (
                f"\n\n[Metadata file: nama={attachment.filename}, ukuran={round(attachment.size / (1024*1024), 2)} MB. "
                f"Catatan: parser isi penuh hanya tersedia untuk gambar, PDF, dan file teks/kode; isi biner/dokumen format {ext} tidak diekstrak isinya.]\n",
                [],
                "metadata_only",
            )

        return None, [], "none"

    async def resolve_replied_images(self, message: discord.Message) -> list[str]:
        ref = message.reference
        if not ref or not ref.message_id:
            return []
        replied_msg = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if replied_msg is None and hasattr(message.channel, "fetch_message"):
            try:
                replied_msg = await message.channel.fetch_message(ref.message_id)
            except Exception:
                replied_msg = None
        if not replied_msg:
            return []

        imgs: list[str] = []
        for att in replied_msg.attachments:
            _, imgs_res, ftype = await self.encode_attachment(att)
            if ftype == "image" and imgs_res:
                imgs.extend(imgs_res)
        return imgs

    def build_messages_payload(
        self,
        guild: discord.Guild | None,
        user_id: int,
        channel_id: int | None = None,
    ) -> list[dict]:
        guild_state = self.get_guild_state(guild, user_id, channel_id)
        messages = [{"role": "system", "content": BASE_SYSTEM_PROMPT}]
        messages.extend(guild_state["messages"])
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
                            if img_url:
                                parts.append({"type": "input_image", "image_url": img_url})
                    input_items.append({"role": "user", "content": parts})
                else:
                    input_items.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": str(content)}],
                    })
        return instructions, input_items

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

    async def sync_commands_to_guilds(self):
        try:
            await self.http.bulk_upsert_global_commands(self.user.id, [])
            for guild in self.guilds:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        except Exception:
            await self.tree.sync()

    async def close(self):
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
                "description": "Membuat gambar baru dari teks ATAU mengedit/memodifikasi gambar yang dilampirkan pengguna (seperti mengganti teks, warna, objek, atau gaya). Panggil HANYA jika pengguna meminta dibuatkan atau diedit gambar.",
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
                    },
                    "required": ["prompt", "use_attached_image"],
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
        last_edit = asyncio.get_event_loop().time()
        web_badge_shown = False

        async with self.http_session.post(url, headers=headers, json=payload, timeout=timeout) as resp:
            if resp.status != 200:
                err_text = await resp.text()
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
                        if now - last_edit >= 0.8 and cleaned_live and len(cleaned_live) <= 1950:
                            try:
                                if target_msg is not None:
                                    await target_msg.edit(content=cleaned_live + " ▌")
                                last_edit = now
                            except Exception:
                                pass

                elif etype == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        function_calls.append(item)
                    elif item.get("type") == "message" and not raw_full_text:
                        for part in item.get("content", []):
                            if part.get("type") in ("output_text", "text"):
                                raw_full_text += part.get("text", "")

                elif etype == "response.completed":
                    resp_obj = event.get("response", {})
                    for item in resp_obj.get("output", []):
                        if item.get("type") == "function_call" and not any(fc.get("id") == item.get("id") for fc in function_calls):
                            function_calls.append(item)
                        elif item.get("type") == "message" and not raw_full_text:
                            for part in item.get("content", []):
                                if part.get("type") in ("output_text", "text"):
                                    raw_full_text += part.get("text", "")

                elif etype in ("error", "response.failed"):
                    err_msg = event.get("error", {}).get("message") or str(event)[:250]
                    raise RuntimeError(f"Responses Stream Error: {err_msg}")

        return clean_response_text(raw_full_text).strip(), function_calls

    async def process_ai_turn(
        self,
        target_channel: discord.abc.Messageable,
        guild: discord.Guild | None,
        user: discord.User | discord.Member,
        clean_content: str,
        image_data_urls: list[str],
        additional_file_text: str = "",
        reply_to_message: discord.Message | None = None,
    ):
        channel_id = getattr(target_channel, "id", 0)
        guild_id = guild.id if guild is not None else 0
        scope_key = self.get_scope_key(guild, user.id, channel_id)
        allow_img_tool = should_allow_image_tool(
            clean_content,
            has_file_or_pdf=bool(additional_file_text),
            has_image=bool(image_data_urls),
        )

        async with self.guild_locks[scope_key]:
            guild_state = self.get_guild_state(guild, user.id, channel_id)
            mode = guild_state["thread_modes"].get(str(target_channel.id), "fast")
            history_msgs = guild_state["messages"]

            if image_data_urls and channel_id:
                self.set_last_user_image(guild_id, channel_id, user.id, image_data_urls)

            full_user_prompt = clean_content
            if additional_file_text:
                full_user_prompt += f"\n\n{additional_file_text}\n(Tolong baca dan analisis file di atas!)"

            user_display_text = f"{user.display_name}: {full_user_prompt}"

            if image_data_urls:
                blocks = [{"type": "text", "text": user_display_text}] + [
                    {"type": "image_url", "image_url": {"url": u}} for u in image_data_urls[:6]
                ]
                history_msgs.append({"role": "user", "content": blocks})
            else:
                history_msgs.append({"role": "user", "content": user_display_text})

            if len(history_msgs) > MAX_RECENT_MESSAGES:
                guild_state["messages"] = history_msgs[-MAX_RECENT_MESSAGES:]
                history_msgs = guild_state["messages"]

            payload_messages = self.build_messages_payload(guild, user.id, channel_id)

            if additional_file_text or image_data_urls:
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

                img_call = next((fc for fc in function_calls if fc.get("name") == "generate_image"), None)
                if img_call and not allow_img_tool:
                    call_id = img_call.get("call_id")
                    if call_id:
                        blocked_output = {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps({
                                "success": False,
                                "reason": "Pengguna hanya meminta analisis/penjelasan, bukan pembuatan atau pengeditan gambar."
                            }, ensure_ascii=False),
                        }
                        try:
                            blocked_text, _ = await self.run_responses_ai(
                                payload_messages,
                                status_msg,
                                mode=mode,
                                allow_tools=False,
                                allow_image_tool=False,
                                extra_input=[img_call, blocked_output],
                            )
                            if blocked_text:
                                reply_text = blocked_text
                        except Exception:
                            pass
                    img_call = None

                if img_call:
                    if user.id in self.image_busy_users:
                        await status_msg.edit(content="⏳ Proses gambar lu sebelumnya masih jalan, tunggu sebentar ya.")
                        return

                    self.image_busy_users.add(user.id)
                    try:
                        try:
                            args_dict = json.loads(img_call.get("arguments", "{}"))
                        except Exception:
                            args_dict = {}

                        img_prompt = (args_dict.get("prompt") or clean_content).strip()
                        use_attached = bool(args_dict.get("use_attached_image", False))

                        replied_imgs: list[str] = []
                        if reply_to_message is not None:
                            replied_imgs = await self.resolve_replied_images(reply_to_message)

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
                            fail_msg = (
                                "⚠️ Gagal memproses edit gambar: tidak ada gambar sumber yang ditemukan "
                                "(lampirkan gambar, *reply* ke pesan bergambar, atau pastikan konteks gambar sebelumnya masih tersedia)."
                            )
                            history_msgs.append({"role": "assistant", "content": fail_msg})
                            guild_state["turn_count"] = guild_state.get("turn_count", 0) + 1
                            await self.save_memory()
                            await status_msg.edit(content=fail_msg)
                            return

                        is_edit = bool(source_imgs)
                        action_badge = "🖌️ *Sedang mengedit gambar...*" if is_edit else "🎨 *Sedang membuat gambar...*"
                        await status_msg.edit(content=action_badge)

                        try:
                            img_bytes, out_filename, out_mime, engine_used = await self.generate_or_edit_image(
                                img_prompt, source_images=source_imgs
                            )
                        except Exception as img_exc:
                            if is_edit:
                                fail_msg = (
                                    f"⚠️ Edit gambar gagal dilakukan ({img_exc}). "
                                    f"Gambar tidak diubah dan tidak dialihkan ke generator gambar baru."
                                )
                                history_msgs.append({"role": "assistant", "content": fail_msg})
                                guild_state["turn_count"] = guild_state.get("turn_count", 0) + 1
                                await self.save_memory()
                                await status_msg.edit(content=fail_msg)
                                return
                            raise

                        call_id = img_call.get("call_id")
                        if call_id:
                            tool_output = {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps({
                                    "success": True,
                                    "operation": "edit_image" if is_edit else "generate_image",
                                    "engine": engine_used,
                                    "filename": out_filename,
                                    "detail": "Gambar berhasil dibuat dan dilampirkan ke pesan Discord."
                                }, ensure_ascii=False),
                            }
                            try:
                                final_text, _ = await self.run_responses_ai(
                                    payload_messages,
                                    status_msg,
                                    mode=mode,
                                    allow_tools=False,
                                    allow_image_tool=False,
                                    extra_input=[img_call, tool_output],
                                )
                                if final_text:
                                    reply_text = final_text
                            except Exception as follow_exc:
                                reply_text = (reply_text + f"\n\nGambar berhasil diproses. (Catatan: model tidak menyelesaikan respons lanjutan: {follow_exc})").strip()

                        if channel_id:
                            b64_out = base64.b64encode(img_bytes).decode("utf-8")
                            self.set_last_user_image(guild_id, channel_id, user.id, [f"data:{out_mime};base64,{b64_out}"])

                        file = discord.File(fp=io.BytesIO(img_bytes), filename=out_filename)

                        embed_title = "🖌️ Clouzer AI Image Editor" if is_edit else "🎨 Clouzer AI Image Generator"
                        embed_desc = f"**Prompt:** {img_prompt[:1800]}"
                        if is_edit:
                            embed_desc = f"{embed_desc[:1650]}\n\n{TEXT_EDIT_NOTE}"

                        embed = discord.Embed(
                            title=embed_title,
                            description=embed_desc,
                            color=discord.Color.blurple(),
                        )
                        embed.set_image(url=f"attachment://{out_filename}")
                        embed.set_footer(text=f"Created by clouzerd | API: {engine_used}")

                        extra_note = f"\n\n{TEXT_EDIT_NOTE}" if is_edit else ""
                        combined_text = f"{reply_text}{extra_note}".strip() if reply_text else (TEXT_EDIT_NOTE if is_edit else "")
                        caption_chunks = split_discord_chunks(combined_text, limit=1950) if combined_text else []
                        first_caption = caption_chunks[0] if caption_chunks else None

                        history_msgs.append({"role": "assistant", "content": f"{reply_text}\n[Gambar diproses ({engine_used}): {img_prompt}]".strip()})
                        guild_state["turn_count"] = guild_state.get("turn_count", 0) + 1
                        await self.save_memory()

                        await status_msg.edit(content=first_caption, embed=embed, attachments=[file])
                        for extra_chunk in caption_chunks[1:]:
                            await target_channel.send(extra_chunk)
                        return
                    finally:
                        self.image_busy_users.discard(user.id)

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
                )
                chunks = split_discord_chunks(reply_text, limit=1950)
                await status_msg.edit(content=chunks[0], view=ui_view)
                for chunk in chunks[1:]:
                    await target_channel.send(chunk)
            except Exception as e:
                if history_msgs:
                    history_msgs.pop()
                await status_msg.edit(content=f"⚠️ Error: `{type(e).__name__}: {e}`")


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

    guild_state = bot.get_guild_state(interaction.guild)
    if aksi.value == "list":
        allowed_ids = [cid for cid in guild_state["allowed_channels"] if interaction.guild.get_channel(cid) is not None]
        if not allowed_ids:
            await interaction.response.send_message("📋 Belum ada channel diizinkan.", ephemeral=True)
        else:
            mentions = "\n".join(f"• <#{cid}>" for cid in allowed_ids)
            await interaction.response.send_message(f"📋 **Channel Diizinkan:**\n{mentions}", ephemeral=True)
        return

    ch = target or (interaction.channel.parent if isinstance(interaction.channel, discord.Thread) else interaction.channel)
    if aksi.value == "izinkan":
        if ch.id not in guild_state["allowed_channels"]:
            guild_state["allowed_channels"].append(ch.id)
            await bot.save_memory()
        await interaction.response.send_message(f"✅ Izin diberikan di {ch.mention}!")
    elif aksi.value == "tolak":
        if ch.id in guild_state["allowed_channels"]:
            guild_state["allowed_channels"].remove(ch.id)
        await bot.save_memory()
        await interaction.response.send_message(f"🚫 Izin dicabut di {ch.mention}!")


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
            f"📋 **Ringkasan Obrolan Channel ({len(messages_text)} pesan terakhir):**\n\n{(summary_result or 'Tidak ada ringkasan.')[:1900]}"
        )
    except Exception:
        logging.exception("Summarize gagal untuk scope %s", scope_key)
        await interaction.followup.send("⚠️ Gagal merangkum obrolan. Cek log bot untuk detail error.")


@bot.tree.command(name="imagine", description="Buat gambar baru atau edit gambar menggunakan Cloudflare AI (+ Backup FLUX)")
@app_commands.describe(
    prompt="Deskripsi gambar baru atau instruksi edit (disarankan bahasa Inggris)",
    gambar="Opsional: Lampirkan gambar jika ingin diedit",
)
async def imagine_cmd(interaction: discord.Interaction, prompt: str, gambar: discord.Attachment | None = None):
    if not bot.is_channel_allowed(interaction.channel, interaction.guild):
        await interaction.response.send_message(get_permission_warning(interaction.guild), ephemeral=True)
        return

    if interaction.user.id in bot.image_busy_users:
        await interaction.response.send_message("⏳ Proses gambar lu sebelumnya masih jalan, tunggu sebentar ya.", ephemeral=True)
        return

    await interaction.response.send_message("💬 *Sedang memproses pesan...*")
    bot.image_busy_users.add(interaction.user.id)
    try:
        source_imgs: list[str] | None = None
        if gambar is not None:
            _, imgs_res, ftype = await bot.encode_attachment(gambar)
            if ftype != "image" or not imgs_res:
                await interaction.edit_original_response(content="⚠️ Lampiran harus berupa file gambar valid (`.png`, `.jpg`, `.jpeg`, `.webp`).")
                return
            source_imgs = imgs_res

        is_edit = bool(source_imgs)
        action_badge = "🖌️ *Sedang mengedit gambar...*" if is_edit else "🎨 *Sedang membuat gambar...*"
        await interaction.edit_original_response(content=action_badge)

        try:
            img_bytes, out_filename, out_mime, engine_used = await bot.generate_or_edit_image(prompt, source_images=source_imgs)
        except Exception as img_exc:
            if is_edit:
                await interaction.edit_original_response(
                    content=(
                        f"⚠️ Edit gambar gagal dilakukan ({img_exc}). "
                        f"Gambar tidak diubah dan tidak dialihkan ke generator gambar baru."
                    )
                )
                return
            raise

        ch_id = getattr(interaction.channel, "id", 0)
        g_id = interaction.guild.id if interaction.guild is not None else 0
        if ch_id:
            b64_out = base64.b64encode(img_bytes).decode("utf-8")
            bot.set_last_user_image(g_id, ch_id, interaction.user.id, [f"data:{out_mime};base64,{b64_out}"])

        file = discord.File(fp=io.BytesIO(img_bytes), filename=out_filename)

        embed_title = "🖌️ Clouzer AI Image Editor" if is_edit else "🎨 Clouzer AI Image Generator"
        embed_desc = f"**Prompt:** {prompt.strip()[:1800]}"
        if is_edit:
            embed_desc = f"{embed_desc[:1650]}\n\n{TEXT_EDIT_NOTE}"

        embed = discord.Embed(
            title=embed_title,
            description=embed_desc,
            color=discord.Color.blurple(),
        )
        embed.set_image(url=f"attachment://{out_filename}")
        embed.set_footer(text=f"Created by clouzerd | API: {engine_used}")

        await interaction.edit_original_response(content=None, embed=embed, attachments=[file])
    except Exception as e:
        await interaction.edit_original_response(content=f"⚠️ Terjadi kesalahan saat memproses gambar: `{type(e).__name__}: {e}`")
    finally:
        bot.image_busy_users.discard(interaction.user.id)


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
    await bot.sync_commands_to_guilds()
    print(f"[ONLINE] Clouzer AI Bot aktif (Cloudflare AI: {'OK (' + str(len(CF_ACCOUNTS)) + ' akun)' if CF_ACCOUNTS else 'MISSING'} | Gen: {CF_IMAGE_MODEL} | Edit: {CF_EDIT_MODEL} | PDF Reader + Native Web Search)!")
    print("[CHECK] Pastikan Message Content Intent juga aktif di Discord Developer Portal.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not bot.is_channel_allowed(message.channel, message.guild):
        return

    clean_content = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

    image_data_urls: list[str] = []
    additional_file_text = ""

    for att in message.attachments:
        text_res, imgs_res, ftype = await bot.encode_attachment(att)
        if text_res:
            additional_file_text += text_res
        if imgs_res:
            image_data_urls.extend(imgs_res)

    if not clean_content:
        if additional_file_text:
            clean_content = "Tolong baca, analisis, dan ringkas isi file yang saya kirim ini ya."
        elif image_data_urls:
            clean_content = "Tolong jelaskan isi gambar ini."
        else:
            return

    await bot.process_ai_turn(
        target_channel=message.channel,
        guild=message.guild,
        user=message.author,
        clean_content=clean_content,
        image_data_urls=image_data_urls,
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