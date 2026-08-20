import streamlit as st
import pandas as pd
import re
import requests
import io
import os
import time
import json
import base64
from datetime import datetime
from PIL import Image

try:
    import fitz  # PyMuPDF
    PDF_OK = True
except Exception:
    PDF_OK = False

try:
    import pytesseract
    if os.name == "nt":
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    OCR_OK = True
except Exception:
    OCR_OK = False

# ================= CONFIGURACIÓN =================
st.set_page_config(page_title="Sistema Inventario TCG", page_icon="🖥️", layout="wide")

ARCHIVO_DATOS = "registros.json"
ARCHIVO_CLIENTE = "cliente.json"
ARCHIVO_HISTORIAL = "historial.json"
ARCHIVO_POS_CLIENTE = "pos_cliente.json"
ARCHIVO_LOGS = "logs_borrado.json"
COLUMNAS = ["Serial", "Costumer", "Marca", "Modelo", "Tipo", "Status", "PO", "Usuario", "Serial Disco", "Modelo Disco", "Capacidad", "Log Borrado", "Cert Borrado", "Fecha"]

USUARIOS = {
    "admin": "admin123",
    "enrique": "weba2026",
}

# ================= FUNCIONES =================
def limpiar_serial(texto):
    s = str(texto).strip().upper()
    for char in [" ", "-", ":", "/", ".", "_"]:
        s = s.replace(char, "")
    prefijos = ["SERVICETAG", "SERIAL", "MODEL", "S/N", "P/N", "M/N", "TAG", "PN", "SN"]
    for p in prefijos:
        if s.startswith(p):
            s = s[len(p):]
            break
    return s

def longitud_ok(s):
    return 5 <= len(s) <= 20

def detectar_marca(serial):
    if serial.startswith('PF') or 'LNV' in serial or re.match(r'^[A-Z]{2}[0-9]', serial): return 'LENOVO'
    if re.match(r'^[A-Z0-9]{7}$', serial): return 'DELL'
    if re.match(r'^[A-Z0-9]{8,10}$', serial): return 'HP'
    return 'DESCONOCIDA'

def detectar_tipo(modelo):
    m = str(modelo).lower()
    if any(x in m for x in ["monitor", "pantalla", "thinkvision", "e2216", "p2419", "u2415", "p24h"]):
        return "MONITOR"
    if any(x in m for x in ["phone", "celular", "smartphone", "moto g", "galaxy a", "galaxy s", "redmi", "iphone"]):
        return "CELULAR"
    if any(x in m for x in ["tablet", "ipad", "tab m", "tab p"]):
        return "TABLETA"
    if any(x in m for x in ["laptop", "notebook", "thinkpad", "ideapad", "elitebook", "probook", "latitude", "inspiron", "xps", "spectre", "envy", "yoga"]):
        return "LAPTOP"
    if any(x in m for x in ["desktop", "optiplex", "thinkcentre", "prodesk", "tower", "all-in-one"]):
        return "CPU"
    return ""

def consultar_dell(serial):
    try:
        url = f"https://api.dell.com/support/v2/assetinfo/warranty/tags.json?svctags={serial}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        asset = data['GetAssetWarrantyResponse']['GetAssetWarrantyResult']['Response']['DellAsset']
        return asset.get('MachineDescription', '')
    except Exception:
        return ""

