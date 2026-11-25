"""Pembuatan lampiran dokumen CLOUZER yang dibatasi dan tanpa akses filesystem bebas."""
from __future__ import annotations
import io, json, re, textwrap, time, zipfile

MAX_GENERATED_FILE_BYTES = 8 * 1024 * 1024
MAX_GENERATED_TEXT_CHARS = 400_000
MAX_TABLE_ROWS = 10_000
MAX_TABLE_COLUMNS = 256
MAX_ZIP_ENTRIES = 100
_FORMAT_PATTERNS = {
    "txt": re.compile(r"(?<![a-z0-9])(?:\.txt|txt|plain text|teks biasa)(?![a-z0-9])", re.I),
    "pdf": re.compile(r"(?<![a-z0-9])(?:\.pdf|pdf)(?![a-z0-9])", re.I),
    "docx": re.compile(r"(?<![a-z0-9])(?:\.docx|docx|dokumen word|file word)(?![a-z0-9])", re.I),
    "xlsx": re.compile(r"(?<![a-z0-9])(?:\.xlsx|xlsx|excel|spreadsheet)(?![a-z0-9])", re.I),
    "pptx": re.compile(r"(?<![a-z0-9])(?:\.pptx|pptx|powerpoint|power point|presentasi|slide)(?![a-z0-9])", re.I),
    "zip": re.compile(r"(?<![a-z0-9])(?:\.zip|zip|arsip zip)(?![a-z0-9])", re.I),
    "csv": re.compile(r"(?<![a-z0-9])(?:\.csv|csv|comma[- ]separated values)(?![a-z0-9])", re.I),
    "json": re.compile(r"(?<![a-z0-9])(?:\.json|json)(?![a-z0-9])", re.I),
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
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match and match.group(1).strip():
            return match.group(1).strip()[:100]
    for line in str(content or "").splitlines():
        value = line.strip()
        if value and "\t" not in value and not (value.startswith("|") and value.endswith("|")):
            return value[:100]
    return "Dokumen CLOUZER"


def _requested_sheet_title(prompt: str) -> str | None:
    value = str(prompt or "")
    pattern = r"\b(?:worksheet|work\s*sheet|sheet|lembar\s+kerja)\b"
    quoted = re.search(pattern + r"\s*(?:bernama|dengan\s+nama|name|:)?\s*[\x60\"']([^\x60\"']{1,80})[\x60\"']", value, re.I)
    match = quoted or re.search(
        pattern + r"\s*(?:bernama|dengan\s+nama|name|:)?\s*([A-Za-z0-9 _-]{1,80})",
        value, re.I,
    )
    if not match:
        return None
    title = match.group(1).strip(" _-")
    title = re.split(r"\s+(?:dengan|berisi|untuk|yang|sebanyak)\b", title, maxsplit=1, flags=re.I)[0]
    title = re.sub(r"[\[\]:*?/\\]", " ", title).strip()[:31]
    return title or None


def _safe_sheet_title(prompt: str) -> str:
    return _requested_sheet_title(prompt) or "Data"


_NUMERIC_XLSX_HEADER_RE = re.compile(
    r"(?:harga|price|biaya|cost|stok|stock|qty|kuantitas|jumlah|quantity|nomor|umur|usia|age|^no\.?$)",
    re.I,
)


def _prompt_pipe_table(prompt: str) -> list[list[str]]:
    runs = []
    current = []
    for line in str(prompt or "").splitlines():
        stripped = line.strip()
        if "|" not in stripped:
            if current:
                runs.append(current)
                current = []
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            if current:
                runs.append(current)
                current = []
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        if current and len(cells) != len(current[0]):
            runs.append(current)
            current = []
        current.append(cells)
    if current:
        runs.append(current)
    valid = [run for run in runs if len(run) >= 2 and len({len(row) for row in run}) == 1]
    return max(valid, key=len) if valid else []


def _safe_filename(prompt: str, extension: str) -> str:
    value = str(prompt or "")
    ext = re.escape(str(extension or "").lower().lstrip("."))
    quoted = re.search(r"[\x60\"']([A-Za-z0-9][A-Za-z0-9_-]{0,100}\." + ext + r")[\x60\"']", value, re.I)
    explicit = quoted or re.search(
        r"(?<![A-Za-z0-9_./\\])([A-Za-z0-9][A-Za-z0-9_-]{0,100}\." + ext + r")(?![A-Za-z0-9_])",
        value, re.I,
    )
    if explicit:
        return explicit.group(1)
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:36] or "dokumen"
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
    if any(x in content.lower() for x in ("gagal membuat file", "gagal: file", "tidak bisa membuat file", "tidak dapat membuat file", "tidak tersedia tool", "tidak dapat melampirkan", "cannot create a file")):
        raise ValueError("Model mengembalikan pesan kegagalan, bukan isi dokumen; tidak dikemas sebagai file.")
    return content


