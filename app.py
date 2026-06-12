import io
import re
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime
import hashlib

import fitz  # PyMuPDF
import streamlit as st
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth

# =====================================================
# CONFIGURACIÓN GENERAL
# =====================================================
APP_TITLE = "Editor de Propuestas CLA - ESS"
YEAR = "2026"
CONTACT_EMAIL = "sembremosseguridad.dppp@msp.go.cr"
LOGO_PATH = Path("assets/logo_ess.png")

PAGE_W, PAGE_H = letter
MARGIN_X = 0.55 * inch
MARGIN_TOP = 0.55 * inch
MARGIN_BOTTOM = 0.55 * inch
CONTENT_W = PAGE_W - (2 * MARGIN_X)

BLUE = colors.HexColor("#0B2E4A")
LIGHT_BLUE = colors.HexColor("#E8EDF3")
MID_BLUE = colors.HexColor("#CBD5E1")
GRAY = colors.HexColor("#475569")
RED = colors.HexColor("#991B1B")
BLACK = colors.black
WHITE = colors.white

FF_MULTILINE = 4096
MAXLEN_BIG = 100000
def crear_control(data: Dict, generado_por: str, cargo: str, dependencia: str, output_type: str) -> Dict:
    ahora = datetime.now()

    base = (
        data.get("delegacion", "") +
        data.get("version", "") +
        generado_por +
        ahora.strftime("%Y%m%d%H%M%S")
    )

    codigo_hash = hashlib.md5(base.encode("utf-8")).hexdigest()[:10].upper()

    return {
        "codigo": f"CLA-{codigo_hash}",
        "fecha": ahora.strftime("%d/%m/%Y"),
        "hora": ahora.strftime("%H:%M:%S"),
        "usuario": generado_por,
        "cargo": cargo,
        "dependencia": dependencia,
        "tipo": "Versión editable para revisión" if output_type == "editable" else "Versión final"
    }

st.set_page_config(page_title=APP_TITLE, page_icon="📄", layout="wide")

