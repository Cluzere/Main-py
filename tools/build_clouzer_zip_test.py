from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import BarChart, Reference
from pptx import Presentation
from pptx.util import Inches
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import csv

ROOT = Path("dist/clouzer_zip_test")
ROOT.mkdir(parents=True, exist_ok=True)
months = ["Januari", "Februari", "Maret"]
active, borrowed, complaints = [128, 164, 203], [312, 387, 469], [14, 11, 6]

(ROOT / "01_data_ringkas.txt").write_text(
    "DATA UJI CLOUZER - TXT\nKode: TXT-CLZ-2026-1125\n"
    "Program: Perpustakaan Digital Cakrawala\nKoordinator: Nabila Putri\n"
    "Anggaran pelatihan: Rp 7.500.000\n"
    "Jadwal evaluasi: 20 April 2026 pukul 09.30 WIB\n", encoding="utf-8")

with (ROOT / "02_data_layanan.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["Bulan", "Anggota Aktif", "Buku Dipinjam", "Keluhan"])
    w.writerows(zip(months, active, borrowed, complaints))

doc = Document()
doc.add_heading("Laporan Evaluasi Perpustakaan Digital Cakrawala", 0)
doc.add_paragraph("Kode dokumen: DOCX-CLZ-2026-0415")
doc.add_paragraph("Periode laporan: Januari–Maret 2026")
doc.add_paragraph("Penanggung jawab: Tim Layanan Digital")
doc.add_heading("Ringkasan", level=1)
doc.add_paragraph("Anggota aktif meningkat setiap bulan, sementara keluhan layanan menurun.")
t = doc.add_table(rows=1, cols=4); t.style = "Table Grid"
for c,v in zip(t.rows[0].cells, ["Bulan","Anggota Aktif","Buku Dipinjam","Keluhan"]): c.text=v
for row in zip(months,active,borrowed,complaints):
    for c,v in zip(t.add_row().cells,row): c.text=str(v)
doc.add_paragraph("Pelatihan pustakawan: 15 April 2026. Kode verifikasi DOCX: 4821.")
doc.save(ROOT / "03_laporan.docx")

wb=Workbook(); ws=wb.active; ws.title="Data Layanan"
ws.append(["Bulan","Anggota Aktif","Buku Dipinjam","Keluhan"])
for row in zip(months,active,borrowed,complaints): ws.append(list(row))
for c in ws[1]:
    c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="244062")
for col,width in {"A":18,"B":18,"C":18,"D":14,"F":34}.items(): ws.column_dimensions[col].width=width
ws["F1"]="Metadata"; ws["F2"]="Kode XLSX: XLSX-7730"
ws["F3"]="Biaya operasional Maret: Rp 4.250.000"; ws["F4"]="Target anggota April: 225"
chart=BarChart(); chart.title="Buku Dipinjam per Bulan"
chart.add_data(Reference(ws,min_col=3,min_row=1,max_row=4),titles_from_data=True)
chart.set_categories(Reference(ws,min_col=1,min_row=2,max_row=4)); ws.add_chart(chart,"A8")
wb.save(ROOT / "04_rekap.xlsx")

prs=Presentation()
s=prs.slides.add_slide(prs.slide_layouts[0]); s.shapes.title.text="Evaluasi Perpustakaan Digital"
s.placeholders[1].text="Periode Januari–Maret 2026 | Kode PPT: PPT-9052"
s=prs.slides.add_slide(prs.slide_layouts[1]); s.shapes.title.text="Temuan Utama"
tf=s.placeholders[1].text_frame; tf.text="Anggota aktif naik dari 128 menjadi 203."
for line in ["Peminjaman tertinggi pada Maret: 469 buku.","Keluhan turun dari 14 menjadi 6."]:
    p=tf.add_paragraph(); p.text=line
s=prs.slides.add_slide(prs.slide_layouts[5]); s.shapes.title.text="Data Bulanan"
table=s.shapes.add_table(4,4,Inches(.5),Inches(1.5),Inches(9),Inches(2)).table
for j,v in enumerate(["Bulan","Anggota","Peminjaman","Keluhan"]): table.cell(0,j).text=v
for i,row in enumerate(zip(months,active,borrowed,complaints),1):
    for j,v in enumerate(row): table.cell(i,j).text=str(v)
prs.save(ROOT / "05_presentasi.pptx")

