import io
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import fitz  # PyMuPDF
import streamlit as st
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage
from reportlab.pdfbase.pdfmetrics import stringWidth

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None


# =====================================================
# CONFIGURACIÓN GENERAL
# =====================================================
APP_TITLE = "Editor de Propuestas CLA - ESS"

SHEET_CONTROL_VERSIONES = "CONTROL_VERSIONES_STL_APP"
SHEET_USUARIOS = "USUARIOS_CLA"
CONTROL_HEADERS = [
    "ID_REGISTRO",
    "FECHA_VERSION",
    "DELEGACION",
    "VERSION",
    "TIPO_DOCUMENTO",
    "ESTADO",
    "USUARIO",
    "CARGO",
]
YEAR = "2026"
CONTACT_EMAIL = "sembremosseguridad.dppp@msp.go.cr"
LOGO_PATH = Path("assetslogo_ess.png")
LOGO_BLUE_PATH = Path("logo_ess_azul.png")

PAGE_W, PAGE_H = letter
MARGIN_X = 0.55 * inch
MARGIN_TOP = 0.55 * inch
MARGIN_BOTTOM = 0.55 * inch
CONTENT_W = PAGE_W - (2 * MARGIN_X)

BLUE = colors.HexColor("#0B2E4A")
LIGHT_BLUE = colors.HexColor("#E8EDF3")
MID_BLUE = colors.HexColor("#CBD5E1")
GRAY = colors.HexColor("#475569")
BLACK = colors.black
WHITE = colors.white

FF_MULTILINE = 4096
MAXLEN_BIG = 100000

st.set_page_config(page_title=APP_TITLE, page_icon="📄", layout="wide")


# =====================================================
# CONTROL DOCUMENTAL
# =====================================================
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
        "tipo": "Versión editable para revisión" if output_type == "editable" else "Versión final",
    }


# =====================================================
# GOOGLE SHEETS - USUARIOS Y CONTROL DE VERSIONES
# =====================================================
def _get_secret_value(*names: str) -> str:
    for name in names:
        try:
            value = st.secrets.get(name, "")
            if value:
                return str(value)
        except Exception:
            pass
    return ""


def get_spreadsheet_id() -> str:
    return _get_secret_value(
        "GOOGLE_SHEET_ID",
        "SPREADSHEET_ID",
        "SHEET_ID",
        "spreadsheet_id",
    )


@st.cache_resource(show_spinner=False)
def get_gspread_client():
    def get_workbook():
    sheet_id = get_spreadsheet_id()
    if not sheet_id:
        raise RuntimeError(
            "No se encontró el ID del Google Sheet. Configure GOOGLE_SHEET_ID en secrets."
        )

    try:
        return get_gspread_client().open_by_key(sheet_id)
    except Exception as e:
        raise RuntimeError(
            f"Error abriendo el libro Google Sheet: {type(e).__name__} - {repr(e)}"
        )


def get_worksheet(nombre_hoja: str):
    wb = get_workbook()
    try:
        return wb.worksheet(nombre_hoja)
    except Exception as exc:
        raise RuntimeError(
            f"No existe o no se pudo acceder a la hoja requerida: {nombre_hoja}. "
            f"Error real: {type(exc).__name__} - {repr(exc)}"
        )
    try:
        creds_info = dict(st.secrets["gcp_service_account"])

        credentials = Credentials.from_service_account_info(
            creds_info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )

        client = gspread.authorize(credentials)
        return client

    except Exception as e:
        st.error(f"ERROR GOOGLE REAL: {type(e).__name__} - {repr(e)}")
        st.stop()
    sheet_id = get_spreadsheet_id()
    if not sheet_id:
        raise RuntimeError(
            "No se encontró el ID del Google Sheet. Configure GOOGLE_SHEET_ID en secrets."
        )
    return get_gspread_client().open_by_key(sheet_id)


def get_worksheet(nombre_hoja: str):
    wb = get_workbook()
    try:
        return wb.worksheet(nombre_hoja)
    except Exception as exc:
        raise RuntimeError(
            f"No existe o no se pudo acceder a la hoja requerida: {nombre_hoja}. "
            f"Error real: {type(exc).__name__} - {repr(exc)}"
        ) from exc


def normalizar(valor: str) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def normalizar_si_no(valor: str) -> str:
    return normalizar(valor).upper()