# =====================================================
# UTILIDADES DE TEXTO Y PDF
# =====================================================
def clean_text(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def safe(value: str) -> str:
    return clean_text(value)


def read_pdf_text(uploaded_file) -> str:
    pdf_bytes = uploaded_file.read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    return "\n".join(parts)


def find_value(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}:\s*(.+)"
    m = re.search(pattern, text)
    return clean_text(m.group(1)) if m else ""


def extract_between(text: str, start: str, end: str) -> str:
    try:
        part = text.split(start, 1)[1]
        part = part.split(end, 1)[0]
        return clean_text(part)
    except Exception:
        return ""


def split_problem_blocks(text: str) -> List[Tuple[str, str]]:
    pattern = r"PROBLEMÁTICA\s+(\d+)\s*:\s*"
    matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
    blocks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[start:end]
        lines = [clean_text(x) for x in raw.splitlines() if clean_text(x)]
        problem_name = lines[0] if lines else f"Problemática {m.group(1)}"
        blocks.append((problem_name, raw))
    return blocks


def parse_indicators_from_action(action_part: str) -> List[Dict[str, str]]:
    """Parser básico adaptado a PDFs generados por Apps Script.
    Busca el bloque después de encabezados de indicador/meta/unidad y antes de otra acción o resultado.
    """
    indicators = []

    if "Indicador" not in action_part or "Meta" not in action_part or "Unidad" not in action_part:
        return indicators

    # Acotar zona de tabla
    table_zone = action_part
    if "Observaciones sobre la acción estratégica" in table_zone:
        table_zone = table_zone.split("Observaciones sobre la acción estratégica", 1)[1]
    if "Resultado de revisión" in table_zone:
        table_zone = table_zone.split("Resultado de revisión", 1)[0]
    if re.search(r"Acción estratégica\s+\d+", table_zone, flags=re.IGNORECASE):
        table_zone = re.split(r"Acción estratégica\s+\d+", table_zone, flags=re.IGNORECASE)[0]

    lines = [clean_text(x) for x in table_zone.splitlines() if clean_text(x)]
    skip_words = [
        "Indicador Meta Unidad", "Observaciones sobre", "indicador/meta", "Indicador", "Meta", "Unidad"
    ]
    useful = []
    for line in lines:
        if any(sw.lower() in line.lower() for sw in skip_words):
            continue
        if line.startswith("☐"):
            continue
        useful.append(line)

    # Heurística: intenta separar indicador meta unidad si meta es número o palabra corta al final.
    for line in useful:
        if not line:
            continue
        # Si termina como: texto 3 intervenciones / texto 12 operativos
        m = re.match(r"(.+?)\s+(\d+(?:[\.,]\d+)?)\s+(.+)$", line)
        if m:
            indicators.append({
                "indicador": clean_text(m.group(1)),
                "meta": clean_text(m.group(2)),
                "unidad": clean_text(m.group(3)),
            })
        else:
            indicators.append({"indicador": line, "meta": "", "unidad": ""})

    # Evitar duplicados simples
    seen = set()
    unique = []
    for ind in indicators:
        key = (ind["indicador"], ind["meta"], ind["unidad"])
        if key not in seen:
            unique.append(ind)
            seen.add(key)
    return unique


def parse_actions(block: str) -> List[Dict[str, str]]:
    actions = []
    matches = list(re.finditer(r"Acción estratégica\s+(\d+)", block, flags=re.IGNORECASE))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
        part = block[start:end]

        action_text = part.split("Responsable:", 1)[0] if "Responsable:" in part else part
        responsable = extract_between(part, "Responsable:", "Trimestres:")
        trimestres = extract_between(part, "Trimestres:", "Observaciones sobre la acción estratégica")

        actions.append({
            "accion": clean_text(action_text),
            "responsable": responsable,
            "trimestres": trimestres,
            "indicadores": parse_indicators_from_action(part),
        })
    return actions


def parse_pdf_content(text: str) -> Dict:
    data = {
        "delegacion": find_value(text, "Delegación Policial"),
        "region": find_value(text, "Dirección Regional"),
        "fecha": find_value(text, "Fecha de generación"),
        "version": find_value(text, "Versión"),
        "estado": find_value(text, "Estado"),
        "elaborado_por": find_value(text, "Elaborado por"),
        "cargo": find_value(text, "Cargo"),
        "problematicas": [],
    }

    for name, block in split_problem_blocks(text):
        id_linea = extract_between(block, "ID Línea", "Línea de acción propuesta")
        linea_accion = extract_between(block, "Línea de acción propuesta", "Observaciones sobre la línea")
        lider = extract_between(block, "Líder estratégico", "Observaciones sobre el líder estratégico")
        cogestores = extract_between(block, "Cogestores", "Acción estratégica")
        actions = parse_actions(block)
        data["problematicas"].append({
            "problematica": name,
            "id_linea": id_linea,
            "linea_accion": linea_accion,
            "lider": lider,
            "cogestores": cogestores,
            "acciones": actions,
        })
    return data


def wrap_lines(c: canvas.Canvas, text: str, width: float, font="Helvetica", size=9) -> List[str]:
    text = safe(text)
    if not text:
        return [""]
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        if stringWidth(test, font, size) <= width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, width: float,
                 font="Helvetica", size=9, leading=12) -> float:
    c.setFont(font, size)
    c.setFillColor(BLACK)
    for line in wrap_lines(c, text, width, font, size):
        c.drawString(x, y, line)
        y -= leading
    return y


def ensure_space(c: canvas.Canvas, y: float, needed: float, page_state: Dict):
    if y - needed < MARGIN_BOTTOM:
        draw_footer_small(c)
        c.showPage()
        page_state["page"] += 1
        draw_internal_header(c, page_state)
        return PAGE_H - 0.9 * inch
    return y


def draw_section_bar(c: canvas.Canvas, title: str, y: float) -> float:
    c.setFillColor(BLUE)
    c.rect(MARGIN_X, y - 18, CONTENT_W, 18, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN_X + 6, y - 13, title)
    return y - 26


def draw_label(c: canvas.Canvas, label: str, y: float) -> float:
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(BLUE)
    c.drawString(MARGIN_X, y, label)
    return y - 12


def text_field(c: canvas.Canvas, name: str, x: float, y: float, w: float, h: float, editable: bool):
    if editable:
        c.acroForm.textfield(
            name=name,
            tooltip=name,
            x=x,
            y=y,
            width=w,
            height=h,
            borderWidth=1,
            borderColor=MID_BLUE,
            fillColor=WHITE,
            textColor=BLACK,
            forceBorder=True,
            fontName="Helvetica",
            fontSize=9,
            fieldFlags=FF_MULTILINE,
            maxlen=MAXLEN_BIG,
        )
    else:
        c.setStrokeColor(MID_BLUE)
        c.rect(x, y, w, h, fill=0, stroke=1)


