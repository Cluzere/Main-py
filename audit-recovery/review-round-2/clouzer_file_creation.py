"""Pembuatan lampiran dokumen CLOUZER yang dibatasi dan tanpa akses filesystem bebas."""
from __future__ import annotations
import io, json, re, textwrap, time, zipfile

MAX_GENERATED_FILE_BYTES = 8 * 1024 * 1024
MAX_GENERATED_TEXT_CHARS = 400_000
_FORMAT_PATTERNS = {
    "txt": re.compile(r"(?<![a-z0-9])(?:\.txt|txt|plain text|teks biasa)(?![a-z0-9])", re.I),
    "pdf": re.compile(r"(?<![a-z0-9])(?:\.pdf|pdf)(?![a-z0-9])", re.I),
    "docx": re.compile(r"(?<![a-z0-9])(?:\.docx|docx|dokumen word|file word)(?![a-z0-9])", re.I),
    "xlsx": re.compile(r"(?<![a-z0-9])(?:\.xlsx|xlsx|excel|spreadsheet)(?![a-z0-9])", re.I),
    "pptx": re.compile(r"(?<![a-z0-9])(?:\.pptx|pptx|powerpoint|power point|presentasi|slide)(?![a-z0-9])", re.I),
    "zip": re.compile(r"(?<![a-z0-9])(?:\.zip|zip|arsip zip)(?![a-z0-9])", re.I),
}
_CREATE_RE = re.compile(r"\b(?:buatkan|bikinkan|bikin|buat|jadikan|simpan|ekspor|export|generate|create|susun)\b", re.I)
_EXPLICIT_ACTION_RE = re.compile(r"\b(?:buatkan|bikinkan|tolong buat|tolong bikin|buat file|bikin file|jadikan file|simpan sebagai|export ke)\b", re.I)

_FILE_RETRY_COMMAND_RE = re.compile(
    r"^(?:ulangi|ulang|coba lagi|buat lagi|lanjutkan|lanjut|teruskan|retry)(?:\s+(?:dong|ya|tolong|please))*[.!?]*$",
    re.I,
)
MAX_PENDING_FILE_PROMPT_CHARS = 10_000


def is_retry_command(prompt: str) -> bool:
    """Recognize a standalone retry command, independent of the failed feature."""
    return bool(_FILE_RETRY_COMMAND_RE.fullmatch(str(prompt or "").strip()))


def is_file_retry_command(prompt: str) -> bool:
    """Backward-compatible alias for older callers."""
    return is_retry_command(prompt)


def normalize_pending_retry_task(raw: object) -> dict | None:
    """Validate a failed operation's replay context; format is present only for file tasks."""
    if not isinstance(raw, dict):
        return None
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        return None
    if len(prompt) > MAX_PENDING_FILE_PROMPT_CHARS:
        prompt = prompt[:MAX_PENDING_FILE_PROMPT_CHARS]
    task = {"prompt": prompt}
    fmt = str(raw.get("format") or "").lower().lstrip(".")
    if fmt:
        if fmt not in _FORMAT_PATTERNS:
            return None
        task["format"] = fmt
    kind = str(raw.get("kind") or "").strip().lower()
    if kind in {"file", "image", "audio", "message", "other"}:
        task["kind"] = kind
    source_message_id = raw.get("source_message_id")
    if isinstance(source_message_id, int) and not isinstance(source_message_id, bool) and source_message_id > 0:
        task["source_message_id"] = source_message_id
    return task


def normalize_pending_file_task(raw: object) -> dict | None:
    """Backward-compatible validator for older file-only state."""
    task = normalize_pending_retry_task(raw)
    if not task or task.get("format") not in _FORMAT_PATTERNS:
        return None
    return {"format": task["format"], "prompt": task["prompt"]}



def detect_requested_file_format(prompt: str) -> str | None:
    value = str(prompt or "").strip()
    if not value or not _CREATE_RE.search(value):
        return None
    low = value.lower()
    if re.search(r"\b(?:bisa|apakah|sudah|udah|mampu|dapat)\b", low) and not _EXPLICIT_ACTION_RE.search(low):
        return None
    matches = [(match.start(), fmt) for fmt, pattern in _FORMAT_PATTERNS.items() if (match := pattern.search(value))]
    return min(matches)[1] if matches else None


def _safe_title(content: str, prompt: str) -> str:
    for line in str(content or "").splitlines():
        title = re.sub(r"^\s{0,3}#{1,6}\s*", "", line).strip()
        if title:
            return title[:100]
    return re.sub(r"\s+", " ", str(prompt or "Dokumen CLOUZER")).strip()[:100] or "Dokumen CLOUZER"


def _safe_filename(prompt: str, extension: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(prompt or "").lower()).strip("_")[:36] or "dokumen"
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    return f"clouzer_{slug}_{stamp}.{extension}"


def _validate_content(content: str) -> str:
    if not isinstance(content, str):
        raise ValueError("Isi dokumen harus berupa teks.")
    content = content.strip()
    if not content:
        raise ValueError("Model tidak menghasilkan isi dokumen.")
    if len(content) > MAX_GENERATED_TEXT_CHARS:
        raise ValueError("Isi dokumen melewati batas 400.000 karakter.")
    return content


