import io
from datetime import datetime
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")

# Estimasi efektivitas mitigasi dalam menurunkan risiko obsolete (URS & Best Practice FMCG/Pharma)
EFEKTIVITAS_MITIGASI = {
    "Transfer Stok": 0.75,
    "Program Diskon Khusus": 0.60,
    "Retur Terjadwal": 0.85,
}
DEFAULT_EFEKTIVITAS = 0.70


def hitung_laporan_dampak(db, filter_obj=None) -> dict:
    """
    Menghitung ringkasan dan detail dampak finansial dari tindakan mitigasi yang diambil.
    Sesuai URS: 'Nilai risiko sebelum & sesudah mitigasi, ringkasan dampak finansial'.
    """
    from sqlalchemy import func, select
    from database import engine
    from models import SkuMaster, Transaksi, SkorRisiko
    from mitigasi import Mitigasi, fmt_waktu
    """
    Menghitung ringkasan dan detail dampak finansial dari tindakan mitigasi yang diambil.
    Sesuai URS: 'Nilai risiko sebelum & sesudah mitigasi, ringkasan dampak finansial'.
    """
    # 1. Total nilai retur historis per SKU
    retur_map = {
        sid: float(total or 0)
        for sid, total in db.query(Transaksi.sku_id, func.sum(func.abs(Transaksi.amount)))
        .filter(Transaksi.type == "RETURN")
        .group_by(Transaksi.sku_id)
        .all()
    }

    # 2. Ambil seluruh data mitigasi aktif
    query = db.query(Mitigasi).join(SkuMaster, SkuMaster.id == Mitigasi.sku_id)

    # Terapkan filter jika ada
    if filter_obj:
        if getattr(filter_obj, "sumber", None):
            query = query.filter(
                SkuMaster.id.in_(
                    select(Transaksi.sku_id).where(Transaksi.source == filter_obj.sumber)
                )
            )
        if getattr(filter_obj, "kategori", None):
            query = query.filter(func.coalesce(SkuMaster.catagory, "OTC") == filter_obj.kategori)
        if getattr(filter_obj, "q", None):
            like = f"%{filter_obj.q}%"
            query = query.filter(
                (SkuMaster.product_name.ilike(like)) | (SkuMaster.sku_code.ilike(like))
            )

    mitigasi_rows = query.order_by(Mitigasi.created_at.desc(), Mitigasi.id.desc()).all()

    items = []
    total_sebelum = 0.0
    total_sesudah = 0.0
    total_penghematan = 0.0

    breakdown_tindakan = {
        "Transfer Stok": {"count": 0, "penghematan": 0.0, "sebelum": 0.0, "sesudah": 0.0},
        "Program Diskon Khusus": {"count": 0, "penghematan": 0.0, "sebelum": 0.0, "sesudah": 0.0},
        "Retur Terjadwal": {"count": 0, "penghematan": 0.0, "sebelum": 0.0, "sesudah": 0.0},
    }

    for m in mitigasi_rows:
        sku = db.get(SkuMaster, m.sku_id)
        if not sku:
            continue

        # Skor risiko sebelum mitigasi: diambil saat pencatatan mitigasi, atau fallback ke skor terakhir
        if m.skor_saat_mitigasi is not None:
            skor_sebelum = float(m.skor_saat_mitigasi)
        else:
            skor_row = (
                db.query(SkorRisiko.skor)
                .filter(SkorRisiko.sku_id == sku.id)
                .order_by(SkorRisiko.id.desc())
                .first()
            )
            skor_sebelum = float(skor_row[0]) if skor_row else 0.50

        # Nilai potensi risiko awal (Nilai retur historis * Skor risiko AI)
        nilai_retur = retur_map.get(sku.id, 0.0)
        # Jika nilai retur 0 (misal produk baru/hanya sales), gunakan baseline estimasi
        if nilai_retur <= 0:
            sales_val = db.query(func.sum(Transaksi.amount)).filter(Transaksi.sku_id == sku.id, Transaksi.type == "SALES").scalar() or 0
            nilai_retur = abs(float(sales_val)) * 0.15

        nilai_risiko_sebelum = round(skor_sebelum * nilai_retur, 2)

        # Efektivitas mitigasi & nilai risiko residual setelah tindakan
        efektivitas = EFEKTIVITAS_MITIGASI.get(m.action_type, DEFAULT_EFEKTIVITAS)
        skor_sesudah = round(skor_sebelum * (1.0 - efektivitas), 4)
        nilai_risiko_sesudah = round(nilai_risiko_sebelum * (1.0 - efektivitas), 2)
        penghematan = round(nilai_risiko_sebelum - nilai_risiko_sesudah, 2)

        total_sebelum += nilai_risiko_sebelum
        total_sesudah += nilai_risiko_sesudah
        total_penghematan += penghematan

        tindakan_key = m.action_type if m.action_type in breakdown_tindakan else "Transfer Stok"
        breakdown_tindakan[tindakan_key]["count"] += 1
        breakdown_tindakan[tindakan_key]["penghematan"] += penghematan
        breakdown_tindakan[tindakan_key]["sebelum"] += nilai_risiko_sebelum
        breakdown_tindakan[tindakan_key]["sesudah"] += nilai_risiko_sesudah

        last_user = m.updated_by or m.created_by
        last_time = m.updated_at or m.created_at

        items.append({
            "id": m.id,
            "sku_code": m.sku_code,
            "product_name": sku.product_name,
            "category": sku.catagory or "OTC",
            "action_type": m.action_type,
            "notes": m.notes,
            "user_pic": last_user,
            "tanggal_mitigasi": fmt_waktu(last_time),
            "skor_sebelum": skor_sebelum,
            "skor_sesudah": skor_sesudah,
            "efektivitas_persen": round(efektivitas * 100),
            "nilai_risiko_sebelum": nilai_risiko_sebelum,
            "nilai_risiko_sesudah": nilai_risiko_sesudah,
            "penghematan": penghematan,
        })

    # Urutkan berdasarkan penghematan terbesar
    items.sort(key=lambda x: x["penghematan"], reverse=True)

    rasio_efektivitas = (
        round((total_penghematan / total_sebelum) * 100, 1) if total_sebelum > 0 else 0.0
    )

    now_wib = datetime.now(WIB).strftime("%d %b %Y, %H:%M WIB")

    return {
        "summary": {
            "total_sku_mitigasi": len(items),
            "total_nilai_sebelum": round(total_sebelum, 2),
            "total_nilai_sesudah": round(total_sesudah, 2),
            "total_penghematan": round(total_penghematan, 2),
            "rasio_efektivitas_persen": rasio_efektivitas,
            "breakdown_tindakan": breakdown_tindakan,
            "generated_at": now_wib,
        },
        "items": items,
    }