def _make_pdf(content: str, title: str) -> bytes:
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF belum terpasang; PDF tidak bisa dibuat.") from exc
    wrapped = []
    source_lines = content.splitlines() or [content]
    if source_lines:
        first = re.sub(r"^\s{0,3}#{1,6}\s*", "", source_lines[0]).strip()
        if first.casefold() == title.casefold():
            source_lines = source_lines[1:]
    for line in source_lines:
        wrapped.extend(textwrap.wrap(line, width=96, break_long_words=True, break_on_hyphens=False) if line.strip() else [""])
    doc = fitz.open()
    try:
        for offset in range(0, max(1, len(wrapped)), 52):
            page = doc.new_page(width=595, height=842)
            page.insert_text((48, 48), title[:90], fontsize=14, fontname="helv")
            page.draw_line((48, 60), (547, 60), color=(0.35, 0.35, 0.35), width=0.6)
            remaining = page.insert_textbox(
                fitz.Rect(48, 76, 547, 790),
                "\n".join(wrapped[offset:offset + 52]),
                fontsize=9.5, fontname="helv", lineheight=1.2,
            )
            if remaining < 0:
                raise ValueError("Teks PDF tidak muat pada layout; konten tidak akan dipotong diam-diam.")
        return doc.tobytes(deflate=True)
    finally:
        doc.close()

def _make_docx(content: str, title: str) -> bytes:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx belum terpasang; DOCX tidak bisa dibuat.") from exc
    document = Document()
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
    first_heading = re.sub(r"^#\s+", "", blocks[0]).strip() if blocks and re.match(r"^#\s+", blocks[0]) else ""
    first_line = blocks[0].splitlines()[0].strip() if blocks else ""
    if title.casefold() not in {first_heading.casefold(), first_line.casefold()}:
        document.add_heading(title, level=0)
    for block in blocks:
        lines = block.splitlines()
        if len(lines) == 1 and re.match(r"^#{1,6}\s+", lines[0]):
            match = re.match(r"^(#{1,6})\s+(.*)$", lines[0])
            document.add_heading(match.group(2).strip(), level=min(len(match.group(1)), 3))
        else:
            paragraph = document.add_paragraph()
            for index, line in enumerate(lines):
                if index:
                    paragraph.add_run().add_break()
                cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
                cleaned = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", cleaned)
                paragraph.add_run(cleaned)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _make_xlsx(content: str, prompt: str) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError as exc:
        raise RuntimeError("openpyxl belum terpasang; XLSX tidak bisa dibuat.") from exc
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _safe_sheet_title(prompt)
    rows = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        elif "\t" in line:
            cells = [cell.strip() for cell in line.split("\t")]
        elif "|" in line:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        else:
            rows.append([line])
            continue
        if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        rows = [[content]]
    _check_table(rows, prompt, "XLSX")
    expected_rows = _prompt_pipe_table(prompt)
    headers = [str(value).strip().casefold() for value in rows[0]]
    for row_index, row in enumerate(rows):
        values = []
        for column_index, cell in enumerate(row):
            value = str(cell)
            if len(value) > 32767:
                raise ValueError("Satu cell XLSX melebihi batas 32.767 karakter.")
            if row_index > 0 and column_index < len(headers) and _NUMERIC_XLSX_HEADER_RE.search(headers[column_index]):
                candidate = value.strip()
                if re.fullmatch(r"[+-]?(?:0|[1-9]\d*)(?:\.\d+)?", candidate):
                    value = float(candidate) if "." in candidate else int(candidate)
                else:
                    raise ValueError(f"Kolom {rows[0][column_index]} harus berupa angka pada baris {row_index + 1}.")
            values.append(value)
        sheet.append(values)
    if expected_rows and rows != expected_rows:
        raise ValueError("Data XLSX tidak sama persis dengan tabel sumber pada prompt; pembuatan dibatalkan.")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="365F91")
    for column_cells in sheet.columns:
        letter = column_cells[0].column_letter
        max_width = max((len(str(cell.value or "")) for cell in column_cells), default=10)
        sheet.column_dimensions[letter].width = min(max(max_width + 2, 12), 48)
    if sheet.max_row != len(rows) or sheet.max_column != len(rows[0]):
        raise ValueError("Validasi XLSX sebelum penyimpanan gagal: dimensi worksheet tidak sesuai.")
    if not any(cell.value is not None for row in sheet.iter_rows() for cell in row):
        raise ValueError("Validasi XLSX sebelum penyimpanan gagal: worksheet kosong.")
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
    if len(chunks) > 100:
        raise ValueError("PPTX melebihi batas 100 slide.")
    for index, chunk in enumerate(chunks):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        slide_title = title if index == 0 else f"{title} — {index + 1}"
        if lines:
            first = re.sub(r"^\s{0,3}#{1,6}\s*", "", lines[0]).strip()
            if first and (lines[0].startswith("#") or index == 0):
                slide_title, lines = first, lines[1:]
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = slide_title
        body = slide.placeholders[1].text_frame
        body.clear()
        body.word_wrap = True
        for line_index, line in enumerate(lines):
            paragraph = body.paragraphs[0] if line_index == 0 else body.add_paragraph()
            paragraph.text = line
            paragraph.font.size = Pt(18)
            paragraph.level = 0
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def _unfence(value: str) -> str:
    lines = value.strip().splitlines()
    tick = chr(96) * 3
    if len(lines) >= 3 and lines[0].startswith(tick) and lines[-1].strip() == tick:
        return "\n".join(lines[1:-1]).strip()
    return value.strip()