def checkbox(c: canvas.Canvas, name: str, x: float, y: float, editable: bool):
    if editable:
        c.acroForm.checkbox(
            name=name,
            tooltip=name,
            x=x,
            y=y,
            size=10,
            borderWidth=1,
            borderColor=BLUE,
            fillColor=WHITE,
            buttonStyle="check",
            forceBorder=True,
        )
    else:
        c.setStrokeColor(BLUE)
        c.rect(x, y, 10, 10, fill=0, stroke=1)

# =====================================================
# DIBUJO DEL PDF
# =====================================================
def draw_logo_center(c: canvas.Canvas, y: float, size: float = 1.7 * inch) -> float:
    if LOGO_PATH.exists():
        try:
            img = ImageReader(str(LOGO_PATH))
            x = (PAGE_W - size) / 2
            c.drawImage(img, x, y - size, width=size, height=size, preserveAspectRatio=True, mask="auto")
            return y - size - 16
        except Exception:
            return y
    return y


def draw_cover(c: canvas.Canvas, data: Dict, control: Dict):
    y = PAGE_H - 1.0 * inch
    y = draw_logo_center(c, y, size=1.85 * inch)

    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(PAGE_W / 2, y, "MINISTERIO DE SEGURIDAD PÚBLICA")
    y -= 18
    c.drawCentredString(PAGE_W / 2, y, "COORDINACIÓN NACIONAL")
    y -= 18
    c.drawCentredString(PAGE_W / 2, y, "ESTRATEGIA SEMBREMOS SEGURIDAD")
    y -= 34

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(PAGE_W / 2, y, "PROPUESTA DE LÍNEAS DE ACCIÓN")
    y -= 22
    c.drawCentredString(PAGE_W / 2, y, "PARA VALIDACIÓN Y MEJORA")
    y -= 28

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(PAGE_W / 2, y, YEAR)
    y -= 42

    rows = [
        ("Delegación Policial", data.get("delegacion", "")),
        ("Dirección Regional", data.get("region", "")),
        ("Fecha de generación", data.get("fecha", "")),
        ("Versión", data.get("version", "")),
        ("Estado", data.get("estado", "")),
        ("Elaborado por", data.get("elaborado_por", "")),
        ("Cargo", data.get("cargo", "")),
        ("Tipo documento", control.get("tipo", "")),
        ("Código control", control.get("codigo", "")),
        ("Fecha emisión", control.get("fecha", "")),
        ("Hora emisión", control.get("hora", "")),
        ("Generado por", control.get("usuario", "")),
         "Cargo generador", control.get("cargo", "")),
        ("Dependencia", control.get("dependencia", "")),
    ]

    box_x = 1.05 * inch
    box_w = PAGE_W - 2.1 * inch
    row_h = 20
    c.setStrokeColor(MID_BLUE)
    for label, value in rows:
        c.setFillColor(LIGHT_BLUE)
        c.rect(box_x, y - row_h + 4, 1.8 * inch, row_h, fill=1, stroke=1)
        c.setFillColor(WHITE)
        c.rect(box_x + 1.8 * inch, y - row_h + 4, box_w - 1.8 * inch, row_h, fill=1, stroke=1)
        c.setFillColor(BLUE)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(box_x + 6, y - 10, label)
        c.setFillColor(BLACK)
        c.setFont("Helvetica", 9)
        c.drawString(box_x + 1.8 * inch + 6, y - 10, safe(value))
        y -= row_h


def draw_internal_header(c: canvas.Canvas, page_state: Dict):
    c.setFillColor(LIGHT_BLUE)
    c.rect(MARGIN_X, PAGE_H - 0.6 * inch, CONTENT_W, 0.32 * inch, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN_X + 6, PAGE_H - 0.48 * inch, "Coordinación Nacional · Estrategia Sembremos Seguridad")
    c.setFont("Helvetica", 8)
    c.drawRightString(PAGE_W - MARGIN_X - 6, PAGE_H - 0.48 * inch, f"Página {page_state.get('page', '')}")