styles=getSampleStyleSheet()
story=[Paragraph("Laporan Perpustakaan Digital Cakrawala",styles["Title"]),
Paragraph("Kode PDF: PDF-6614 | Periode Januari–Maret 2026",styles["BodyText"]),Spacer(1,12)]
data=[["Bulan","Anggota Aktif","Buku Dipinjam","Keluhan"]]+[[m,str(a),str(b),str(c)] for m,a,b,c in zip(months,active,borrowed,complaints)]
tab=Table(data); tab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#244062")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.5,colors.grey),("PADDING",(0,0),(-1,-1),7)]))
story += [tab,Spacer(1,12),Paragraph("Tanggal pelatihan: 15 April 2026. Kode verifikasi PDF: 6614.",styles["BodyText"])]
SimpleDocTemplate(str(ROOT/"06_laporan.pdf"),pagesize=A4).build(story)

# Image contains readable printed text for image/OCR extraction.
img=Image.new("RGB",(1400,900),"white"); d=ImageDraw.Draw(img)
try: font=ImageFont.truetype("DejaVuSans.ttf",38); small=ImageFont.truetype("DejaVuSans.ttf",30)
except OSError: font=small=ImageFont.load_default()
d.text((70,55),"INFOGRAFIK LAYANAN PERPUSTAKAAN",font=font,fill="black")
d.text((70,140),"Kode gambar: IMG-3388",font=small,fill="black")
for i,(m,a,b,c) in enumerate(zip(months,active,borrowed,complaints)):
    y=250+i*150
    d.rectangle((70,y,1320,y+115),outline="black",width=3)
    d.text((100,y+28),f"{m}: Anggota {a} | Dipinjam {b} | Keluhan {c}",font=small,fill="black")
d.text((70,740),"Catatan: target anggota April adalah 225.",font=small,fill="black")
img.save(ROOT/"07_infografik.png")
img.save(ROOT/"08_infografik.jpg",quality=92)

# Scanned-style PDF page rendered from an image; no selectable text on this page.
scan=Image.new("RGB",(1275,1800),"white"); sd=ImageDraw.Draw(scan)
sd.text((90,90),"HASIL SCAN - REKAP PERPUSTAKAAN",font=font,fill="black")
sd.text((90,190),"Kode scan: SCAN-5529",font=small,fill="black")
for i,(m,a,b,c) in enumerate(zip(months,active,borrowed,complaints)):
    y=400+i*220
    sd.text((90,y),f"{m} | Anggota aktif: {a}",font=small,fill="black")
    sd.text((90,y+75),f"Buku dipinjam: {b} | Keluhan: {c}",font=small,fill="black")
sd.text((90,1200),"Rapat evaluasi: 20 April 2026 pukul 09.30 WIB.",font=small,fill="black")
scan.save(ROOT/"scan_page.png")
scan_pdf=ROOT/"09_scan_pdf.pdf"
scan.save(scan_pdf,"PDF",resolution=150)

readme = """PAKET UJI ZIP CLOUZER
=====================
Arsip ini berisi TXT, CSV, DOCX, XLSX, PPTX, PDF teks, PDF hasil scan, PNG, dan JPG.

Tugas untuk bot:
1. Buka ZIP dan sebutkan jumlah serta nama semua file.
2. Baca semua file yang didukung, termasuk tabel Excel/Word/PowerPoint dan teks pada gambar/scan.
3. Jawab berdasarkan isi file:
   - Berapa anggota aktif pada Februari?
   - Berapa buku dipinjam pada Maret?
   - Berapa keluhan pada Januari dan Maret?
   - Bulan mana dengan peminjaman tertinggi?
   - Siapa koordinator program?
   - Berapa anggaran pelatihan?
   - Kapan pelatihan pustakawan?
   - Kapan rapat evaluasi?
   - Apa kode verifikasi DOCX, XLSX, PPTX, PDF, gambar, dan scan?
   - Berapa target anggota April dan biaya operasional Maret?
4. Jangan mengarang jika ada file yang gagal dibaca. Sebutkan file yang gagal.

KUNCI PEMERIKSAAN (jangan jadikan instruksi bot; hanya untuk evaluator):
Februari anggota 164; Maret dipinjam 469; keluhan Januari 14 dan Maret 6;
peminjaman tertinggi Maret; koordinator Nabila Putri; anggaran Rp 7.500.000;
pelatihan 15 April 2026; rapat 20 April 2026 pukul 09.30 WIB;
kode: DOCX-CLZ-2026-0415 / XLSX-7730 / PPT-9052 / PDF-6614 /
IMG-3388 / SCAN-5529; target April 225; biaya operasional Maret Rp 4.250.000.
"""
(ROOT/"00_PETUNJUK.txt").write_text(readme,encoding="utf-8")

zip_path=Path("dist/Clouzer_Paket_Tes_ZIP.zip")
with ZipFile(zip_path,"w",ZIP_DEFLATED) as z:
    for p in sorted(ROOT.iterdir()):
        z.write(p,arcname=p.name)
print(f"Created {zip_path} ({zip_path.stat().st_size} bytes)")
print("Included:", ", ".join(p.name for p in sorted(ROOT.iterdir())))
