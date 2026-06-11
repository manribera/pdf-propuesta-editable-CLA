import io
import re
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.acroform import AcroForm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image,
    Flowable,
)
from reportlab.pdfgen import canvas


APP_TITLE = "Editor de Propuestas CLA - ESS"
YEAR = "2026"
CONTACT_EMAIL = "sembremosseguridad.dppp@msp.go.cr"
LOGO_PATH = Path("assets/logo_ess.png")


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📄",
    layout="wide"
)


def read_pdf_text(uploaded_file) -> str:
    pdf_bytes = uploaded_file.read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    text_parts = []
    for page in doc:
        text_parts.append(page.get_text("text"))

    return "\n".join(text_parts)


def clean_text(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def find_value(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}:\s*(.+)"
    match = re.search(pattern, text)
    if match:
        return clean_text(match.group(1))
    return ""


def parse_pdf_content(text: str) -> dict:
    data = {
        "delegacion": find_value(text, "Delegación Policial"),
        "region": find_value(text, "Dirección Regional"),
        "fecha": find_value(text, "Fecha de generación"),
        "version": find_value(text, "Versión"),
        "estado": find_value(text, "Estado"),
        "elaborado_por": find_value(text, "Elaborado por"),
        "cargo": find_value(text, "Cargo"),
        "problematicas": []
    }

    bloques = re.split(r"PROBLEMÁTICA\s+\d+:", text)

    for bloque in bloques[1:]:
        problematica_linea = clean_text(bloque.split("\n")[0])

        id_linea = extract_between(bloque, "ID Línea", "Línea de acción propuesta")
        linea_accion = extract_between(bloque, "Línea de acción propuesta", "Observaciones sobre la línea")
        lider = extract_between(bloque, "Líder estratégico", "Observaciones sobre el líder estratégico")
        cogestores = extract_between(bloque, "Cogestores", "Acción estratégica")

        acciones = parse_acciones(bloque)

        data["problematicas"].append({
            "problematica": problematica_linea,
            "id_linea": clean_text(id_linea),
            "linea_accion": clean_text(linea_accion),
            "lider": clean_text(lider),
            "cogestores": clean_text(cogestores),
            "acciones": acciones
        })

    return data


def extract_between(text: str, start: str, end: str) -> str:
    try:
        part = text.split(start, 1)[1]
        part = part.split(end, 1)[0]
        return clean_text(part)
    except Exception:
        return ""


def parse_acciones(bloque: str) -> list:
    acciones = []
    partes = re.split(r"Acción estratégica\s+\d+", bloque)

    for parte in partes[1:]:
        accion_texto = parte.split("Responsable:", 1)[0]
        responsable = extract_between(parte, "Responsable:", "Trimestres:")
        trimestres = extract_between(parte, "Trimestres:", "Observaciones sobre la acción estratégica")

        indicadores = parse_indicadores(parte)

        acciones.append({
            "accion": clean_text(accion_texto),
            "responsable": clean_text(responsable),
            "trimestres": clean_text(trimestres),
            "indicadores": indicadores
        })

    return acciones


def parse_indicadores(parte_accion: str) -> list:
    indicadores = []

    if "Indicador Meta Unidad" not in parte_accion:
        return indicadores

    tabla = parte_accion.split("Indicador Meta Unidad", 1)[1]
    tabla = tabla.split("Resultado de revisión", 1)[0]
    tabla = tabla.split("Acción estratégica", 1)[0]

    lineas = [clean_text(x) for x in tabla.split("\n") if clean_text(x)]

    # Lectura básica: intenta tomar indicador, meta y unidad de forma aproximada
    for linea in lineas:
        if len(linea) < 5:
            continue

        if "Observaciones" in linea or "indicador/meta" in linea:
            continue

        indicadores.append({
            "indicador": linea,
            "meta": "",
            "unidad": ""
        })

    return indicadores

def build_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="TitleCenter",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0B2E4A"),
        spaceAfter=12,
    ))

    styles.add(ParagraphStyle(
        name="HeaderCenter",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0B2E4A"),
        spaceAfter=6,
    ))

    styles.add(ParagraphStyle(
        name="SectionTitle",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.white,
        backColor=colors.HexColor("#0B2E4A"),
        spaceBefore=8,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name="SubTitle",
        parent=styles["Heading3"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0B2E4A"),
        spaceBefore=6,
        spaceAfter=4,
    ))

    styles.add(ParagraphStyle(
        name="Small",
        parent=styles["Normal"],
        fontSize=9,
        leading=11,
    ))

    styles.add(ParagraphStyle(
        name="NormalJust",
        parent=styles["Normal"],
        fontSize=10,
        leading=12,
        alignment=TA_LEFT,
    ))

    return styles


class EditableCanvas(canvas.Canvas):
    def __init__(self, *args, editable=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.editable = editable
        self.field_counter = 0

    def next_field_name(self, prefix):
        self.field_counter += 1
        return f"{prefix}_{self.field_counter}"

    def text_field(self, x, y, w, h, name_prefix="obs"):
        if self.editable:
            self.acroform.textfield(
                name=self.next_field_name(name_prefix),
                x=x,
                y=y,
                width=w,
                height=h,
                borderWidth=1,
                borderColor=colors.HexColor("#94A3B8"),
                fillColor=colors.white,
                textColor=colors.black,
                forceBorder=True,
                fontSize=9,
            )
        else:
            self.setStrokeColor(colors.HexColor("#94A3B8"))
            self.rect(x, y, w, h)

    def checkbox_field(self, x, y, name_prefix="chk"):
        if self.editable:
            self.acroform.checkbox(
                name=self.next_field_name(name_prefix),
                x=x,
                y=y,
                size=10,
                borderWidth=1,
                borderColor=colors.HexColor("#0B2E4A"),
                fillColor=colors.white,
                buttonStyle="check",
                forceBorder=True,
            )
        else:
            self.setStrokeColor(colors.HexColor("#0B2E4A"))
            self.rect(x, y, 10, 10)


class ObservationBox(Flowable):
    def __init__(self, height=45, prefix="obs"):
        super().__init__()
        self.height = height
        self.prefix = prefix
        self.width = 0

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.height

    def draw(self):
        if isinstance(self.canv, EditableCanvas):
            self.canv.text_field(0, 0, self.width, self.height, self.prefix)
        else:
            self.canv.rect(0, 0, self.width, self.height)


class CheckboxLine(Flowable):
    def __init__(self, text, prefix="chk"):
        super().__init__()
        self.text = text
        self.prefix = prefix
        self.width = 0
        self.height = 16

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.height

    def draw(self):
        if isinstance(self.canv, EditableCanvas):
            self.canv.checkbox_field(0, 3, self.prefix)
        else:
            self.canv.rect(0, 3, 10, 10)

        self.canv.setFont("Helvetica", 9)
        self.canv.setFillColor(colors.black)
        self.canv.drawString(16, 3, self.text)


def make_pdf(data: dict, output_type: str) -> bytes:
    editable = output_type == "editable"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = build_styles()
    story = []

    add_cover(story, styles, data)
    story.append(PageBreak())

    add_instructions(story, styles, data)

    for idx, prob in enumerate(data.get("problematicas", []), start=1):
        add_problematic_block(story, styles, prob, idx, editable)

    story.append(PageBreak())
    add_final_page(story, styles)

    doc.build(
        story,
        canvasmaker=lambda *args, **kwargs: EditableCanvas(
            *args,
            editable=editable,
            **kwargs
        )
    )

    buffer.seek(0)
    return buffer.getvalue()

def add_logo(story, width=1.5 * inch):
    if LOGO_PATH.exists():
        img = Image(str(LOGO_PATH))
        img.drawWidth = width
        img.drawHeight = width
        story.append(img)
        story.append(Spacer(1, 10))


def add_cover(story, styles, data):
    story.append(Spacer(1, 40))

    if LOGO_PATH.exists():
        img = Image(str(LOGO_PATH))
        img.drawWidth = 1.8 * inch
        img.drawHeight = 1.8 * inch
        img.hAlign = "CENTER"
        story.append(img)
        story.append(Spacer(1, 20))

    story.append(Paragraph("MINISTERIO DE SEGURIDAD PÚBLICA", styles["HeaderCenter"]))
    story.append(Paragraph("COORDINACIÓN NACIONAL", styles["HeaderCenter"]))
    story.append(Paragraph("ESTRATEGIA SEMBREMOS SEGURIDAD", styles["HeaderCenter"]))

    story.append(Spacer(1, 16))

    story.append(Paragraph(
        "PROPUESTA DE LÍNEAS DE ACCIÓN<br/>PARA VALIDACIÓN Y MEJORA",
        styles["TitleCenter"]
    ))

    story.append(Spacer(1, 10))
    story.append(Paragraph(YEAR, styles["TitleCenter"]))
    story.append(Spacer(1, 24))

    table_data = [
        ["Delegación Policial", data.get("delegacion", "")],
        ["Dirección Regional", data.get("region", "")],
        ["Fecha de generación", data.get("fecha", "")],
        ["Versión", data.get("version", "")],
        ["Estado", data.get("estado", "")],
        ["Elaborado por", data.get("elaborado_por", "")],
        ["Cargo", data.get("cargo", "")],
    ]

    table = Table(table_data, colWidths=[2.0 * inch, 4.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8EDF3")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0B2E4A")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))

    story.append(table)


def add_instructions(story, styles, data):
    story.append(Paragraph("INSTRUCCIONES PARA LA REVISIÓN", styles["SectionTitle"]))

    text = (
        "El presente documento contiene la propuesta preliminar de líneas de acción, "
        "acciones estratégicas e indicadores construidos a partir del análisis territorial. "
        "La persona revisora deberá verificar la coherencia entre la problemática priorizada, "
        "la línea de acción propuesta, la acción estratégica definida, los indicadores planteados, "
        "las metas, unidades y el líder estratégico asignado. "
        "Las observaciones registradas servirán como insumo para la elaboración de la versión corregida "
        "o versión final."
    )

    story.append(Paragraph(text, styles["NormalJust"]))
    story.append(Spacer(1, 12))


def add_problematic_block(story, styles, prob, number, editable):
    story.append(Paragraph(
        f"PROBLEMÁTICA {number}: {prob.get('problematica', '')}",
        styles["SectionTitle"]
    ))

    story.append(Paragraph("ID Línea", styles["SubTitle"]))
    story.append(Paragraph(prob.get("id_linea", ""), styles["NormalJust"]))

    story.append(Paragraph("Línea de acción propuesta", styles["SubTitle"]))
    story.append(Paragraph(prob.get("linea_accion", ""), styles["NormalJust"]))

    story.append(Paragraph("Observaciones sobre la línea", styles["SubTitle"]))
    story.append(ObservationBox(height=50, prefix=f"obs_linea_{number}"))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Líder estratégico", styles["SubTitle"]))
    story.append(Paragraph(prob.get("lider", ""), styles["NormalJust"]))

    story.append(Paragraph("Observaciones sobre el líder estratégico", styles["SubTitle"]))
    story.append(ObservationBox(height=38, prefix=f"obs_lider_{number}"))
    story.append(Spacer(1, 8))

    if prob.get("cogestores"):
        story.append(Paragraph("Cogestores", styles["SubTitle"]))
        story.append(Paragraph(prob.get("cogestores", ""), styles["NormalJust"]))
        story.append(Spacer(1, 8))

    acciones = prob.get("acciones", [])

    if not acciones:
        story.append(Paragraph("No se registran acciones estratégicas.", styles["Small"]))
    else:
        for a_idx, accion in enumerate(acciones, start=1):
          story.append(Paragraph(f"Acción estratégica {a_idx}", styles["SubTitle"]))
          story.append(Paragraph(accion.get("accion", ""), styles["NormalJust"]))
          story.append(Paragraph(f"<b>Responsable:</b> {accion.get('responsable', '')}", styles["Small"]))
          story.append(Paragraph(f"<b>Trimestres:</b> {accion.get('trimestres', '')}", styles["Small"]))

          story.append(Paragraph("Observaciones sobre la acción estratégica", styles["SubTitle"]))
          story.append(ObservationBox(height=40, prefix=f"obs_accion_{number}_{a_idx}"))
          story.append(Spacer(1, 6))

          indicadores = accion.get("indicadores", [])

          if indicadores:
              table_data = [["Indicador", "Meta", "Unidad", "Observaciones"]]

              for i_idx, ind in enumerate(indicadores, start=1):
                  table_data.append([
                      Paragraph(ind.get("indicador", ""), styles["Small"]),
                      Paragraph(ind.get("meta", ""), styles["Small"]),
                      Paragraph(ind.get("unidad", ""), styles["Small"]),
                      ObservationBox(height=38, prefix=f"obs_ind_{number}_{a_idx}_{i_idx}")
                  ])

              table = Table(
                  table_data,
                  colWidths=[2.8 * inch, 0.8 * inch, 1.0 * inch, 2.1 * inch],
                  repeatRows=1
              )
              table.setStyle(TableStyle([
                  ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B2E4A")),
                  ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                  ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                  ("VALIGN", (0, 0), (-1, -1), "TOP"),
                  ("FONTSIZE", (0, 0), (-1, -1), 8),
                  ("PADDING", (0, 0), (-1, -1), 4),
              ]))
              story.append(table)
              story.append(Spacer(1, 10))

    story.append(Paragraph("Resultado de revisión de la problemática", styles["SubTitle"]))
    story.append(CheckboxLine("Sin observaciones", prefix=f"chk_sin_obs_{number}"))
    story.append(CheckboxLine("Con observaciones de mejora", prefix=f"chk_con_obs_{number}"))
    story.append(CheckboxLine("Requiere reformulación parcial", prefix=f"chk_parcial_{number}"))
    story.append(CheckboxLine("Requiere reformulación total", prefix=f"chk_total_{number}"))

    story.append(Paragraph("Observaciones generales de la problemática", styles["SubTitle"]))
    story.append(ObservationBox(height=60, prefix=f"obs_general_{number}"))

    story.append(PageBreak())


def add_final_page(story, styles):
    story.append(Spacer(1, 80))

    if LOGO_PATH.exists():
        img = Image(str(LOGO_PATH))
        img.drawWidth = 1.6 * inch
        img.drawHeight = 1.6 * inch
        img.hAlign = "CENTER"
        story.append(img)
        story.append(Spacer(1, 20))

    story.append(Paragraph("COORDINACIÓN NACIONAL", styles["HeaderCenter"]))
    story.append(Paragraph("ESTRATEGIA SEMBREMOS SEGURIDAD", styles["HeaderCenter"]))
    story.append(Spacer(1, 16))

    story.append(Paragraph("Correo:", styles["HeaderCenter"]))
    story.append(Paragraph(CONTACT_EMAIL, styles["HeaderCenter"]))

    story.append(Spacer(1, 16))
    story.append(Paragraph("MINISTERIO DE SEGURIDAD PÚBLICA", styles["HeaderCenter"]))
    story.append(Paragraph(YEAR, styles["TitleCenter"]))

def make_output_filename(original_name: str, output_type: str) -> str:
    base = Path(original_name).stem

    if output_type == "editable":
        suffix = "_EDITABLE.pdf"
    else:
        suffix = "_FINAL.pdf"

    return f"{base}{suffix}"


def preview_data(data: dict):
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

    problematicas = data.get("problematicas", [])

    if not problematicas:
        st.warning("No se detectaron problemáticas en el PDF.")
        return

    for idx, prob in enumerate(problematicas, start=1):
        with st.expander(f"Problemática {idx}: {prob.get('problematica', '')}", expanded=False):
            st.write("**ID Línea:**", prob.get("id_linea", ""))
            st.write("**Línea de acción:**", prob.get("linea_accion", ""))
            st.write("**Líder estratégico:**", prob.get("lider", ""))
            st.write("**Cogestores:**", prob.get("cogestores", ""))

            acciones = prob.get("acciones", [])

            if acciones:
                st.write("### Acciones estratégicas")
                for a_idx, accion in enumerate(acciones, start=1):
                    st.write(f"**Acción {a_idx}:** {accion.get('accion', '')}")
                    st.write(f"Responsable: {accion.get('responsable', '')}")
                    st.write(f"Trimestres: {accion.get('trimestres', '')}")

                    indicadores = accion.get("indicadores", [])
                    if indicadores:
                        st.write("**Indicadores:**")
                        for i_idx, indicador in enumerate(indicadores, start=1):
                            st.write(
                                f"{i_idx}. {indicador.get('indicador', '')} "
                                f"| Meta: {indicador.get('meta', '')} "
                                f"| Unidad: {indicador.get('unidad', '')}"
                            )
            else:
                st.info("No se detectaron acciones estratégicas.")


def main():
    st.title("Editor de Propuestas CLA - ESS")
    st.caption("Coordinación Nacional · Estrategia Sembremos Seguridad · 2026")

    st.markdown(
        """
        Esta herramienta recibe el PDF de propuesta generado desde el constructor,
        reconstruye el contenido y genera una versión optimizada para revisión.
        """
    )

    uploaded_pdf = st.file_uploader(
        "Subir PDF de propuesta",
        type=["pdf"]
    )

    output_type = st.radio(
        "Tipo de salida",
        options=["editable", "final"],
        format_func=lambda x: "Versión editable para revisión" if x == "editable" else "Versión final sin campos editables",
        horizontal=False
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
            pdf_bytes = make_pdf(data, output_type)

            output_name = make_output_filename(uploaded_pdf.name, output_type)

            st.success("PDF generado correctamente.")

            st.download_button(
                label="Descargar PDF generado",
                data=pdf_bytes,
                file_name=output_name,
                mime="application/pdf"
            )

    except Exception as e:
        st.error(f"No se pudo procesar el PDF: {e}")


if __name__ == "__main__":
    main()