def draw_footer_small(c: canvas.Canvas):
    c.setStrokeColor(MID_BLUE)
    c.line(MARGIN_X, 0.42 * inch, PAGE_W - MARGIN_X, 0.42 * inch)
    c.setFont("Helvetica", 7)
    c.setFillColor(GRAY)
    c.drawString(MARGIN_X, 0.27 * inch, "Documento controlado. Verifique el código de control indicado en portada.")


def draw_instructions(c: canvas.Canvas, data: Dict, page_state: Dict) -> float:
    y = PAGE_H - 0.9 * inch
    draw_internal_header(c, page_state)
    y = PAGE_H - 1.05 * inch
    y = draw_section_bar(c, "INSTRUCCIONES PARA LA REVISIÓN", y)
    text = (
        "El presente documento contiene la propuesta preliminar de líneas de acción, acciones estratégicas e indicadores "
        "construidos a partir del análisis territorial. La persona revisora deberá verificar la coherencia entre la problemática "
        "priorizada, la línea de acción propuesta, la acción estratégica definida, los indicadores planteados, las metas, unidades "
        "y el líder estratégico asignado. Las observaciones registradas servirán como insumo para la elaboración de la versión "
        "corregida o versión final."
    )
    y = draw_wrapped(c, text, MARGIN_X, y, CONTENT_W, size=9, leading=12) - 12
    return y


def draw_problematic(c: canvas.Canvas, prob: Dict, number: int, y: float, page_state: Dict, editable: bool) -> float:
    y = ensure_space(c, y, 120, page_state)
    y = draw_section_bar(c, f"PROBLEMÁTICA {number}: {prob.get('problematica', '')}", y)

    y = draw_label(c, "ID Línea", y)
    y = draw_wrapped(c, prob.get("id_linea", ""), MARGIN_X, y, CONTENT_W, size=9, leading=11) - 5

    y = ensure_space(c, y, 95, page_state)
    y = draw_label(c, "Línea de acción propuesta", y)
    y = draw_wrapped(c, prob.get("linea_accion", ""), MARGIN_X, y, CONTENT_W, size=9, leading=11) - 5
    y = draw_label(c, "Observaciones sobre la línea", y)
    text_field(c, f"obs_linea_{number}", MARGIN_X, y - 48, CONTENT_W, 45, editable)
    y -= 58

    y = ensure_space(c, y, 75, page_state)
    y = draw_label(c, "Líder estratégico", y)
    y = draw_wrapped(c, prob.get("lider", ""), MARGIN_X, y, CONTENT_W, size=9, leading=11) - 5
    y = draw_label(c, "Observaciones sobre el líder estratégico", y)
    text_field(c, f"obs_lider_{number}", MARGIN_X, y - 36, CONTENT_W, 34, editable)
    y -= 46

    if prob.get("cogestores"):
        y = ensure_space(c, y, 55, page_state)
        y = draw_label(c, "Cogestores", y)
        y = draw_wrapped(c, prob.get("cogestores", ""), MARGIN_X, y, CONTENT_W, size=8.5, leading=10) - 5

    actions = prob.get("acciones", [])
    if not actions:
        y = draw_wrapped(c, "No se registran acciones estratégicas.", MARGIN_X, y, CONTENT_W, size=9, leading=11) - 8
    else:
        for a_idx, action in enumerate(actions, start=1):
            y = draw_action(c, action, number, a_idx, y, page_state, editable)

    y = ensure_space(c, y, 130, page_state)
    y = draw_label(c, "Resultado de revisión de la problemática", y)
    options = [
        "Sin observaciones",
        "Con observaciones de mejora",
        "Requiere reformulación parcial",
        "Requiere reformulación total",
    ]
    for opt_idx, option in enumerate(options, start=1):
        checkbox(c, f"rev_{number}_{opt_idx}", MARGIN_X, y - 2, editable)
        c.setFont("Helvetica", 9)
        c.setFillColor(BLACK)
        c.drawString(MARGIN_X + 16, y, option)
        y -= 15

    y -= 5
    y = draw_label(c, "Observaciones generales de la problemática", y)
    text_field(c, f"obs_general_{number}", MARGIN_X, y - 58, CONTENT_W, 55, editable)
    y -= 72
    return y