@st.cache_data(ttl=60, show_spinner=False)
def obtener_usuarios_activos() -> List[Dict[str, str]]:
    ws = get_worksheet(SHEET_USUARIOS)
    registros = ws.get_all_records()

    usuarios = []
    for row in registros:
        nombre = normalizar(row.get("NOMBRE", ""))
        cargo = normalizar(row.get("CARGO", ""))
        activo = normalizar_si_no(row.get("ACTIVO", ""))

        if nombre and activo == "SI":
            usuarios.append({
                "NOMBRE": nombre,
                "CARGO": cargo,
            })

    usuarios.sort(key=lambda x: x["NOMBRE"])
    return usuarios


def asegurar_hoja_control():
    ws = get_worksheet(SHEET_CONTROL_VERSIONES)
    valores = ws.get_all_values()

    if not valores:
        ws.append_row(CONTROL_HEADERS, value_input_option="USER_ENTERED")
        return ws

    encabezados = [normalizar(x) for x in valores[0]]
    if encabezados[:len(CONTROL_HEADERS)] != CONTROL_HEADERS:
        raise RuntimeError(
            f"La hoja {SHEET_CONTROL_VERSIONES} no tiene los encabezados esperados. "
            f"Deben ser: {' | '.join(CONTROL_HEADERS)}"
        )

    return ws


def leer_control_versiones() -> List[Dict[str, str]]:
    ws = asegurar_hoja_control()
    return ws.get_all_records()


def crear_id_registro(registros: List[Dict[str, str]]) -> str:
    hoy = datetime.now().strftime("%Y%m%d")
    max_consecutivo = 0

    for row in registros:
        valor = normalizar(row.get("ID_REGISTRO", ""))
        m = re.match(rf"^{hoy}-(\d{{3}})$", valor)
        if m:
            max_consecutivo = max(max_consecutivo, int(m.group(1)))

    return f"{hoy}-{max_consecutivo + 1:03d}"


def obtener_siguiente_version(delegacion: str, registros: List[Dict[str, str]]) -> str:
    delegacion_ref = normalizar(delegacion).lower()
    max_version = 0

    for row in registros:
        if normalizar(row.get("DELEGACION", "")).lower() != delegacion_ref:
            continue

        version = normalizar(row.get("VERSION", ""))
        m = re.match(r"^V(\d+)$", version, flags=re.IGNORECASE)
        if m:
            max_version = max(max_version, int(m.group(1)))

    return f"V{max_version + 1}"


def existe_version(delegacion: str, version: str, registros: List[Dict[str, str]]) -> bool:
    delegacion_ref = normalizar(delegacion).lower()
    version_ref = normalizar(version).upper()

    for row in registros:
        if (
            normalizar(row.get("DELEGACION", "")).lower() == delegacion_ref
            and normalizar(row.get("VERSION", "")).upper() == version_ref
        ):
            return True
    return False


def registrar_version_pdf(
    delegacion: str,
    version: str,
    tipo_documento: str,
    estado: str,
    usuario: str,
    cargo: str,
) -> Tuple[bool, str]:
    ws = asegurar_hoja_control()
    registros = ws.get_all_records()

    if existe_version(delegacion, version, registros):
        return False, "La versión ya existía en el control. No se creó una fila duplicada."

    id_registro = crear_id_registro(registros)
    fecha_version = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ws.append_row(
        [
            id_registro,
            fecha_version,
            delegacion,
            version,
            tipo_documento,
            estado,
            usuario,
            cargo,
        ],
        value_input_option="USER_ENTERED",
    )

    # Limpia caché para que el siguiente cálculo de versión lea el dato nuevo.
    st.cache_data.clear()

    return True, id_registro


def preparar_nueva_version(delegacion: str) -> Tuple[str, str]:
    registros = leer_control_versiones()
    version = obtener_siguiente_version(delegacion, registros)
    id_preview = crear_id_registro(registros)
    return version, id_preview


def limpiar_nombre_archivo(valor: str) -> str:
    valor = normalizar(valor)
    valor = re.sub(r"[^\w\-áéíóúÁÉÍÓÚñÑ]+", "_", valor, flags=re.UNICODE)
    valor = re.sub(r"_+", "_", valor).strip("_")
    return valor or "Documento"



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
    pdf_bytes = uploaded_file.getvalue()
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
    indicators = []

    if "Indicador" not in action_part or "Meta" not in action_part or "Unidad" not in action_part:
        return indicators

    table_zone = action_part
    if "Observaciones sobre la acción estratégica" in table_zone:
        table_zone = table_zone.split("Observaciones sobre la acción estratégica", 1)[1]
    if "Resultado de revisión" in table_zone:
        table_zone = table_zone.split("Resultado de revisión", 1)[0]
    if re.search(r"Acción estratégica\s+\d+", table_zone, flags=re.IGNORECASE):
        table_zone = re.split(r"Acción estratégica\s+\d+", table_zone, flags=re.IGNORECASE)[0]

    lines = [clean_text(x) for x in table_zone.splitlines() if clean_text(x)]
    skip_words = [
        "Indicador Meta Unidad",
        "Observaciones sobre",
        "indicador/meta",
        "Indicador",
        "Meta",
        "Unidad",
    ]

    useful = []
    for line in lines:
        if any(sw.lower() in line.lower() for sw in skip_words):
            continue
        if line.startswith("☐"):
            continue
        useful.append(line)

    for line in useful:
        if not line:
            continue

        m = re.match(r"(.+?)\s+(\d+(?:[\.,]\d+)?)\s+(.+)$", line)
        if m:
            indicators.append({
                "indicador": clean_text(m.group(1)),
                "meta": clean_text(m.group(2)),
                "unidad": clean_text(m.group(3)),
            })
        else:
            indicators.append({"indicador": line, "meta": "", "unidad": ""})

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