def generate_excel_laporan(data: dict, user_full_name: str) -> bytes:
    """
    Menghasilkan file Excel (.xlsx) resmi dengan metadata ALCOA,
    ringkasan KPI dampak finansial, dan rincian tabel per SKU.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Laporan Dampak Mitigasi"

    # Styling Palettes
    navy_header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    accent_kpi_fill = PatternFill(start_color="F0FDF4", end_color="F0FDF4", fill_type="solid")
    alt_row_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    table_head_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")

    font_title = Font(name="Calibri", size=16, bold=True, color="1E3A8A")
    font_subtitle = Font(name="Calibri", size=11, bold=True, color="475569")
    font_meta = Font(name="Calibri", size=9, italic=True, color="64748B")
    font_head = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_regular = Font(name="Calibri", size=10)
    font_green_bold = Font(name="Calibri", size=11, bold=True, color="15803D")

    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    # 1. Header & Identitas Sistem
    ws["A1"] = "ONEKalbe EWS - SISTEM PREDIKSI RISIKO RETUR OBSOLETE"
    ws["A1"].font = font_title
    ws["A2"] = "LAPORAN RINGKASAN DAMPAK FINANSIAL TINDAKAN MITIGASI"
    ws["A2"].font = font_subtitle

    summary = data["summary"]
    now_str = summary.get("generated_at", datetime.now(WIB).strftime("%d %b %Y %H:%M WIB"))

    ws["A3"] = f"Dicetak oleh: {user_full_name}  |  Tanggal Ekspor: {now_str}"
    ws["A3"].font = font_meta
    ws["A4"] = "Keterangan: Dokumen ini dihasilkan secara elektronik dan sah tanpa tanda tangan basah (ALCOA Standard)."
    ws["A4"].font = font_meta

    # 2. Ringkasan Eksekutif (KPI Box)
    ws["A6"] = "RINGKASAN DAMPAK FINANSIAL"
    ws["A6"].font = Font(name="Calibri", size=11, bold=True, color="1E293B")

    kpis = [
        ("Total SKU Dimitigasi", f"{summary['total_sku_mitigasi']} SKU"),
        ("Nilai Risiko Sebelum Mitigasi", summary["total_nilai_sebelum"]),
        ("Nilai Risiko Sesudah Mitigasi (Residual)", summary["total_nilai_sesudah"]),
        ("Estimasi Kerugian Dicegah (Cost Avoidance)", summary["total_penghematan"]),
        ("Rata-Rata Rasio Penghematan", f"{summary['rasio_efektivitas_persen']}%"),
    ]

    for col_idx, (label, val) in enumerate(kpis, start=1):
        cell_lbl = ws.cell(row=7, column=col_idx, value=label)
        cell_lbl.font = Font(name="Calibri", size=9, bold=True, color="475569")
        cell_lbl.fill = accent_kpi_fill
        cell_lbl.border = thin_border
        cell_lbl.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        cell_val = ws.cell(row=8, column=col_idx, value=val)
        cell_val.font = font_green_bold if "Kerugian Dicegah" in label else font_bold
        cell_val.fill = accent_kpi_fill
        cell_val.border = thin_border
        cell_val.alignment = Alignment(horizontal="center", vertical="center")
        if isinstance(val, (int, float)):
            cell_val.number_format = '"Rp "#,##0'

    # 3. Tabel Detail Mitigasi per SKU
    headers = [
        "No",
        "Kode SKU",
        "Nama Produk",
        "Kategori",
        "Tindakan Mitigasi",
        "PIC Pelaksana",
        "Tanggal Mitigasi",
        "Skor Sebelum",
        "Nilai Risiko Sebelum",
        "Efektivitas",
        "Skor Sesudah",
        "Nilai Risiko Sesudah",
        "Kerugian Dicegah (Rp)",
        "Catatan Mitigasi",
    ]

    start_row = 10
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=col_idx, value=h)
        cell.font = font_head
        cell.fill = table_head_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[start_row].height = 28

    current_row = start_row + 1
    for idx, item in enumerate(data["items"], start=1):
        is_alt = idx % 2 == 0
        row_fill = alt_row_fill if is_alt else PatternFill(fill_type=None)

        values = [
            idx,
            item["sku_code"],
            item["product_name"],
            item["category"],
            item["action_type"],
            item["user_pic"],
            item["tanggal_mitigasi"],
            item["skor_sebelum"],
            item["nilai_risiko_sebelum"],
            item["efektivitas_persen"] / 100.0,
            item["skor_sesudah"],
            item["nilai_risiko_sesudah"],
            item["penghematan"],
            item["notes"],
        ]

        for col_idx, val in enumerate(values, start=1):
            c = ws.cell(row=current_row, column=col_idx, value=val)
            c.font = font_regular
            c.border = thin_border
            if row_fill.fill_type:
                c.fill = row_fill

            # Formatting kolom
            if col_idx in (1, 2, 7):
                c.alignment = Alignment(horizontal="center")
            elif col_idx in (8, 10, 11):
                c.alignment = Alignment(horizontal="center")
                c.number_format = "0.0%"
            elif col_idx in (9, 12):
                c.alignment = Alignment(horizontal="right")
                c.number_format = '"Rp "#,##0'
            elif col_idx == 13:
                c.alignment = Alignment(horizontal="right")
                c.font = font_green_bold
                c.number_format = '"Rp "#,##0'
            else:
                c.alignment = Alignment(horizontal="left")

        current_row += 1

    # Total Row
    total_row = current_row
    ws.cell(row=total_row, column=1, value="TOTAL")
    ws.cell(row=total_row, column=1).font = font_bold
    ws.cell(row=total_row, column=1).alignment = Alignment(horizontal="center")
    ws.cell(row=total_row, column=1).border = thin_border

    for c in range(2, 9):
        cell_empty = ws.cell(row=total_row, column=c, value="")
        cell_empty.border = thin_border

    c_seb = ws.cell(row=total_row, column=9, value=summary["total_nilai_sebelum"])
    c_seb.font = font_bold
    c_seb.number_format = '"Rp "#,##0'
    c_seb.border = thin_border

    ws.cell(row=total_row, column=10, value="")
    ws.cell(row=total_row, column=10).border = thin_border
    ws.cell(row=total_row, column=11, value="")
    ws.cell(row=total_row, column=11).border = thin_border

    c_ses = ws.cell(row=total_row, column=12, value=summary["total_nilai_sesudah"])
    c_ses.font = font_bold
    c_ses.number_format = '"Rp "#,##0'
    c_ses.border = thin_border

    c_save = ws.cell(row=total_row, column=13, value=summary["total_penghematan"])
    c_save.font = font_green_bold
    c_save.number_format = '"Rp "#,##0'
    c_save.border = thin_border

    ws.cell(row=total_row, column=14, value="")
    ws.cell(row=total_row, column=14).border = thin_border

    # Auto-adjust column width
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            v_str = str(cell.value or "")
            if len(v_str) > max_len and "\n" not in v_str:
                max_len = len(v_str)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 11)
    ws.column_dimensions["C"].width = 30  # Nama Produk
    ws.column_dimensions["N"].width = 35  # Catatan Mitigasi

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def generate_pdf_laporan(data: dict, user_full_name: str) -> bytes:
    """
    Menghasilkan file PDF Landscape resmi (A4 Landscape: 841.89 x 595.28 pt)
    secara murni (pure Python) dengan tata letak corporate Kalbe,
    ringkasan dampak finansial lengkap, rincian tabel per SKU, dan baris TOTAL.
    """
    summary = data["summary"]
    items = data["items"]

    def esc(text: str) -> str:
        """Escape teks untuk PDF string literal."""
        if text is None:
            return ""
        s = str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return s.encode("latin1", "replace").decode("latin1")

    def fmt_idr(val: float) -> str:
        return f"Rp {int(val):,}".replace(",", ".")

    now_str = summary.get("generated_at", datetime.now(WIB).strftime("%d %b %Y %H:%M WIB"))

    # A4 Landscape: width = 841.89, height = 595.28 pt
    page_w = 841.89
    page_h = 595.28
    margin = 25.0
    table_w = page_w - 2 * margin

    pages_streams = []

    # Kapasitas baris per halaman:
    # Halaman 1 memuat Header + 4 Kartu KPI + Tabel (muat hingga 20 baris + 1 baris TOTAL).
    # Halaman berikutnya (jika data > 20) memuat hingga 26 baris.
    rows_page_1 = 20
    rows_page_next = 26

    chunked_items = []
    if not items:
        chunked_items = [[]]
    elif len(items) <= rows_page_1:
        chunked_items = [items]
    else:
        chunked_items.append(items[:rows_page_1])
        remaining = items[rows_page_1:]
        while remaining:
            chunked_items.append(remaining[:rows_page_next])
            remaining = remaining[rows_page_next:]

    total_pages = len(chunked_items)

    # Definisi 13 Kolom Lengkap (Sesuai Excel)
    # (x_pos, width, title, align)
    cols = [
        (margin + 3, 20, "No", "center"),
        (margin + 25, 44, "SKU", "left"),
        (margin + 71, 115, "Nama Produk", "left"),
        (margin + 188, 30, "Kat", "center"),
        (margin + 220, 80, "Tindakan", "left"),
        (margin + 302, 62, "PIC", "left"),
        (margin + 366, 46, "Tanggal", "center"),
        (margin + 414, 34, "Skor", "center"),
        (margin + 450, 72, "Risiko Awal", "right"),
        (margin + 524, 32, "Reduksi", "center"),
        (margin + 558, 72, "Risiko Akhir", "right"),
        (margin + 632, 74, "Dicegah (Rp)", "right"),
        (margin + 708, 80, "Catatan Mitigasi", "left"),
    ]

    for p_idx, page_rows in enumerate(chunked_items, start=1):
        ops = []
        is_first = p_idx == 1
        is_last = p_idx == total_pages

        # 1. Header Bar Korporat (Dark Navy #1E3A8A)
        ops.append("q")
        ops.append("0.118 0.227 0.541 rg")
        ops.append(f"{margin} {page_h - 56} {table_w} 32 re f")
        ops.append("Q")

        ops.append("BT")
        ops.append("/F2 12 Tf 1 1 1 rg")
        ops.append(f"1 0 0 1 {margin + 12} {page_h - 38} Tm (ONEKalbe EWS - LAPORAN RINGKASAN DAMPAK FINANSIAL MITIGASI) Tj")
        ops.append("/F1 7.5 Tf 0.85 0.9 1 rg")
        ops.append(f"1 0 0 1 {margin + 12} {page_h - 50} Tm (Sistem AI Prediksi Obsolete • PT Kalbe Farma & PT Saka Farma Laboratories) Tj")
        ops.append("/F1 7.5 Tf 1 1 1 rg")
        ops.append(f"1 0 0 1 {page_w - margin - 230} {page_h - 40} Tm (Dicetak: {esc(now_str)}) Tj")
        ops.append(f"1 0 0 1 {page_w - margin - 230} {page_h - 50} Tm (User: {esc(user_full_name)}) Tj")
        ops.append("ET")

        y_cursor = page_h - 68

        # 2. Empat Kartu KPI Ringkasan (Halaman Pertama)
        if is_first:
            card_gap = 10.0
            card_w = (table_w - 3 * card_gap) / 4
            card_h = 44.0
            cy = y_cursor - card_h

            kpi_data = [
                ("Total SKU Dimitigasi", f"{summary['total_sku_mitigasi']} SKU", "0.08 0.22 0.54"),
                ("Risiko Sebelum Mitigasi", fmt_idr(summary["total_nilai_sebelum"]), "0.75 0.15 0.15"),
                ("Risiko Sesudah (Residual)", fmt_idr(summary["total_nilai_sesudah"]), "0.25 0.3 0.4"),
                ("Kerugian Dicegah (Saved)", fmt_idr(summary["total_penghematan"]), "0.06 0.52 0.2"),
            ]

            for c_idx, (lbl, val, col) in enumerate(kpi_data):
                cx = margin + c_idx * (card_w + card_gap)

                # Kotak Kartu dengan border tipis dan background subtle
                ops.append("q")
                ops.append("0.97 0.98 0.99 rg 0.85 0.88 0.92 RG 0.75 w")
                ops.append(f"{cx} {cy} {card_w} {card_h} re B")
                ops.append("Q")

                # Label Kartu (Posisi Absolut dengan Tm)
                ops.append("BT")
                ops.append("/F1 7.5 Tf 0.4 0.45 0.52 rg")
                ops.append(f"1 0 0 1 {cx + 10} {cy + 27} Tm ({esc(lbl)}) Tj")
                # Nilai Kartu (Posisi Absolut dengan Tm, font tebal)
                ops.append(f"/F2 11.5 Tf {col} rg")
                ops.append(f"1 0 0 1 {cx + 10} {cy + 10} Tm ({esc(val)}) Tj")
                ops.append("ET")

            y_cursor = cy - 14

        # 3. Header Tabel (Dark Slate #0F172A)
        th_h = 18.0
        ops.append("q")
        ops.append("0.06 0.09 0.16 rg")
        ops.append(f"{margin} {y_cursor - th_h} {table_w} {th_h} re f")
        ops.append("Q")

        ops.append("BT")
        ops.append("/F2 7 Tf 1 1 1 rg")
        for x, w, title, align in cols:
            ops.append(f"1 0 0 1 {x} {y_cursor - 12.5} Tm ({esc(title)}) Tj")
        ops.append("ET")

        y_cursor -= th_h

        # 4. Baris-Baris Data Tabel
        row_h = 16.5
        start_row_num = sum(len(c) for c in chunked_items[: p_idx - 1]) + 1

        if not page_rows:
            ops.append("BT")
            ops.append("/F1 8 Tf 0.5 0.5 0.5 rg")
            ops.append(f"1 0 0 1 {margin + 15} {y_cursor - 12} Tm (Belum ada catatan tindakan mitigasi yang tersimpan di sistem.) Tj")
            ops.append("ET")
            y_cursor -= row_h
        else:
            for r_idx, item in enumerate(page_rows):
                row_y = y_cursor - row_h
                row_num = start_row_num + r_idx

                # Zebra striping
                if r_idx % 2 == 1:
                    ops.append("q")
                    ops.append("0.97 0.98 0.99 rg")
                    ops.append(f"{margin} {row_y} {table_w} {row_h} re f")
                    ops.append("Q")

                # Garis pemisah baris tipis
                ops.append("q")
                ops.append("0.9 0.92 0.94 RG 0.5 w")
                ops.append(f"{margin} {row_y} m {margin + table_w} {row_y} l S")
                ops.append("Q")

                # Format PIC pendek (username jika panjang)
                pic_display = item["user_pic"]
                if "(" in pic_display and ")" in pic_display:
                    pic_display = pic_display.split("(")[-1].replace(")", "").strip()
                elif len(pic_display) > 12:
                    pic_display = pic_display[:12]

                # Format Tanggal (ambil YYYY-MM-DD saja)
                tgl_display = (item["tanggal_mitigasi"] or "").split(" ")[0]

                # Format Produk & Catatan
                prod_display = item["product_name"][:24] + (".." if len(item["product_name"]) > 24 else "")
                notes_display = (item["notes"] or "-")[:22] + (".." if len(item["notes"] or "") > 22 else "")

                # Nilai teks per kolom
                c_vals = [
                    (cols[0][0], str(row_num), False, "0.4 0.45 0.5"),
                    (cols[1][0], item["sku_code"], True, "0.1 0.15 0.2"),
                    (cols[2][0], prod_display, False, "0.1 0.15 0.2"),
                    (cols[3][0], item["category"][:5], False, "0.4 0.45 0.5"),
                    (cols[4][0], item["action_type"][:16], False, "0.1 0.2 0.5"),
                    (cols[5][0], pic_display, False, "0.3 0.35 0.4"),
                    (cols[6][0], tgl_display, False, "0.3 0.35 0.4"),
                    (cols[7][0], f"{item['skor_sebelum'] * 100:.1f}%", False, "0.75 0.15 0.15"),
                    (cols[8][0], fmt_idr(item["nilai_risiko_sebelum"]), False, "0.1 0.15 0.2"),
                    (cols[9][0], f"-{item['efektivitas_persen']}%", False, "0.08 0.5 0.2"),
                    (cols[10][0], fmt_idr(item["nilai_risiko_sesudah"]), False, "0.2 0.25 0.3"),
                    (cols[11][0], fmt_idr(item["penghematan"]), True, "0.06 0.52 0.2"),
                    (cols[12][0], notes_display, False, "0.45 0.5 0.55"),
                ]

                ops.append("BT")
                for cx, txt, is_b, col_code in c_vals:
                    font_tag = "/F2" if is_b else "/F1"
                    ops.append(f"{font_tag} 6.5 Tf {col_code} rg")
                    ops.append(f"1 0 0 1 {cx} {row_y + 4.5} Tm ({esc(txt)}) Tj")
                ops.append("ET")

                y_cursor -= row_h

        # 5. Baris TOTAL KESELURUHAN (Hanya di Halaman Terakhir)
        if is_last and items:
            tot_h = 18.0
            tot_y = y_cursor - tot_h

            # Background baris total
            ops.append("q")
            ops.append("0.93 0.95 0.98 rg 0.7 0.75 0.82 RG 1.0 w")
            ops.append(f"{margin} {tot_y} {table_w} {tot_h} re B")
            ops.append("Q")

            ops.append("BT")
            ops.append("/F2 7 Tf 0.08 0.12 0.2 rg")
            ops.append(f"1 0 0 1 {cols[0][0]} {tot_y + 5.5} Tm (TOTAL KESELURUHAN) Tj")
            ops.append(f"1 0 0 1 {cols[8][0]} {tot_y + 5.5} Tm ({esc(fmt_idr(summary['total_nilai_sebelum']))}) Tj")
            ops.append(f"/F2 7 Tf 0.06 0.52 0.2 rg")
            ops.append(f"1 0 0 1 {cols[9][0]} {tot_y + 5.5} Tm ({esc(str(summary['rasio_efektivitas_persen']))}%) Tj")
            ops.append(f"/F2 7 Tf 0.15 0.2 0.3 rg")
            ops.append(f"1 0 0 1 {cols[10][0]} {tot_y + 5.5} Tm ({esc(fmt_idr(summary['total_nilai_sesudah']))}) Tj")
            ops.append(f"/F2 7 Tf 0.06 0.52 0.2 rg")
            ops.append(f"1 0 0 1 {cols[11][0]} {tot_y + 5.5} Tm ({esc(fmt_idr(summary['total_penghematan']))}) Tj")
            ops.append("ET")

        # 6. Footer Legal ALCOA & Nomor Halaman
        ops.append("q")
        ops.append("0.8 0.82 0.85 RG 0.5 w")
        ops.append(f"{margin} 28 m {margin + table_w} 28 l S")
        ops.append("Q")

        ops.append("BT")
        ops.append("/F1 7 Tf 0.45 0.5 0.55 rg")
        ops.append(f"1 0 0 1 {margin} 18 Tm (Dokumen ini dihasilkan secara elektronik oleh Sistem ONEKalbe EWS dan tidak dapat diedit setelah diexport - Standar ALCOA.) Tj")
        ops.append(f"1 0 0 1 {page_w - margin - 85} 18 Tm (Halaman {p_idx} dari {total_pages}) Tj")
        ops.append("ET")

        pages_streams.append("\n".join(ops))

    # Bangun Dokumen PDF Binary
    objects = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")

    kids_refs = []
    next_id = 5
    for _ in pages_streams:
        page_obj_id = next_id
        kids_refs.append(f"{page_obj_id} 0 R")
        next_id += 2

    objects.append(f"<< /Type /Pages /Kids [{' '.join(kids_refs)}] /Count {len(pages_streams)} >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    for p_i, stream_content in enumerate(pages_streams):
        p_obj_id = 5 + p_i * 2
        c_obj_id = p_obj_id + 1
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {c_obj_id} 0 R >>"
        )
        objects.append(page_dict)

        stream_bytes = stream_content.encode("latin1", "replace")
        content_dict = f"<< /Length {len(stream_bytes)} >>\nstream\n{stream_content}\nendstream"
        objects.append(content_dict)

    header = b"%PDF-1.4\n"
    body_parts = []
    offsets = []
    current_offset = len(header)

    for i, obj in enumerate(objects, start=1):
        offsets.append(current_offset)
        part = f"{i} 0 obj\n{obj}\nendobj\n".encode("latin1", "replace")
        body_parts.append(part)
        current_offset += len(part)

    xref_offset = current_offset
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"

    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"

    return header + b"".join(body_parts) + xref.encode("latin1") + trailer.encode("latin1")