def draw_action(c: canvas.Canvas, action: Dict, p_num: int, a_idx: int, y: float, page_state: Dict, editable: bool) -> float:
    y = ensure_space(c, y, 105, page_state)
    c.setFillColor(LIGHT_BLUE)
    c.rect(MARGIN_X, y - 18, CONTENT_W, 18, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN_X + 6, y - 13, f"Acción estratégica {a_idx}")
    y -= 28

    y = draw_wrapped(c, action.get("accion", ""), MARGIN_X, y, CONTENT_W, size=9, leading=11) - 2
    y = draw_wrapped(c, f"Responsable: {action.get('responsable', '')}", MARGIN_X, y, CONTENT_W, font="Helvetica-Bold", size=8.5, leading=10) - 1
    y = draw_wrapped(c, f"Trimestres: {action.get('trimestres', '')}", MARGIN_X, y, CONTENT_W, font="Helvetica-Bold", size=8.5, leading=10) - 5

    y = draw_label(c, "Observaciones sobre la acción estratégica", y)
    text_field(c, f"obs_accion_{p_num}_{a_idx}", MARGIN_X, y - 38, CONTENT_W, 35, editable)
    y -= 50

    indicators = action.get("indicadores", [])
    for i_idx, ind in enumerate(indicators, start=1):
        y = draw_indicator(c, ind, p_num, a_idx, i_idx, y, page_state, editable)
    return y


def draw_indicator(c: canvas.Canvas, ind: Dict, p_num: int, a_idx: int, i_idx: int, y: float, page_state: Dict, editable: bool) -> float:
    y = ensure_space(c, y, 95, page_state)
    c.setStrokeColor(MID_BLUE)
    c.setFillColor(WHITE)
    c.rect(MARGIN_X, y - 78, CONTENT_W, 78, fill=0, stroke=1)

    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(MARGIN_X + 6, y - 13, f"Indicador {i_idx}")
    y_text = y - 27
    y_text = draw_wrapped(c, ind.get("indicador", ""), MARGIN_X + 6, y_text, CONTENT_W - 12, size=8.5, leading=10)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(BLACK)
    c.drawString(MARGIN_X + 6, y_text - 2, f"Meta: {ind.get('meta', '')}")
    c.drawString(MARGIN_X + 1.55 * inch, y_text - 2, f"Unidad: {ind.get('unidad', '')}")

    obs_x = MARGIN_X + 3.1 * inch
    obs_y = y - 72
    obs_w = CONTENT_W - 3.2 * inch
    text_field(c, f"obs_ind_{p_num}_{a_idx}_{i_idx}", obs_x, obs_y, obs_w, 35, editable)
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(BLUE)
    c.drawString(obs_x, y - 31, "Observación indicador/meta")

    return y - 88


def draw_final_page(c: canvas.Canvas, control: Dict):
    c.showPage()
    y = PAGE_H - 1.25 * inch
    y = draw_logo_center(c, y, size=1.65 * inch)
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(PAGE_W / 2, y, "COORDINACIÓN NACIONAL")
    y -= 18
    c.drawCentredString(PAGE_W / 2, y, "ESTRATEGIA SEMBREMOS SEGURIDAD")
    y -= 34
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(PAGE_W / 2, y, "Correo:")
    y -= 16
    c.setFont("Helvetica", 11)
    c.drawCentredString(PAGE_W / 2, y, CONTACT_EMAIL)
        y -= 34
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(PAGE_W / 2, y, "DOCUMENTO CONTROLADO")
    y -= 16

    c.setFont("Helvetica", 10)
    c.drawCentredString(PAGE_W / 2, y, f"Código de control: {control.get('codigo', '')}")
    y -= 14
    c.drawCentredString(PAGE_W / 2, y, f"Tipo: {control.get('tipo', '')}")
    y -= 14
    c.drawCentredString(PAGE_W / 2, y, f"Generado por: {control.get('usuario', '')}")
    y -= 14
    c.drawCentredString(PAGE_W / 2, y, f"Fecha y hora: {control.get('fecha', '')} {control.get('hora', '')}")
    y -= 30
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(PAGE_W / 2, y, "MINISTERIO DE SEGURIDAD PÚBLICA")
    y -= 24
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(PAGE_W / 2, y, YEAR)