def draw_wrapped(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    width: float,
    font="Helvetica",
    size=9,
    leading=12,
) -> float:
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
def get_logo_path_for_pdf():
    if not LOGO_PATH.exists():
        return None

    try:
        img = PILImage.open(LOGO_PATH).convert("RGBA")
        pixels = img.load()

        azul = (11, 46, 74)

        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = pixels[x, y]
                if a > 0 and (r + g + b) < 740:
                    pixels[x, y] = (azul[0], azul[1], azul[2], a)

        img.save(LOGO_BLUE_PATH)
        return LOGO_BLUE_PATH

    except Exception:
        return LOGO_PATH
def draw_logo_center(c: canvas.Canvas, y: float, size: float = 1.7 * inch) -> float:
    logo_path = get_logo_path_for_pdf()

    if logo_path:
        try:
            img = ImageReader(str(logo_path))
            x = (PAGE_W - size) / 2
            c.drawImage(
                img,
                x,
                y - size,
                width=size,
                height=size,
                preserveAspectRatio=True,
                mask="auto"
            )
            return y - size - 16
        except Exception:
            return y

    return y


def draw_cover(c: canvas.Canvas, data: Dict, control: Dict):
    y = PAGE_H - 0.75 * inch
    y = draw_logo_center(c, y, size=1.55 * inch)

    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(PAGE_W / 2, y, "MINISTERIO DE SEGURIDAD PÚBLICA")
    y -= 16
    c.drawCentredString(PAGE_W / 2, y, "COORDINACIÓN NACIONAL")
    y -= 16
    c.drawCentredString(PAGE_W / 2, y, "ESTRATEGIA SEMBREMOS SEGURIDAD")
    y -= 28

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(PAGE_W / 2, y, "PROPUESTA DE LÍNEAS DE ACCIÓN")
    y -= 20
    c.drawCentredString(PAGE_W / 2, y, "PARA VALIDACIÓN Y MEJORA")
    y -= 24

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(PAGE_W / 2, y, YEAR)
    y -= 34

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
        ("Cargo generador", control.get("cargo", "")),
        ("Dependencia", control.get("dependencia", "")),
    ]

    box_x = 0.95 * inch
    box_w = PAGE_W - 1.9 * inch
    row_h = 18
    c.setStrokeColor(MID_BLUE)

    for label, value in rows:
        c.setFillColor(LIGHT_BLUE)
        c.rect(box_x, y - row_h + 4, 1.75 * inch, row_h, fill=1, stroke=1)
        c.setFillColor(WHITE)
        c.rect(box_x + 1.75 * inch, y - row_h + 4, box_w - 1.75 * inch, row_h, fill=1, stroke=1)
        c.setFillColor(BLUE)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(box_x + 6, y - 9, label)
        c.setFillColor(BLACK)
        c.setFont("Helvetica", 8)
        c.drawString(box_x + 1.75 * inch + 6, y - 9, safe(value))
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
    draw_internal_header(c, page_state)
    y = PAGE_H - 1.05 * inch
    y = draw_section_bar(c, "INSTRUCCIONES PARA LA VALIDACIÓN", y)
    text = (
        "Revise integralmente la propuesta presentada, verificando la coherencia entre la problemática identificada, "
        "el análisis estructural, las líneas de acción, las acciones estratégicas, los indicadores, las metas, los líderes "
        "estratégicos y los cogestores propuestos. Consigne observaciones, recomendaciones o ajustes sugeridos únicamente "
        "cuando contribuyan al fortalecimiento de la propuesta evaluada. Procure que las observaciones sean claras, "
        "específicas y orientadas a la mejora. No olvide guardar el documento una vez finalizada la revisión."
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

    y = ensure_space(c, y, 180, page_state)
    y = draw_label(c, "Resultado de la validación de la propuesta", y)
    y = draw_wrapped(
        c,
        "Seleccione la opción que mejor refleje el resultado de la revisión técnica realizada.",
        MARGIN_X,
        y,
        CONTENT_W,
        size=8,
        leading=10,
    ) - 2

    options = [
        "Validada",
        "Validada con observaciones o recomendaciones",
        "Validada con ajustes parciales sugeridos",
        "Validada con ajustes integrales sugeridos",
    ]

    for opt_idx, option in enumerate(options, start=1):
        checkbox(c, f"rev_{number}_{opt_idx}", MARGIN_X, y - 2, editable)
        c.setFont("Helvetica", 9)
        c.setFillColor(BLACK)
        c.drawString(MARGIN_X + 16, y, option)
        y -= 15

    y -= 5
    y = draw_label(c, "Observaciones, recomendaciones o ajustes sugeridos", y)
    text_field(c, f"obs_general_{number}", MARGIN_X, y - 64, CONTENT_W, 60, editable)
    y -= 76

    y = draw_wrapped(
        c,
        "Los ajustes sugeridos podrán referirse a la problemática identificada, el análisis estructural, "
        "las líneas de acción, las acciones estratégicas, los indicadores, los líderes estratégicos o los cogestores propuestos.",
        MARGIN_X,
        y,
        CONTENT_W,
        size=7.5,
        leading=9,
    ) - 8

    y = ensure_space(c, y, 85, page_state)
    y = draw_label(c, "Datos de la validación", y)

    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(BLUE)
    c.drawString(MARGIN_X, y, "Nombre completo de quien emite la validación:")
    text_field(c, f"nombre_validador_{number}", MARGIN_X + 2.65 * inch, y - 4, CONTENT_W - 2.65 * inch, 14, editable)
    y -= 22

    c.drawString(MARGIN_X, y, "Fecha de emisión de observaciones o validación:")
    text_field(c, f"fecha_validacion_{number}", MARGIN_X + 2.85 * inch, y - 4, 1.6 * inch, 14, editable)
    y -= 28

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


def draw_final_page(c: canvas.Canvas, control: Dict, editable: bool):
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
    draw_final_page(c, control, editable)
    c.save()
    buffer.seek(0)
    return buffer.read()


# =====================================================
# STREAMLIT UI
# =====================================================
def make_output_filename(original_name: str, output_type: str, codigo_control: str, delegacion: str, version: str) -> str:
    base = limpiar_nombre_archivo(Path(original_name).stem)
    deleg = limpiar_nombre_archivo(delegacion)
    tipo = "EDITABLE" if output_type == "editable" else "NO_EDITABLE_FINAL"
    fecha = datetime.now().strftime("%Y-%m-%d")
    return f"{deleg}_{version}_{tipo}_{fecha}_{codigo_control}.pdf"


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
                    st.write(
                        f"Indicador {i_idx}: {ind.get('indicador', '')} | "
                        f"Meta: {ind.get('meta', '')} | Unidad: {ind.get('unidad', '')}"
                    )



def render_brand_footer():
    st.markdown("---")
    st.caption("SIGESS 2026 · Editor de Propuestas CLA - ESS · Versión 1.0")
    st.caption("Desarrollo técnico: Manfred Rivera Meneses")


def main():
    st.title(APP_TITLE)
    st.caption("Coordinación Nacional · Estrategia Sembremos Seguridad · 2026")

    st.markdown(
        """
        Suba el PDF de propuesta generado desde el constructor. La aplicación reconstruye el documento
        y genera una versión optimizada, con campos editables reales cuando se selecciona la versión para validación.
        """
    )

    # -------------------------------------------------
    # Usuario desde catálogo USUARIOS_CLA
    # -------------------------------------------------
    st.subheader("Usuario generador")

    try:
        usuarios = obtener_usuarios_activos()
    except Exception as exc:
        st.error(
            f"No se pudo cargar la hoja {SHEET_USUARIOS}: "
            f"{type(exc).__name__} - {repr(exc)}"
        )
        st.stop()

    if not usuarios:
        st.error(f"No hay usuarios activos en la hoja {SHEET_USUARIOS}.")
        st.stop()

    opciones_usuario = [f"{u['NOMBRE']} | {u['CARGO']}" for u in usuarios]
    seleccion_usuario = st.selectbox(
        "Seleccione quién genera esta versión del documento",
        options=opciones_usuario,
        index=None,
        placeholder="Seleccione un usuario activo",
    )

    usuario_data = None
    if seleccion_usuario:
        idx_usuario = opciones_usuario.index(seleccion_usuario)
        usuario_data = usuarios[idx_usuario]
        st.info(f"Usuario seleccionado: {usuario_data['NOMBRE']} · Cargo: {usuario_data['CARGO']}")

    dependencia_generador = st.text_input(
        "Dependencia / Unidad",
        value="Coordinación Nacional - Estrategia Sembremos Seguridad",
    )

    uploaded_pdf = st.file_uploader("Subir PDF de propuesta", type=["pdf"])

    output_type = st.radio(
        "Tipo de salida",
        options=["editable", "final"],
        format_func=lambda x: "PDF para validación (editable)" if x == "editable" else "PDF final no editable",
    )

    if uploaded_pdf is None:
        st.info("Suba un PDF de propuesta para iniciar.")
        render_brand_footer()
        return

    try:
        text = read_pdf_text(uploaded_pdf)
        data = parse_pdf_content(text)

        if not data.get("delegacion"):
            st.warning("No se detectó la delegación en el PDF. Revise que el documento incluya el campo 'Delegación Policial'.")

        preview_data(data)

        st.markdown("---")
        st.subheader("Control de versión")

        tipo_documento = "Editable" if output_type == "editable" else "No editable"
        estado_version = "En proceso" if output_type == "editable" else "Final"

        if data.get("delegacion"):
            try:
                siguiente_version, id_preview = preparar_nueva_version(data.get("delegacion", ""))
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.metric("Siguiente versión", siguiente_version)
                with col_b:
                    st.metric("Tipo documento", tipo_documento)
                with col_c:
                    st.metric("Estado", estado_version)
            except Exception as exc:
                st.error(f"No se pudo preparar el control de versiones: {exc}")
                st.stop()
        else:
            siguiente_version = ""
            st.error("No se puede calcular versión sin delegación.")
            st.stop()

        st.markdown("---")

        if "ultimo_pdf_generado" in st.session_state:
            info = st.session_state["ultimo_pdf_generado"]
            st.success(f"Último PDF generado en esta sesión: {info['nombre_archivo']}")
            st.download_button(
                "Descargar último PDF generado",
                data=info["pdf_bytes"],
                file_name=info["nombre_archivo"],
                mime="application/pdf",
                key="download_ultimo_pdf",
            )

        if st.button("Generar nueva versión PDF", type="primary"):
            if not usuario_data:
                st.error("Debe seleccionar un usuario activo.")
                st.stop()

            pdf_hash = hashlib.sha256(uploaded_pdf.getvalue()).hexdigest()
            llave_actual = f"{pdf_hash}|{data.get('delegacion','')}|{output_type}|{usuario_data['NOMBRE']}"

            if st.session_state.get("ultima_llave_generacion") == llave_actual:
                st.warning(
                    "Esta misma versión ya fue generada en esta sesión. "
                    "Use el botón de descarga mostrado arriba para evitar registros duplicados."
                )
                st.stop()

            # Recalcular justo antes de guardar, para evitar versiones desactualizadas.
            registros = leer_control_versiones()
            version_final = obtener_siguiente_version(data.get("delegacion", ""), registros)

            data["version"] = version_final
            data["estado"] = estado_version

            control = crear_control(
                data,
                usuario_data["NOMBRE"],
                usuario_data["CARGO"],
                dependencia_generador.strip(),
                output_type,
            )

            registrado, id_registro = registrar_version_pdf(
                delegacion=data.get("delegacion", ""),
                version=version_final,
                tipo_documento=tipo_documento,
                estado=estado_version,
                usuario=usuario_data["NOMBRE"],
                cargo=usuario_data["CARGO"],
            )

            if not registrado:
                st.warning(id_registro)
                st.stop()

            pdf_bytes = make_pdf(data, output_type, control)
            output_name = make_output_filename(
                uploaded_pdf.name,
                output_type,
                control["codigo"],
                data.get("delegacion", ""),
                version_final,
            )

            st.session_state["ultima_llave_generacion"] = llave_actual
            st.session_state["ultimo_pdf_generado"] = {
                "pdf_bytes": pdf_bytes,
                "nombre_archivo": output_name,
            }

            st.success(f"PDF generado correctamente. Registro de versión: {id_registro}")
            st.download_button(
                "Descargar PDF generado",
                data=pdf_bytes,
                file_name=output_name,
                mime="application/pdf",
                key=f"download_{id_registro}",
            )

    except Exception as exc:
        st.error(f"No se pudo procesar el PDF: {exc}")

    render_brand_footer()


if __name__ == "__main__":
    main()