def _make_pdf(content: str, title: str) -> bytes:
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF belum terpasang; PDF tidak bisa dibuat.") from exc
    wrapped = []
    for line in content.splitlines() or [content]:
        wrapped.extend(textwrap.wrap(line, width=96, break_long_words=True, break_on_hyphens=False) if line.strip() else [""])
    doc = fitz.open()
    try:
        for offset in range(0, max(1, len(wrapped)), 52):
            page = doc.new_page(width=595, height=842)
            page.insert_text((48, 48), title[:90], fontsize=14, fontname="helv")
            page.draw_line((48, 60), (547, 60), color=(0.35, 0.35, 0.35), width=0.6)
            page.insert_textbox(fitz.Rect(48, 76, 547, 790), "\n".join(wrapped[offset:offset + 52]), fontsize=9.5, fontname="helv", lineheight=1.2)
        return doc.tobytes(deflate=True)
    finally:
        doc.close()


def _make_docx(content: str, title: str) -> bytes:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx belum terpasang; DOCX tidak bisa dibuat.") from exc
    document = Document()
    document.add_heading(title, level=0)
    for block in re.split(r"\n\s*\n", content):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if len(lines) == 1 and lines[0].startswith("# "):
            document.add_heading(lines[0][2:].strip(), level=1)
        else:
            paragraph = document.add_paragraph()
            for index, line in enumerate(lines):
                if index:
                    paragraph.add_run().add_break()
                paragraph.add_run(line)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _make_xlsx(content: str, title: str) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError as exc:
        raise RuntimeError("openpyxl belum terpasang; XLSX tidak bisa dibuat.") from exc
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = re.sub(r"[\[\]:*?/\\]", " ", title).strip()[:31] or "Data"
    rows = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            rows.append([""])
        elif stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                continue
            rows.append(cells)
        elif "\t" in line:
            rows.append([cell.strip() for cell in line.split("\t")])
        else:
            rows.append([line])
    for row in rows or [[content]]:
        sheet.append([str(cell)[:32767] for cell in row])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="365F91")
    for column_cells in sheet.columns:
        letter = column_cells[0].column_letter
        max_width = max((len(str(cell.value or "")) for cell in column_cells), default=10)
        sheet.column_dimensions[letter].width = min(max(max_width + 2, 12), 48)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _make_pptx(content: str, title: str) -> bytes:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise RuntimeError("python-pptx belum terpasang; PPTX tidak bisa dibuat.") from exc
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    chunks = re.split(r"\n\s*---\s*SLIDE\s*---\s*\n", content, flags=re.I)
    if len(chunks) == 1:
        paragraphs = [part.strip() for part in content.splitlines() if part.strip()]
        if len(paragraphs) > 9:
            chunks = ["\n".join(paragraphs[index:index + 8]) for index in range(0, len(paragraphs), 8)]
    for index, chunk in enumerate(chunks):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        slide_title = title if index == 0 else f"{title} — {index + 1}"
        if lines:
            first = re.sub(r"^\s{0,3}#{1,6}\s*", "", lines[0]).strip()
            if first and (lines[0].startswith("#") or index == 0):
                slide_title, lines = first[:100], lines[1:]
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = slide_title
        body = slide.placeholders[1].text_frame
        body.clear()
        body.word_wrap = True
        for line_index, line in enumerate(lines[:30]):
            paragraph = body.paragraphs[0] if line_index == 0 else body.add_paragraph()
            paragraph.text = line[:1000]
            paragraph.font.size = Pt(18)
            paragraph.level = 0
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def _make_zip(content: str, title: str) -> bytes:
    output = io.BytesIO()
    manifest = {"created_by": "CLOUZER", "description": title, "files": ["konten.txt", "README.txt"]}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("README.txt", f"Arsip dibuat oleh CLOUZER.\n\n{title}\n")
        archive.writestr("konten.txt", content)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output.getvalue()


def create_document_file(format_name: str, prompt: str, content: str) -> tuple[bytes, str, str]:
    fmt = str(format_name or "").lower().lstrip(".")
    if fmt not in _FORMAT_PATTERNS:
        raise ValueError("Format file tidak didukung.")
    text = _validate_content(content)
    title = _safe_title(text, prompt)
    builders = {
        "txt": lambda: text.encode("utf-8"),
        "pdf": lambda: _make_pdf(text, title),
        "docx": lambda: _make_docx(text, title),
        "xlsx": lambda: _make_xlsx(text, title),
        "pptx": lambda: _make_pptx(text, title),
        "zip": lambda: _make_zip(text, title),
    }
    mime_types = {
        "txt": "text/plain; charset=utf-8",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "zip": "application/zip",
    }
    data = builders[fmt]()
    if not data:
        raise RuntimeError("File hasil pembuatan kosong.")
    if len(data) > MAX_GENERATED_FILE_BYTES:
        raise RuntimeError("File hasil melebihi batas aman 8 MB.")
    return data, _safe_filename(prompt, fmt), mime_types[fmt]