def _rows_from_text(value: str) -> list[list[str]]:
    import csv
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        return []
    if all(line.strip().startswith("|") and line.strip().endswith("|") for line in lines):
        rows = []
        for line in lines:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                continue
            rows.append(cells)
    else:
        delimiter = "\t" if "\t" in lines[0] else ","
        rows = list(csv.reader(io.StringIO(value), delimiter=delimiter))
        rows = [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []
    width = max(map(len, rows))
    if width > MAX_TABLE_COLUMNS or len(rows) > MAX_TABLE_ROWS + 1:
        raise ValueError("Tabel melewati batas 10.000 baris data atau 256 kolom.")
    return [row + [""] * (width - len(row)) for row in rows]

def _check_table(rows: list[list[str]], prompt: str, label: str) -> None:
    if len(rows) < 2 or not rows[0]:
        raise ValueError(label + " harus memiliki header dan setidaknya satu baris data.")
    if len(rows) - 1 > MAX_TABLE_ROWS or len(rows[0]) > MAX_TABLE_COLUMNS:
        raise ValueError(label + " melewati batas 10.000 baris data atau 256 kolom.")
    low = prompt.lower()
    row_match = re.search(r"\b(\d{1,4})\s*(?:baris data|baris|rows?|produk(?:s)?|products?|records?)\b", low)
    col_match = re.search(r"\b(\d{1,3})\s*(?:kolom|columns?)\b", low)
    allow_more = bool(re.search(r"\b(?:minimal|setidaknya|paling sedikit|at least)\b", low))
    if row_match:
        expected = int(row_match.group(1))
        actual = len(rows) - 1
        if (actual < expected) if allow_more else (actual != expected):
            qualifier = "minimal" if allow_more else "tepat"
            raise ValueError(f"{label} berisi {actual} baris data; permintaan {qualifier} {expected}.")
    if col_match:
        expected = int(col_match.group(1))
        actual = len(rows[0])
        if actual != expected:
            raise ValueError(f"{label} memiliki {actual} kolom; permintaan tepat {expected} kolom.")
    if any(len(row) != len(rows[0]) for row in rows):
        raise ValueError(label + " memiliki jumlah kolom yang tidak konsisten.")

def _make_csv(content: str, prompt: str) -> bytes:
    import csv
    value = _unfence(content)
    rows = _rows_from_text(value)
    if not rows:
        rows = list(csv.reader(io.StringIO(value)))
    _check_table(rows, prompt, "CSV")
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def _make_json(content: str) -> bytes:
    value = _unfence(content)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON tidak valid pada baris {exc.lineno}, kolom {exc.colno}.") from exc
    return (json.dumps(parsed, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _make_zip(content: str, title: str, prompt: str = "") -> bytes:
    from pathlib import PurePosixPath
    marker = re.compile(r"(?m)^===\s*FILE:\s*([^=\r\n]+?)\s*===\s*$")
    matches = list(marker.finditer(content))
    files = {}
    if matches:
        for i, match in enumerate(matches):
            raw_name = match.group(1).strip().replace("\\", "/")
            path = PurePosixPath(raw_name)
            name = path.as_posix()
            if (not raw_name or "\x00" in raw_name or path.is_absolute() or ".." in path.parts
                    or name in files or re.match(r"^[A-Za-z]:", raw_name) or name in {"", "."}):
                raise ValueError("Nama anggota ZIP tidak aman atau duplikat: " + raw_name)
            if len(name) > 240:
                raise ValueError("Nama anggota ZIP terlalu panjang.")
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[match.end():end].lstrip("\r\n")
            files[name] = body.rstrip("\r\n") + "\n"
    else:
        files = {"README.txt": f"Arsip dibuat oleh CLOUZER.\n{title}\n", "konten.txt": content}
    required = set(re.findall(r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.(?:txt|json|csv)\b", prompt, re.I))
    missing = sorted(required - set(files))
    if missing:
        raise ValueError("ZIP tidak memiliki anggota yang diminta: " + ", ".join(missing))
    if len(files) > MAX_ZIP_ENTRIES:
        raise ValueError("ZIP melebihi batas 100 anggota.")
    for name, value in files.items():
        if name.lower().endswith(".json"):
            json.loads(_unfence(value))
        elif name.lower().endswith(".csv"):
            rows = _rows_from_text(_unfence(value))
            if len(rows) < 2 or any(len(row) != len(rows[0]) for row in rows):
                raise ValueError("CSV internal tidak valid: " + name)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    data = out.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as check:
        if check.testzip() or set(check.namelist()) != set(files):
            raise RuntimeError("Validasi integritas ZIP gagal.")
    return data
def validate_document_file(format_name: str, prompt: str, data: bytes) -> dict:
    fmt = str(format_name or "").lower().lstrip(".")
    if fmt not in _FORMAT_PATTERNS:
        raise ValueError("Format validasi tidak didukung.")
    if not isinstance(data, bytes) or not data or len(data) > MAX_GENERATED_FILE_BYTES:
        raise ValueError("File kosong atau melewati batas 8 MB.")
    result = {"format": fmt, "bytes": len(data), "valid": False}
    if fmt == "txt":
        if not data.decode("utf-8-sig").strip():
            raise ValueError("TXT kosong.")
    elif fmt == "pdf":
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.page_count < 1 or not "".join(page.get_text() for page in doc).strip():
                raise ValueError("PDF tidak memiliki halaman atau teks.")
            result["pages"] = doc.page_count
    elif fmt == "docx":
        from docx import Document
        doc = Document(io.BytesIO(data))
        text_content = "\n".join(p.text for p in doc.paragraphs)
        text_content += "\n" + "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
        if not text_content.strip():
            raise ValueError("DOCX tidak memiliki teks.")
    elif fmt == "xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            if not wb.sheetnames or wb.active.max_row > MAX_TABLE_ROWS + 1 or wb.active.max_column > MAX_TABLE_COLUMNS:
                raise ValueError("XLSX melewati batas tabel atau tidak memiliki worksheet.")
            for name in wb.sheetnames:
                if not any(cell.value is not None for row in wb[name].iter_rows() for cell in row):
                    raise ValueError(f"XLSX memiliki worksheet kosong: {name}.")
            requested_sheet = _requested_sheet_title(prompt)
            if requested_sheet and wb.active.title != requested_sheet:
                raise ValueError(f"Worksheet bernama {wb.active.title}; prompt meminta {requested_sheet}.")
            rows = [[str(cell or "") for cell in row] for row in wb.active.iter_rows(values_only=True)]
            _check_table(rows, prompt, "XLSX")
            expected_rows = _prompt_pipe_table(prompt)
            if expected_rows and rows != expected_rows:
                raise ValueError("Data XLSX berbeda dari tabel sumber pada prompt.")
            headers = [str(value).strip().casefold() for value in rows[0]]
            for index, header in enumerate(headers):
                if _NUMERIC_XLSX_HEADER_RE.search(header):
                    for row_number, row in enumerate(wb.active.iter_rows(min_row=2, values_only=False), 2):
                        value = row[index].value if index < len(row) else None
                        if not isinstance(value, (int, float)) or isinstance(value, bool):
                            raise ValueError(f"Kolom {rows[0][index]} harus berisi angka pada baris {row_number}.")
            result.update({"rows": len(rows) - 1, "columns": len(rows[0]), "sheets": len(wb.sheetnames), "sheet_name": wb.active.title})
        finally:
            wb.close()
    elif fmt == "pptx":
        from pptx import Presentation
        prs = Presentation(io.BytesIO(data))
        if not prs.slides:
            raise ValueError("PPTX tidak memiliki slide.")
        if len(prs.slides) > 100:
            raise ValueError("PPTX melebihi batas 100 slide.")
        for i, slide in enumerate(prs.slides, 1):
            text = " ".join(shape.text.strip() for shape in slide.shapes if getattr(shape, "has_text_frame", False))
            if not text.strip() or "gagal: file" in text.lower():
                raise ValueError(f"Slide {i} kosong atau berisi pesan kegagalan.")
        match = re.search(r"\b(\d{1,2})\s*slide", prompt, re.I)
        if match and len(prs.slides) != int(match.group(1)):
            raise ValueError(f"PPTX berisi {len(prs.slides)} slide, bukan {match.group(1)}.")
        result["slides"] = len(prs.slides)
    elif fmt == "zip":
        import csv
        from pathlib import PurePosixPath
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) > MAX_ZIP_ENTRIES or len(names) != len(set(names)):
                raise ValueError("ZIP memiliki anggota berlebih atau nama anggota duplikat.")
            if sum(info.file_size for info in infos) > MAX_GENERATED_FILE_BYTES:
                raise ValueError("Isi ZIP melewati batas total 8 MB.")
            if archive.testzip():
                raise ValueError("ZIP rusak.")
            normalized_names = set()
            for name in names:
                normalized = name.replace("\\", "/")
                path = PurePosixPath(normalized)
                canonical = path.as_posix()
                if (not normalized or "\x00" in normalized or path.is_absolute()
                        or ".." in path.parts or re.match(r"^[A-Za-z]:", normalized)
                        or canonical in normalized_names or canonical in {"", "."}):
                    raise ValueError("ZIP memiliki path anggota tidak aman atau duplikat.")
                normalized_names.add(canonical)
            required = set(re.findall(r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.(?:txt|json|csv)\b", prompt, re.I))
            missing = sorted(required - set(names))
            if missing:
                raise ValueError("ZIP kehilangan anggota wajib: " + ", ".join(missing))
            for name in names:
                raw = archive.read(name)
                if name.lower().endswith(".json"):
                    json.loads(raw.decode("utf-8-sig"))
                elif name.lower().endswith(".csv"):
                    rows = _rows_from_text(raw.decode("utf-8-sig"))
                    if len(rows) < 2 or any(len(row) != len(rows[0]) for row in rows):
                        raise ValueError("CSV internal tidak valid: " + name)
            result["members"] = names
    elif fmt == "csv":
        rows = _rows_from_text(data.decode("utf-8-sig"))
        _check_table(rows, prompt, "CSV")
        result.update({"rows": len(rows) - 1, "columns": len(rows[0])})
    elif fmt == "json":
        json.loads(data.decode("utf-8-sig"))
    result["valid"] = True
    return result

def create_document_file(format_name: str, prompt: str, content: str) -> tuple[bytes, str, str]:
    fmt = str(format_name or "").lower().lstrip(".")
    if fmt not in _FORMAT_PATTERNS:
        raise ValueError("Format file tidak didukung.")
    try:
        text = _validate_content(content)
    except ValueError as exc:
        # XLSX can be built deterministically from an explicit table already present
        # in the user's prompt when the model refuses/returns a failure message.
        # Never synthesize rows: the prompt must contain a complete rectangular table.
        if fmt != "xlsx" or "Model mengembalikan pesan kegagalan" not in str(exc):
            raise
        prompt_rows = _prompt_pipe_table(prompt)
        if len(prompt_rows) < 2 or not prompt_rows[0]:
            raise
        text = "\\n".join("\\t".join(row) for row in prompt_rows)
    title = _safe_title(text, prompt)
    builders = {
        "txt": lambda: text.encode("utf-8"),
        "pdf": lambda: _make_pdf(text, title),
        "docx": lambda: _make_docx(text, title),
        "xlsx": lambda: _make_xlsx(text, prompt),
        "pptx": lambda: _make_pptx(text, title),
        "zip": lambda: _make_zip(text, title, prompt),
        "csv": lambda: _make_csv(text, prompt),
        "json": lambda: _make_json(text),
    }
    mime_types = {
        "txt": "text/plain; charset=utf-8",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "zip": "application/zip",
        "csv": "text/csv; charset=utf-8",
        "json": "application/json; charset=utf-8",
    }
    data = builders[fmt]()
    if not data:
        raise RuntimeError("File hasil pembuatan kosong.")
    if len(data) > MAX_GENERATED_FILE_BYTES:
        raise RuntimeError("File hasil melebihi batas aman 8 MB.")
    validate_document_file(fmt, prompt, data)
    return data, _safe_filename(prompt, fmt), mime_types[fmt]