def consultar_lenovo(serial):
    try:
        url = f"https://pcsupport.lenovo.com/us/en/api/v1/warranty?serial={serial}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        texto = json.dumps(r.json())
        m = re.search(r'(ThinkPad|ThinkCentre|IdeaPad|IdeaCentre|Legion|Yoga)\s+[A-Z0-9]{1,6}', texto)
        if m:
            return m.group(0)
    except Exception:
        pass
    try:
        url2 = f"https://pcsupport.lenovo.com/us/en/warranty/{serial}"
        r2 = requests.get(url2, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        m2 = re.search(r'(ThinkPad|ThinkCentre|IdeaPad|IdeaCentre|Legion|Yoga)\s+[A-Z0-9]{1,6}', r2.text)
        if m2:
            return m2.group(0)
    except Exception:
        pass
    return ""

def norm_tipo(t):
    t = str(t).upper()
    if "LAPTOP" in t or "NOTEBOOK" in t: return "LAPTOP"
    if "CPU" in t or "PC" in t or "DESKTOP" in t or "TOWER" in t: return "CPU"
    if "MONITOR" in t: return "MONITOR"
    if "CELULAR" in t or "PHONE" in t: return "CELULAR"
    if "TABLET" in t: return "TABLETA"
    if t in ("", "NAN", "NONE"): return ""
    return "OTHER"

def col_por_palabras(df, palabras):
    for c in df.columns:
        if any(p in str(c).lower() for p in palabras):
            return c
    return None

def detectar_col_serial(df):
    mejor_col = None
    mejor_puntaje = 0
    for col in df.columns:
        puntaje = 0
        for val in df[col].dropna().astype(str).head(20):
            s = limpiar_serial(val)
            if longitud_ok(s) and re.match(r'^[A-Z0-9]+$', s):
                puntaje += 1
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_col = col
    return mejor_col if mejor_puntaje >= 1 else None

def parece_serial(tok):
    if not (7 <= len(tok) <= 10):
        return False
    if not re.match(r'^[A-Z0-9]+$', tok):
        return False
    if not any(c.isdigit() for c in tok):
        return False
    if not any(c.isalpha() for c in tok):
        return False
    return True

def leer_pdf_seriales(bytes_arch):
    doc = fitz.open(stream=bytes_arch, filetype="pdf")
    texto = ""
    for pagina in doc:
        texto += pagina.get_text()
    tokens = re.findall(r'\b[A-Z0-9]{7,10}\b', texto.upper())
    seriales = [t for t in tokens if parece_serial(t)]
    origen = "texto digital"
    if len(seriales) < 3 and OCR_OK:
        texto_ocr = ""
        for pagina in doc:
            pix = pagina.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            texto_ocr += pytesseract.image_to_string(img, lang="spa+eng")
        tokens = re.findall(r'\b[A-Z0-9]{7,10}\b', texto_ocr.upper())
        seriales = [t for t in tokens if parece_serial(t)]
        origen = "OCR (escaneado)"
    return sorted(set(seriales)), origen

def leer_log_xerase(raw):
    def campo(clave):
        m = re.search(rf"^\s*{clave}\s*:\s*(.*)$", raw, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ""
    capacidad = campo("Capacity")
    m_cap = re.search(r'([0-9]+(?:\.[0-9]+)?\s?(?:GB|TB|MB))', capacidad, re.IGNORECASE)
    if m_cap:
        capacidad = m_cap.group(1).upper().replace(" ", "")
    sys_sn = campo("System Serial Number")
    if not re.match(r'^[A-Z0-9]{5,15}$', sys_sn or ""):
        sys_sn = ""
    return {
        "fabricante_disco": campo("Manufacturer"),
        "modelo_disco": campo("Model"),
        "serial_disco": campo("Serial Number"),
        "capacidad": capacidad,
        "grade": campo("Grade"),
        "ispf": campo("ISPF"),
        "system_sn": sys_sn,
        "system_model": campo("System Model"),
        "resultado": "Passed" if re.search(r'Erasure Results\s*:.*Passed', raw, re.IGNORECASE | re.DOTALL) else "",
    }

def leer_certificado_xerase(bytes_arch):
    doc = fitz.open(stream=bytes_arch, filetype="pdf")
    filas = []
    encabezados = None
    for pagina in doc:
        for t in pagina.find_tables().tables:
            for row in t.extract():
                if not row or all(c is None for c in row):
                    continue
                celdas = [str(c).replace("\n", " ").strip() if c else "" for c in row]
                up = [c.upper() for c in celdas]
                if "SERIALNUM" in up and "SYSTEM_SN" in up:
                    encabezados = up
                    continue
                if encabezados and re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}', celdas[0]):
                    filas.append(dict(zip(encabezados, celdas)))
    return filas

def cargar_datos():
    if os.path.exists(ARCHIVO_DATOS):
        try:
            df = pd.read_json(ARCHIVO_DATOS, orient="records", dtype=str)
            return df.reindex(columns=COLUMNAS).fillna("")
        except Exception:
            pass
    return pd.DataFrame(columns=COLUMNAS)

def guardar_datos(df):
    try:
        df.to_json(ARCHIVO_DATOS, orient="records")
    except Exception:
        pass

def cargar_json(nombre):
    if os.path.exists(nombre):
        try:
            with open(nombre, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def guardar_json(nombre, datos):
    with open(nombre, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)

def mostrar_logo(grande=False):
    if os.path.exists("logo.png"):
        with open("logo.png", "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ancho = 220 if grande else 110
        st.markdown(f"<div style='display:flex; justify-content:center; margin:10px 0;'><img src='data:image/png;base64,{img_b64}' width='{ancho}' style='border-radius:15px; box-shadow:0 4px 12px rgba(0,0,0,0.4);'></div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='font-size:70px; text-align:center'>🖥️</div>", unsafe_allow_html=True)

# ================= SESIÓN =================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "usuario" not in st.session_state:
    st.session_state.usuario = ""
if "po_actual" not in st.session_state:
    st.session_state.po_actual = ""
if "df" not in st.session_state:
    st.session_state.df = cargar_datos()
if "df_cliente" not in st.session_state:
    if os.path.exists(ARCHIVO_CLIENTE):
        try:
            st.session_state.df_cliente = pd.read_json(ARCHIVO_CLIENTE, orient="records", dtype=str)
        except Exception:
            st.session_state.df_cliente = None
    else:
        st.session_state.df_cliente = None

if not st.session_state.logged_in:
    tk = st.query_params.get("tk")
    if tk in USUARIOS:
        st.session_state.logged_in = True
        st.session_state.usuario = tk

# ================= LOGIN =================
if not st.session_state.logged_in:
    st.markdown("""
        <style>
        .bienvenido {text-align:center; color:#1f4e79; font-size:28px; font-weight:bold;}
        .sub {text-align:center; color:gray;}
        </style>
    """, unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        mostrar_logo(grande=True)
        st.markdown("<div class='bienvenido'>¡BIENVENIDO!</div>", unsafe_allow_html=True)
        st.markdown("<div class='sub'>Sistema de Inventario de Equipos — Inicia sesión</div>", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        usuario = st.text_input("👤 Usuario")
        password = st.text_input("🔑 Contraseña", type="password")
        if st.button("🔓 Entrar al sistema", use_container_width=True):
            if usuario in USUARIOS and USUARIOS[usuario] == password:
                st.session_state.logged_in = True
                st.session_state.usuario = usuario
                st.query_params["tk"] = usuario
                st.rerun()
            else:
                st.error("❌ Usuario o contraseña incorrectos")
    st.stop()

# ================= MEMORIA MULTI-PO =================
historial = cargar_json(ARCHIVO_HISTORIAL)
pos_cliente = cargar_json(ARCHIVO_POS_CLIENTE)

mapa_pos_cliente = {}
for po, regs in pos_cliente.items():
    dfp = pd.DataFrame(regs)
    if len(dfp) == 0:
        continue
    col = col_por_palabras(dfp, ["serial", "service", "tag", "serie"]) or detectar_col_serial(dfp)
    if col is None:
        col = dfp.columns[0]
    for v in dfp[col].dropna():
        s = limpiar_serial(v)
        if s and s not in mapa_pos_cliente:
            mapa_pos_cliente[s] = po

# ================= BARRA LATERAL =================
st.sidebar.title("⚙️ Menú")
st.sidebar.markdown(f"👤 **Usuario:** {st.session_state.usuario.upper()}")
if st.sidebar.button("🚪 Cerrar sesión"):
    st.session_state.logged_in = False
    st.query_params.clear()
    st.rerun()
st.sidebar.divider()

po_input = st.sidebar.text_input("🏷️ PO / Proyecto activo", value=st.session_state.po_actual, placeholder="Ej: PO_BANCO_2026")
st.session_state.po_actual = po_input.strip().upper()

archivo_cliente = st.sidebar.file_uploader("📂 Subir base del cliente (PO)", type=["xlsx", "xls", "xlsm", "csv", "pdf"])
if archivo_cliente:
    st.session_state["archivo_bytes"] = archivo_cliente.getvalue()
    st.session_state["archivo_nombre"] = archivo_cliente.name
    st.session_state["recargar_po"] = True

skip_po = st.sidebar.number_input("🔧 Fila donde está el encabezado", min_value=1, max_value=30, step=1,
                                  value=st.session_state.get("skip_po", 1),
                                  help="Si el archivo tiene títulos o logos arriba, sube este número hasta que la Radiografía muestre seriales válidos.")
if skip_po != st.session_state.get("skip_po", 1):
    st.session_state["skip_po"] = skip_po
    st.session_state["recargar_po"] = True

if st.session_state.get("recargar_po") and st.session_state.get("archivo_bytes"):
    try:
        bytes_arch = st.session_state["archivo_bytes"]
        nombre = st.session_state["archivo_nombre"]
        if nombre.endswith(".csv"):
            dfc_nueva = pd.read_csv(io.BytesIO(bytes_arch), dtype=str)
            hojas = ["CSV"]
            hoja_usada = "CSV"
        elif nombre.lower().endswith(".pdf"):
            if not PDF_OK:
                raise Exception("Falta soporte PDF. Ejecuta: pip install pymupdf")
            seriales_pdf, origen_pdf = leer_pdf_seriales(bytes_arch)
            st.session_state["pdf_seriales"] = seriales_pdf
            dfc_nueva = pd.DataFrame({"Serial": seriales_pdf})
            hojas = ["PDF"]
            hoja_usada = f"PDF ({origen_pdf})"
        else:
            xls = pd.ExcelFile(io.BytesIO(bytes_arch), engine="openpyxl")
            hojas = [h.upper() for h in xls.sheet_names]
            idx_po = None
            for i, h in enumerate(hojas):
                if "CLIENTE" in h and "CAPTURA" not in h:
                    idx_po = i
                    break
            hoja_usada = xls.sheet_names[idx_po] if idx_po is not None else xls.sheet_names[0]
            dfc_nueva = xls.parse(hoja_usada, header=skip_po - 1, dtype=str)

            if "TABLA_CAPTURA" in hojas:
                dft = xls.parse(xls.sheet_names[hojas.index("TABLA_CAPTURA")], header=5, dtype=str)
                col_ser = col_por_palabras(dft, ["serial", "interno"]) or detectar_col_serial(dft)
                if col_ser:
                    existentes = set(st.session_state.df["Serial"])
                    nuevas = []
                    for _, r in dft.iterrows():
                        s = limpiar_serial(r[col_ser])
                        if longitud_ok(s) and s not in existentes:
                            existentes.add(s)
                            nuevas.append({"Serial": s, "Costumer": "", "Marca": detectar_marca(s), "Modelo": "",
                                           "Tipo": "", "Status": "Importado de Excel", "PO": st.session_state.po_actual or "IMPORTADO",
                                           "Usuario": st.session_state.usuario, "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M")})
                    if nuevas:
                        st.session_state.df = pd.concat([st.session_state.df, pd.DataFrame(nuevas)], ignore_index=True)
                        guardar_datos(st.session_state.df)

        col_det = col_por_palabras(dfc_nueva, ["serial", "service", "tag", "serie"]) or detectar_col_serial(dfc_nueva)
        if col_det is None and len(dfc_nueva.columns) > 0:
            col_det = dfc_nueva.columns[0]
        validos = 0
        if col_det is not None:
            validos = sum(1 for v in dfc_nueva[col_det].dropna() if longitud_ok(limpiar_serial(v)))
        st.session_state["xray"] = (
            f"Archivo: {nombre}\n"
            f"Hojas: {hojas}\n"
            f"Hoja usada: {hoja_usada}\n"
            f"Fila de encabezado: {skip_po}\n"
            f"Columnas leídas: {list(dfc_nueva.columns)[:8]}\n"
            f"Columna serial detectada: {col_det}\n"
            f"SERIALES VÁLIDOS ENCONTRADOS: {validos}\n"
            f"Primeras filas:\n{dfc_nueva.head(5).to_string()}"
        )

        nombre_po = st.session_state.po_actual or os.path.splitext(nombre)[0].upper()
        st.session_state.df_cliente = dfc_nueva
        st.session_state.df_cliente.to_json(ARCHIVO_CLIENTE, orient="records")
        pos_cliente[nombre_po] = dfc_nueva.to_dict(orient="records")
        guardar_json(ARCHIVO_POS_CLIENTE, pos_cliente)
        st.session_state["recargar_po"] = False
        st.sidebar.success(f"✅ PO '{nombre_po}' cargada (seriales válidos: {validos})")
        st.rerun()
    except Exception as e:
        st.session_state["recargar_po"] = False
        st.sidebar.error(f"Error al leer: {e}")

with st.sidebar.expander("🔍 Trazabilidad de serial"):
    s_bus = st.text_input("Serial a investigar", key="busca_serial")
    if s_bus:
        s_bus = limpiar_serial(s_bus)
        info = historial.get(s_bus)
        if info:
            st.sidebar.write(f"📌 Registrado en PO(s): **{', '.join(info['pos'])}**")
            st.sidebar.write(f"👤 Por: {info['usuario']} | 🕐 {info['primera']}")
        else:
            st.sidebar.write("❌ Nunca registrado en el sistema.")
        if s_bus in mapa_pos_cliente:
            st.sidebar.write(f"📋 Aparece en la PO del cliente: **{mapa_pos_cliente[s_bus]}**")
        else:
            st.sidebar.write("📋 No aparece en ninguna PO del cliente.")

if st.sidebar.button("🗑️ Reiniciar todos los datos"):
    st.session_state.df = pd.DataFrame(columns=COLUMNAS)
    guardar_datos(st.session_state.df)
    st.session_state.df_cliente = None
    st.session_state["xray"] = None
    st.session_state["archivo_bytes"] = None
    st.session_state["skip_po"] = 1
    for f in [ARCHIVO_CLIENTE, ARCHIVO_HISTORIAL, ARCHIVO_POS_CLIENTE, ARCHIVO_LOGS]:
        if os.path.exists(f):
            os.remove(f)
    st.sidebar.info("Sistema reiniciado por completo")

# ================= ENCABEZADO =================
ca, cb = st.columns([1, 5])
with ca:
    mostrar_logo()
with cb:
    st.title("Sistema de Inventario TCG")
    st.success(f"👋 ¡Bienvenido, {st.session_state.usuario.upper()}! | 🏷️ PO activa: {st.session_state.po_actual or 'SIN PO'}")

if st.session_state.get("xray"):
    with st.expander("🩻 Radiografía del archivo (diagnóstico)", expanded=False):
        st.code(st.session_state["xray"])
        if "SERIALES VÁLIDOS ENCONTRADOS: 0" in st.session_state["xray"]:
            st.warning("⚠️ El sistema encontró 0 seriales. Ajusta el número de **'Fila donde está el encabezado'** en la barra lateral hasta que aparezcan.")

if st.session_state.get("pdf_seriales"):
    buf_pdf = io.BytesIO()
    pd.DataFrame({"Serial": st.session_state["pdf_seriales"]}).to_excel(buf_pdf, index=False, engine="openpyxl")
    st.download_button("⬇️ Descargar Excel convertido desde el PDF", data=buf_pdf.getvalue(),
                       file_name="PO_convertida_de_PDF.xlsx")

df = st.session_state.df
dfc = st.session_state.df_cliente

# ================= CÁLCULOS =================
interno = len(df)
cliente = len(dfc) if dfc is not None else 0

set_cliente = set()
col_tipo_cliente = None
if dfc is not None:
    col_ser_c = col_por_palabras(dfc, ["serial", "service", "tag", "serie"]) or detectar_col_serial(dfc)
    if col_ser_c is None and len(dfc.columns) > 0:
        col_ser_c = dfc.columns[0]
    if col_ser_c:
        set_cliente = {limpiar_serial(x) for x in dfc[col_ser_c].dropna()}
        set_cliente.discard("")
    col_tipo_cliente = col_por_palabras(dfc, ["tipo", "arquitectura"])

if set_cliente:
    coinciden = int(df["Serial"].isin(set_cliente).sum())
    no_coinciden = interno - coinciden
else:
    coinciden = "—"
    no_coinciden = "—"

tipos_esc = df["Tipo"].map(norm_tipo)
pc_esc = int((tipos_esc == "CPU").sum())
lap_esc = int((tipos_esc == "LAPTOP").sum())
oth_esc = int(tipos_esc.isin(["OTHER", "MONITOR", "CELULAR", "TABLETA"]).sum())

pc_cli = lap_cli = oth_cli = 0
if dfc is not None and col_tipo_cliente:
    tipos_cli = dfc[col_tipo_cliente].map(norm_tipo)
    pc_cli = int((tipos_cli == "CPU").sum())
    lap_cli = int((tipos_cli == "LAPTOP").sum())
    oth_cli = int(tipos_cli.isin(["OTHER", "MONITOR", "CELULAR", "TABLETA"]).sum())

progreso = (interno / cliente) if cliente else 0

# ================= DATOS TOTALES =================
st.markdown("## 📊 DATOS TOTALES")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Equipos Interno", interno)
c2.metric("Equipos Cliente", cliente if cliente else "—")
c3.metric("Restantes", max(cliente - interno, 0) if cliente else "—")
c4.metric("Progreso", f"{progreso:.0%}")
st.progress(min(progreso, 1.0))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Coinciden", coinciden)
c2.metric("No coinciden", no_coinciden)
c3.metric("PC Escaneada", pc_esc)
c4.metric("PC Total PO Cliente", pc_cli if cliente else "—")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Laptops Escaneada", lap_esc)
c2.metric("Laptop Total PO Cliente", lap_cli if cliente else "—")
c3.metric("Others Escaneada", oth_esc)
c4.metric("Other Cliente", oth_cli if cliente else "—")

st.divider()

# ================= CAPTURA =================
tab1, tab2 = st.tabs(["📷 Captura individual (escáner)", "📋 Carga masiva"])

with tab1:
    with st.form("form_individual"):
        fa, fb = st.columns([2, 1])
        serial_txt = fa.text_input("Serial escaneado o escrito")
        costumer_txt = fb.text_input("Costumer (opcional)")
        enviar = st.form_submit_button("✅ Registrar")
    if enviar and serial_txt:
        serial = limpiar_serial(serial_txt)
        existentes = set(df["Serial"])
        if not longitud_ok(serial):
            st.error(f"⚠️ LONGITUD INCORRECTA: {len(serial)} caracteres. Debe ser entre 5 y 20.")
        elif serial in existentes:
            fila = df[df["Serial"] == serial].iloc[0]
            po_previa = fila["PO"] or "SIN PO"
            if po_previa != (st.session_state.po_actual or "SIN PO"):
                st.error(f"🚨 TRAZABILIDAD: Este serial YA fue registrado en la PO '{po_previa}' por {fila['Usuario'] or 'alguien'} el {fila['Fecha']}. Revisa si está en la PO equivocada.")
            else:
                st.error(f"⚠️ SERIAL DUPLICADO en esta misma PO ({po_previa}).")
        else:
            if set_cliente and serial in set_cliente:
                status_nuevo = "Coincide"
            elif serial in mapa_pos_cliente:
                status_nuevo = f"Pertenece a PO {mapa_pos_cliente[serial]}"
            elif set_cliente:
                status_nuevo = "No está en PO"
            else:
                status_nuevo = "Pendiente"
            po_nueva = st.session_state.po_actual or "SIN_PO"
            nueva = pd.DataFrame([{
                "Serial": serial, "Costumer": costumer_txt.strip().upper(), "Marca": detectar_marca(serial),
                "Modelo": "", "Tipo": "", "Status": status_nuevo, "PO": po_nueva,
                "Usuario": st.session_state.usuario, "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M")
            }])
            st.session_state.df = pd.concat([st.session_state.df, nueva], ignore_index=True)
            guardar_datos(st.session_state.df)
            entrada = historial.get(serial, {"pos": [], "primera": datetime.now().strftime("%Y-%m-%d %H:%M"), "usuario": st.session_state.usuario})
            if po_nueva not in entrada["pos"]:
                entrada["pos"].append(po_nueva)
            historial[serial] = entrada
            guardar_json(ARCHIVO_HISTORIAL, historial)
            if status_nuevo == "Coincide":
                st.success(f"✅ {serial} registrado en PO '{po_nueva}' — ✔️ COINCIDE con la PO del cliente")
            elif status_nuevo.startswith("Pertenece"):
                st.warning(f"⚠️ {serial} registrado en '{po_nueva}', pero OJO: {status_nuevo} del cliente.")
            elif status_nuevo == "No está en PO":
                st.warning(f"⚠️ {serial} registrado en '{po_nueva}', pero NO está en la PO del cliente.")
            else:
                st.success(f"✅ {serial} registrado en PO '{po_nueva}' ({detectar_marca(serial)})")
            st.rerun()

with tab2:
    with st.form("form_masiva"):
        texto = st.text_area("Pega los seriales aquí (uno por línea)")
        enviar2 = st.form_submit_button("✅ Registrar todos")
    if enviar2 and texto:
        existentes = set(df["Serial"])
        nuevas = []
        ok = dup = mal = 0
        po_nueva = st.session_state.po_actual or "SIN_PO"
        for linea in texto.splitlines():
            serial = limpiar_serial(linea)
            if not serial:
                continue
            if not longitud_ok(serial):
                mal += 1
                continue
            if serial in existentes:
                dup += 1
                continue
            existentes.add(serial)
            nuevas.append({"Serial": serial, "Costumer": "", "Marca": detectar_marca(serial), "Modelo": "",
                           "Tipo": "", "Status": "Pendiente", "PO": po_nueva,
                           "Usuario": st.session_state.usuario, "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M")})
            entrada = historial.get(serial, {"pos": [], "primera": datetime.now().strftime("%Y-%m-%d %H:%M"), "usuario": st.session_state.usuario})
            if po_nueva not in entrada["pos"]:
                entrada["pos"].append(po_nueva)
            historial[serial] = entrada
            ok += 1
        if nuevas:
            st.session_state.df = pd.concat([st.session_state.df, pd.DataFrame(nuevas)], ignore_index=True)
            guardar_datos(st.session_state.df)
            guardar_json(ARCHIVO_HISTORIAL, historial)
        st.success(f"✅ Registrados en PO '{po_nueva}': {ok} | ❌ Duplicados: {dup} | ⚠️ Longitud incorrecta: {mal}")
        st.rerun()

# ================= BOTONES DE ACCIÓN =================
st.divider()
ba, bb, bc = st.columns(3)

if ba.button("🔗 Rellenar desde base cliente"):
    if dfc is not None:
        col_ser_c = col_por_palabras(dfc, ["serial", "service", "tag", "serie"]) or detectar_col_serial(dfc)
        col_mod_c = col_por_palabras(dfc, ["model", "producto", "descripcion"])
        col_tip_c = col_tipo_cliente
        if col_ser_c:
            mapa = {}
            for _, r in dfc.iterrows():
                s = limpiar_serial(r[col_ser_c])
                mapa[s] = (str(r[col_mod_c]) if col_mod_c else "", str(r[col_tip_c]) if col_tip_c else "")
            for i, row in st.session_state.df.iterrows():
                if row["Serial"] in mapa:
                    mod, tip = mapa[row["Serial"]]
                    if mod and mod != "nan":
                        st.session_state.df.at[i, "Modelo"] = mod
                    if tip and tip != "nan":
                        st.session_state.df.at[i, "Tipo"] = tip.upper()
                    st.session_state.df.at[i, "Status"] = "En base cliente"
            guardar_datos(st.session_state.df)
            st.success("Datos rellenados desde la base cliente ✅")
            st.rerun()
    else:
        st.warning("Primero sube la base cliente en la barra lateral.")

if bb.button("🔍 Consultar modelos (Dell + Lenovo)"):
    pendientes = st.session_state.df[(st.session_state.df["Modelo"] == "") & (st.session_state.df["Marca"].isin(["DELL", "LENOVO"]))]
    if len(pendientes) == 0:
        st.info("No hay Dell/Lenovo pendientes de consulta.")
    else:
        bar = st.progress(0)
        for n, (idx, row) in enumerate(pendientes.iterrows()):
            modelo = consultar_dell(row["Serial"]) if row["Marca"] == "DELL" else consultar_lenovo(row["Serial"])
            if modelo:
                st.session_state.df.at[idx, "Modelo"] = modelo
                t = detectar_tipo(modelo)
                if t:
                    st.session_state.df.at[idx, "Tipo"] = t
                st.session_state.df.at[idx, "Status"] = "Consultado"
            bar.progress((n + 1) / len(pendientes))
            time.sleep(1)
        guardar_datos(st.session_state.df)
        st.success("Consulta Dell/Lenovo terminada ✅")
        st.rerun()

df_final = st.session_state.df
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as w:
    df_final.to_excel(w, index=False, sheet_name="TABLA_CAPTURA")
bc.download_button("⬇️ Descargar Excel", buffer.getvalue(),
                   file_name=f"inventario_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx")

# ================= TABLA CAPTURA =================
st.markdown("## 📋 TABLA CAPTURA")
st.caption("Edita Modelo, Tipo, Costumer o PO directamente aquí si necesitas corregir algo.")
df_editado = st.data_editor(st.session_state.df, use_container_width=True, hide_index=True, num_rows="dynamic")
if st.button("💾 Guardar cambios de la tabla"):
    st.session_state.df = df_editado.fillna("")
    guardar_datos(st.session_state.df)
    st.success("Cambios guardados ✅")
    st.rerun()

# ================= FASE 2: LOGS XERASE (TXT) =================
st.divider()
st.markdown("## 💾 FASE 2 — LOGS DE BORRADO XERASE (TXT)")
st.caption("Sube los .txt de la USB (todos juntos). Extrae: fabricante, modelo y serial de disco, capacidad, grado e ISPF.")

logs_borrado = cargar_json(ARCHIVO_LOGS)

log_files = st.file_uploader("Subir logs Xerase (TXT/LOG)", type=["txt", "log"], accept_multiple_files=True, key="logs")
if log_files:
    for lf in log_files:
        if lf.name in st.session_state.get("logs_procesados", set()):
            continue
        st.session_state.setdefault("logs_procesados", set()).add(lf.name)
        raw = lf.getvalue().decode("utf-8", errors="ignore")
        d = leer_log_xerase(raw)
        clave = d["serial_disco"] or lf.name
        logs_borrado[clave] = d
        guardar_json(ARCHIVO_LOGS, logs_borrado)
        msg = ""
        if d["ispf"]:
            idxs = st.session_state.df[st.session_state.df["Serial"] == limpiar_serial(d["ispf"])].index
            if len(idxs) > 0:
                i = idxs[0]
                if d["serial_disco"]:
                    st.session_state.df.at[i, "Serial Disco"] = d["serial_disco"]
                if d["modelo_disco"]:
                    st.session_state.df.at[i, "Modelo Disco"] = f"{d['fabricante_disco']} {d['modelo_disco']}".strip()
                if d["capacidad"]:
                    st.session_state.df.at[i, "Capacidad"] = d["capacidad"]
                st.session_state.df.at[i, "Log Borrado"] = (d["grade"] or "Procesado") + " | " + datetime.now().strftime("%Y-%m-%d")
                guardar_datos(st.session_state.df)
                msg = f" → Enlazado con la tabla vía ISPF ({d['ispf']})."
        with st.expander(f"📄 {lf.name} → Disco: {d['serial_disco'] or '—'} | {d['modelo_disco'] or '—'} | {d['capacidad'] or '—'} | {d['grade'] or '—'}"):
            st.code(raw[:800])
        st.success(f"✅ Log {lf.name} guardado en memoria.{msg}")

# ================= FASE 3: CERTIFICADO XERASE (PDF) =================
st.divider()
st.markdown("## 📜 FASE 3 — CERTIFICADO XERASE (Comparación final)")
st.caption("Sube el PDF. Compara cada disco y equipo contra tus logs (Fase 2) y contra tu TABLA CAPTURA.")

cert_files = st.file_uploader("Subir certificado Xerase (PDF)", type=["pdf"], accept_multiple_files=True, key="certs")
if cert_files:
    discos_cert = set()
    for cf in cert_files:
        if cf.name in st.session_state.get("certs_procesados", set()):
            continue
        st.session_state.setdefault("certs_procesados", set()).add(cf.name)
        filas = leer_certificado_xerase(cf.getvalue())
        if not filas:
            st.error(f"❌ {cf.name}: no se detectó la tabla del certificado.")
            continue
        ok_tabla = 0
        sin_tabla = []
        sin_log = []
        for fila in filas:
            serial_disco = fila.get("SERIALNUM", "")
            system_sn = limpiar_serial(fila.get("SYSTEM_SN", ""))
            system_model = fila.get("SYSTEM_MODEL", "")
            grade = fila.get("GRADE", "")
            status = fila.get("STATUS", "")
            cap_cert = fila.get("CAPACITY", "")
            discos_cert.add(serial_disco)
            log = logs_borrado.get(serial_disco, {})
            if not log:
                sin_log.append(serial_disco)
            aviso_cap = ""
            if log and log.get("capacidad") and cap_cert and log["capacidad"] != cap_cert:
                aviso_cap = f" ⚠️ Capacidad distinta: log {log['capacidad']} vs certificado {cap_cert}."
            if system_sn:
                idxs = st.session_state.df[st.session_state.df["Serial"] == system_sn].index
                if len(idxs) > 0:
                    i = idxs[0]
                    ok_tabla += 1
                    if system_model and str(st.session_state.df.at[i, "Modelo"]) in ("", "nan"):
                        st.session_state.df.at[i, "Modelo"] = system_model
                        t = detectar_tipo(system_model)
                        if t:
                            st.session_state.df.at[i, "Tipo"] = t
                    if serial_disco:
                        st.session_state.df.at[i, "Serial Disco"] = serial_disco
                    if cap_cert:
                        st.session_state.df.at[i, "Capacidad"] = cap_cert
                    if log.get("modelo_disco"):
                        st.session_state.df.at[i, "Modelo Disco"] = f"{log.get('fabricante_disco', '')} {log['modelo_disco']}".strip()
                    if log.get("grade"):
                        st.session_state.df.at[i, "Log Borrado"] = log["grade"]
                    st.session_state.df.at[i, "Cert Borrado"] = f"{status} {grade}".strip() + " | " + datetime.now().strftime("%Y-%m-%d")
                    if aviso_cap:
                        st.warning(f"⚠️ Equipo {system_sn}:{aviso_cap}")
                else:
                    sin_tabla.append(system_sn)
        guardar_datos(st.session_state.df)
        st.success(f"✅ {cf.name}: {len(filas)} discos leídos | {ok_tabla} equipos cotejados con tu tabla.")
        if sin_tabla:
            st.warning(f"⚠️ Equipos del certificado NO registrados en tu tabla: {', '.join(sorted(set(sin_tabla)))}")
        if sin_log:
            st.warning(f"⚠️ Discos en certificado SIN log de la Fase 2: {', '.join(sorted(set(sin_log)))}")
    discos_logs = {v["serial_disco"] for v in logs_borrado.values() if v.get("serial_disco")}
    sin_cert = discos_logs - discos_cert
    if discos_cert and sin_cert:
        st.warning(f"⚠️ Discos con log que NO aparecen en el certificado: {', '.join(sorted(sin_cert))}")
    elif discos_cert:
        st.success("✅ Todos los discos de tus logs aparecen en el certificado. Cotejo completo.")

# ================= REPORTE FINAL IMPRIMIBLE =================
import streamlit.components.v1 as components

st.markdown("""
<style>
@media print {
    section[data-testid="stSidebar"], .stButton, header, footer, #MainMenu {display:none !important;}
    body, .stApp, [data-testid="stAppViewContainer"] {background:#fff !important;}
    p, h1, h2, h3, span, div, td, th, label {color:#000 !important;}
}
</style>
""", unsafe_allow_html=True)

st.divider()
st.markdown("## 📊 REPORTE FINAL POR PO")
st.caption(f"PO activa: {st.session_state.po_actual or 'SIN PO'} | Estado actual del proyecto.")

r1, r2, r3, r4 = st.columns(4)
r1.metric("Equipos Totales", interno)
r2.metric("Equipos Cliente", cliente if cliente else "—")
r3.metric("Restantes", max(cliente - interno, 0) if cliente else "—")
r4.metric("Progreso", f"{progreso:.0%}")

r1, r2, r3, r4 = st.columns(4)
r1.metric("Coinciden", coinciden)
r2.metric("No coinciden", no_coinciden)
r3.metric("PC", pc_esc)
r4.metric("Laptops", lap_esc)

r1, r2, r3, r4 = st.columns(4)
r1.metric("Others", oth_esc)
r2.metric("PC Cliente", pc_cli if cliente else "—")
r3.metric("Laptops Cliente", lap_cli if cliente else "—")
r4.metric("Others Cliente", oth_cli if cliente else "—")

if len(df_final) > 0:
    st.markdown("**Desglose por marca:** " + " | ".join([f"{k}: {v}" for k, v in df_final["Marca"].value_counts().to_dict().items()]))
    st.markdown("**Desglose por PO:** " + " | ".join([f"{k}: {v}" for k, v in df_final["PO"].value_counts().to_dict().items()]))

if st.button("🖨️ Imprimir Reporte"):
    st.session_state["imprimir"] = True

if st.session_state.get("imprimir"):
    components.html("<script>window.parent.window.print();</script>", height=0, width=0)
    st.session_state["imprimir"] = False