def make_pdf(data: Dict, output_type: str, control: Dict) -> bytes:
    editable = output_type == "editable"
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

   draw_cover(c, data, control)
    c.showPage()
    page_state = {"page": 1}
    y = draw_instructions(c, data, page_state)

    problems = data.get("problematicas", [])
    if not problems:
        y = draw_wrapped(c, "No se detectaron problemáticas en el PDF.", MARGIN_X, y, CONTENT_W)
    else:
        for idx, prob in enumerate(problems, start=1):
            y = draw_problematic(c, prob, idx, y, page_state, editable)
            y = ensure_space(c, y, 35, page_state)

    draw_footer_small(c)
    draw_final_page(c, control)
    c.save()
    buffer.seek(0)
    return buffer.read()

# =====================================================
# STREAMLIT UI
# =====================================================
def make_output_filename(original_name: str, output_type: str, codigo_control: str) -> str:
    base = Path(original_name).stem
    tipo = "EDITABLE" if output_type == "editable" else "FINAL"
    return f"{base}_{tipo}_{codigo_control}.pdf"


def preview_data(data: Dict):
    st.subheader("Vista previa del contenido extraído")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.write("**Delegación:**", data.get("delegacion", ""))
        st.write("**Región:**", data.get("region", ""))
    with col2:
        st.write("**Versión:**", data.get("version", ""))
        st.write("**Estado:**", data.get("estado", ""))
    with col3:
        st.write("**Elaborado por:**", data.get("elaborado_por", ""))
        st.write("**Cargo:**", data.get("cargo", ""))

    st.markdown("---")
    problems = data.get("problematicas", [])
    if not problems:
        st.warning("No se detectaron problemáticas en el PDF.")
        return

    for idx, prob in enumerate(problems, start=1):
        with st.expander(f"Problemática {idx}: {prob.get('problematica', '')}"):
            st.write("**ID Línea:**", prob.get("id_linea", ""))
            st.write("**Línea de acción:**", prob.get("linea_accion", ""))
            st.write("**Líder estratégico:**", prob.get("lider", ""))
            st.write("**Cogestores:**", prob.get("cogestores", ""))
            for a_idx, action in enumerate(prob.get("acciones", []), start=1):
                st.write(f"**Acción {a_idx}:** {action.get('accion', '')}")
                st.write(f"Responsable: {action.get('responsable', '')}")
                st.write(f"Trimestres: {action.get('trimestres', '')}")
                for i_idx, ind in enumerate(action.get("indicadores", []), start=1):
                    st.write(f"Indicador {i_idx}: {ind.get('indicador', '')} | Meta: {ind.get('meta', '')} | Unidad: {ind.get('unidad', '')}")


def main():
    st.title(APP_TITLE)
    st.caption("Coordinación Nacional · Estrategia Sembremos Seguridad · 2026")

    st.markdown(
        """
        Suba el PDF de propuesta generado desde el constructor. La aplicación reconstruye el documento
        y genera una versión optimizada, con campos editables reales cuando se selecciona la versión editable.
        """
    )

    uploaded_pdf = st.file_uploader("Subir PDF de propuesta", type=["pdf"])
st.subheader("Datos de control documental")

generado_por = st.text_input("Nombre de quien genera el documento")
cargo_generador = st.text_input("Cargo de quien genera el documento")
dependencia_generador = st.text_input(
    "Dependencia / Unidad",
    value="Coordinación Nacional - Estrategia Sembremos Seguridad"
)

    output_type = st.radio(
        "Tipo de salida",
        options=["editable", "final"],
        format_func=lambda x: "Versión editable para revisión" if x == "editable" else "Versión final sin campos editables",
    )

    if uploaded_pdf is None:
        st.info("Suba un PDF de propuesta para iniciar.")
        return

    try:
        text = read_pdf_text(uploaded_pdf)
        data = parse_pdf_content(text)
        preview_data(data)

        st.markdown("---")
if st.button("Generar PDF", type="primary"):

    if not generado_por.strip() or not cargo_generador.strip():
        st.error("Debe completar el nombre y cargo de quien genera el documento.")
        st.stop()

    control = crear_control(
        data,
        generado_por.strip(),
        cargo_generador.strip(),
        dependencia_generador.strip(),
        output_type
    )

    pdf_bytes = make_pdf(data, output_type, control)
    output_name = make_output_filename(uploaded_pdf.name, output_type, control["codigo"])

    except Exception as exc:
        st.error(f"No se pudo procesar el PDF: {exc}")


if __name__ == "__main__":
    main()
