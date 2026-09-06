import streamlit as st
import pandas as pd
import re
import requests
import io
import os
import time
import json
import base64
import zipfile
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

try:
    import plotly.express as px
    PLOTLY_OK = True
except Exception:
    PLOTLY_OK = False

# ================= CONFIGURACIÓN =================
st.set_page_config(page_title="Sistema de Inventario TCG/E-TEST", page_icon="🖥️", layout="wide")

ARCHIVO_DATOS = "registros.json"
ARCHIVO_CLIENTE = "cliente.json"
ARCHIVO_HISTORIAL = "historial.json"
ARCHIVO_POS_CLIENTE = "pos_cliente.json"
ARCHIVO_LOGS = "logs_borrado.json"
ARCHIVO_USUARIOS = "usuarios.json"
ARCHIVO_ALERTAS = "alertas.json"
ARCHIVO_POS_REG = "pos_registro.json"
ARCHIVO_CERRADAS = "po_cerradas.json"
COLUMNAS = ["Serial", "Costumer", "Marca", "Modelo", "Tipo", "Status", "PO", "Usuario", "Serial Disco", "Modelo Disco", "Capacidad", "Log Borrado", "Cert Borrado", "Fecha"]

USUARIOS_BASE = {
    "admin": "admin123",
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

def leer_certificado_por_posicion(doc):
    conocidos = {"DATE", "TIME", "SERIALNUM", "MODEL", "CAPACITY", "DEVICE", "TYPE",
                 "SYSTEM_MFG", "SYSTEM_MODEL", "SYSTEM_SN", "VER", "ERASURE_METHOD", "GRADE", "STATUS"}
    filas = []
    for pagina in doc:
        words = pagina.get_text("words")
        if not words:
            continue
        lineas = {}
        for w in words:
            clave = round(w[1] / 4)
            lineas.setdefault(clave, []).append(w)
        claves = sorted(lineas.keys())
        k_header = None
        for k in claves:
            ws = sorted(lineas[k], key=lambda w: w[0])
            junta = " ".join(w[4].upper() for w in ws)
            if "SERIALNUM" in junta and "SYSTEM_SN" in junta:
                k_header = k
                break
        if k_header is None:
            continue
        anclas = []
        for kk in [k_header - 1, k_header, k_header + 1]:
            for w in lineas.get(kk, []):
                t = w[4].upper()
                if t in conocidos:
                    anclas.append([w[0], t])
        anclas.sort(key=lambda a: a[0])
        nombres = []
        xs = []
        for x, n in anclas:
            if n == "TYPE":
                continue
            if n == "DEVICE":
                n = "DEVICE_TYPE"
            if n in nombres:
                n = n + "_2"
            nombres.append(n)
            xs.append(x)
        if not nombres:
            continue

        def col_de(x):
            idx = 0
            for i in range(len(xs)):
                if x >= xs[i] - 3:
                    idx = i
            while idx < len(xs) - 1:
                gap = xs[idx + 1] - xs[idx]
                slack = max(4.0, 0.2 * gap)
                if x >= xs[idx + 1] - slack:
                    idx += 1
                else:
                    break
            return nombres[idx]

        fila = None
        for k in claves:
            ws = sorted(lineas[k], key=lambda w: w[0])
            junta = " ".join(w[4] for w in ws)
            if junta.strip().lower().startswith("page"):
                continue
            es_fecha = bool(re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}', ws[0][4])) and col_de(ws[0][0]) == "DATE"
            if es_fecha:
                if fila:
                    filas.append(fila)
                fila = {n: "" for n in nombres}
            if fila is None:
                continue
            for w in ws:
                c = col_de(w[0])
                fila[c] = (fila[c] + " " + w[4]).strip()
        if fila:
            filas.append(fila)
    return filas

def leer_certificado_xerase(bytes_arch):
    doc = fitz.open(stream=bytes_arch, filetype="pdf")
    debug = {"texto": 0, "tablas": 0}
    for pagina in doc:
        debug["texto"] += len(pagina.get_text())
    for estrategia in ["lines", "text"]:
        encabezados = None
        filas = []
        for pagina in doc:
            tablas = pagina.find_tables(strategy=estrategia).tables
            debug["tablas"] += len(tablas)
            for t in tablas:
                for row in t.extract():
                    if not row or all(c is None for c in row):
                        continue
                    celdas = [str(c).replace("\n", " ").strip() if c else "" for c in row]
                    up = [c.upper() for c in celdas]
                    if "SERIALNUM" in up and "SYSTEM_SN" in up:
                        encabezados = up
                        continue
                    if encabezados and celdas and re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}', celdas[0]):
                        filas.append(dict(zip(encabezados, celdas)))
        if filas:
            return filas, f"estrategia={estrategia}"
    filas = leer_certificado_por_posicion(doc)
    if filas:
        return filas, "posicional"
    return [], f"texto={debug['texto']} tablas={debug['tablas']}"

def limpiar_filas_certificado(filas):
    limpias = []
    for f in filas:
        f = dict(f)
        fecha = re.sub(r'\s+', ' ', str(f.get("DATE", ""))).strip()
        m = re.match(r'^(\d{1,2}/\d{1,2}/(\d{2,4}))\s+(\d)$', fecha)
        if m and len(m.group(2)) < 4:
            partes = m.group(1).split("/")
            partes[2] = partes[2] + m.group(3)
            fecha = "/".join(partes)
        m2 = re.match(r'^(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*(.*)$', fecha)
        if m2:
            f["DATE"] = m2.group(1)
            tiempo = m2.group(2)
            extra = m2.group(3).strip()
            if extra:
                tiempo = f"{tiempo} {extra}"
            f["TIME"] = tiempo
        else:
            f["DATE"] = fecha
        ver = str(f.get("VER", ""))
        m3 = re.match(r'^(v[\d.]+[a-z]?)\s*(\S.*)$', ver)
        if m3:
            f["VER"] = m3.group(1)
            resto = m3.group(2).strip()
            if resto:
                f["ERASURE_METHOD"] = (resto + " " + str(f.get("ERASURE_METHOD", ""))).strip()
        modelo = str(f.get("MODEL", ""))
        m4 = re.search(r'(\d+\s?(?:GB|TB))\s*(\d+\s?(?:GB|TB))$', modelo, re.IGNORECASE)
        if m4 and not str(f.get("CAPACITY", "")).strip():
            f["MODEL"] = modelo[:m4.start(2)].strip()
            f["CAPACITY"] = m4.group(2).upper().replace(" ", "")
        cap = str(f.get("CAPACITY", ""))
        dev = str(f.get("DEVICE_TYPE", ""))
        tiene_size = lambda t: bool(re.search(r'\d\s?(GB|TB|MB)', t, re.IGNORECASE))
        if cap and not tiene_size(cap):
            if tiene_size(dev):
                f["CAPACITY"], f["DEVICE_TYPE"] = dev, cap
            else:
                m5 = re.search(r'(\d+\s?(?:GB|TB))$', modelo, re.IGNORECASE)
                f["DEVICE_TYPE"] = cap
                if m5:
                    f["CAPACITY"] = m5.group(1).upper().replace(" ", "")
                    f["MODEL"] = modelo[:m5.start()].strip()
                else:
                    f["CAPACITY"] = ""
        for k in f:
            if isinstance(f[k], str):
                f[k] = re.sub(r'\s+', ' ', f[k]).strip()
        limpias.append(f)
    return limpias

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

def get_usuarios():
    u = dict(USUARIOS_BASE)
    u.update(cargar_json(ARCHIVO_USUARIOS))
    return u

def agregar_alerta(serial, usuario, po_intento, po_correcta, tipo):
    alertas = cargar_json(ARCHIVO_ALERTAS)
    if not isinstance(alertas, list):
        alertas = []
    alertas.append({
        "id": f"{serial}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "usuario": usuario,
        "serial": serial,
        "po_intento": po_intento,
        "po_correcta": po_correcta,
        "tipo": tipo,
        "leidos": [],
    })
    guardar_json(ARCHIVO_ALERTAS, alertas)

def registrar_po(nombre, usuario, con_base=False, declarados=0):
    reg = cargar_json(ARCHIVO_POS_REG)
    if not isinstance(reg, dict):
        reg = {}
    if nombre and nombre not in reg:
        reg[nombre] = {
            "inicio": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "usuario": usuario,
            "con_base": con_base,
            "declarados": declarados,
            "estado": "ABIERTA",
            "asignada_a": "",
            "tomada_por": "",
        }
        guardar_json(ARCHIVO_POS_REG, reg)
    return reg

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

USUARIOS = get_usuarios()

if not st.session_state.logged_in:
    tk = st.query_params.get("tk")
    if tk:
        tk = str(tk).strip().lower()
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
        st.markdown("<div class='sub'>Sistema de Inventario TCG/E-TEST — Inicia sesión</div>", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        usuario = st.text_input("👤 Usuario")
        password = st.text_input("🔑 Contraseña", type="password")

        if st.button("🔓 Entrar al sistema", use_container_width=True):
            usuario_login = usuario.strip().lower()

            if usuario_login in USUARIOS and USUARIOS[usuario_login] == password:
                st.session_state.logged_in = True
                st.session_state.usuario = usuario_login
                st.query_params["tk"] = usuario_login
                st.rerun()
            else:
                st.error("❌ Usuario o contraseña incorrectos")
    st.stop()

# ================= RECARGA MULTIUSUARIO (disco en vivo) =================
st.session_state.df = cargar_datos()
if os.path.exists(ARCHIVO_CLIENTE):
    try:
        st.session_state.df_cliente = pd.read_json(ARCHIVO_CLIENTE, orient="records", dtype=str)
    except Exception:
        st.session_state.df_cliente = None
else:
    st.session_state.df_cliente = None

historial = cargar_json(ARCHIVO_HISTORIAL)
pos_cliente = cargar_json(ARCHIVO_POS_CLIENTE)
pos_reg = cargar_json(ARCHIVO_POS_REG)
if not isinstance(pos_reg, dict):
    pos_reg = {}

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

es_admin = st.session_state.usuario == "admin"

# ================= BARRA LATERAL =================
st.sidebar.title("⚙️ Menú")
st.sidebar.markdown(f"👤 **Usuario:** {st.session_state.usuario.upper()}")
if st.sidebar.button("🚪 Cerrar sesión"):
    st.session_state.logged_in = False
    st.query_params.clear()
    st.rerun()
st.sidebar.divider()

pos_abiertas_reg = sorted([p for p in pos_reg.keys() if pos_reg[p].get("estado", "ABIERTA") == "ABIERTA"])
pos_sin_reg = sorted([p for p in set(st.session_state.df["PO"]) if p and p != "SIN_PO" and p not in pos_reg])

if es_admin:
    opciones = ["(selecciona una PO)"] + pos_abiertas_reg + pos_sin_reg + ["➕ CREAR PO NUEVA..."]
else:
    opciones = ["(selecciona una PO)"] + pos_abiertas_reg + pos_sin_reg
if not opciones:
    opciones = ["(sin POs activas)"]

idx_po = 0
if st.session_state.po_actual in opciones:
    idx_po = opciones.index(st.session_state.po_actual)
po_sel = st.sidebar.selectbox("🏷️ PO / Proyecto activo", opciones, index=idx_po)

if po_sel == "➕ CREAR PO NUEVA...":
    nombre_nuevo = st.sidebar.text_input("Nombre de la PO nueva", placeholder="Ej: PO_SORIANA_2026")
    asignar_a = st.sidebar.selectbox("🎯 Asignar a (opcional)", ["— sin asignar —"] + sorted(get_usuarios().keys()))
    if st.sidebar.button("📝 Iniciar PO"):
        nn = nombre_nuevo.strip().upper().replace(" ", "_")
        if nn:
            reg_n = registrar_po(nn, st.session_state.usuario)
            if asignar_a != "— sin asignar —":
                reg_n[nn]["asignada_a"] = asignar_a
                guardar_json(ARCHIVO_POS_REG, reg_n)
            st.session_state.po_actual = ""
            st.sidebar.success(f"PO '{nn}' creada ✅ — aún SIN TOMAR")
            st.rerun()
        else:
            st.sidebar.error("Escribe un nombre para la PO.")
    st.session_state.po_actual = ""
elif po_sel in ("(selecciona una PO)", "(sin POs activas)"):
    st.session_state.po_actual = ""
    st.sidebar.info("Selecciona una PO para trabajar. La captura está deshabilitada hasta que tomes una.")
else:
    info_sel = pos_reg.get(po_sel, {})
    asignada = info_sel.get("asignada_a", "")
    if asignada and st.session_state.usuario != asignada and not es_admin:
        st.session_state.po_actual = ""
        st.sidebar.error(f"🔒 La PO {po_sel} está asignada a '{asignada}'. Solo él/ella o el admin pueden tomarla.")
    else:
        st.session_state.po_actual = po_sel
        reg_t = cargar_json(ARCHIVO_POS_REG)
        if not isinstance(reg_t, dict):
            reg_t = {}
        if po_sel in reg_t and not reg_t[po_sel].get("tomada_por"):
            reg_t[po_sel]["tomada_por"] = st.session_state.usuario
            reg_t[po_sel]["tomada_fecha"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            guardar_json(ARCHIVO_POS_REG, reg_t)
            pos_reg = reg_t
            st.sidebar.success(f"🎯 Tomaste la PO {po_sel}")
        if po_sel in pos_sin_reg and es_admin and st.sidebar.button("📝 Registrar como iniciada", key="btn_reg_det"):
            registrar_po(po_sel, st.session_state.usuario)
            st.rerun()
        info_po = pos_reg.get(po_sel, {})
        if info_po:
            st.sidebar.caption(f"📅 Iniciada: {info_po.get('inicio', '—')} por {info_po.get('usuario', '—')}")
            st.sidebar.caption(f"📦 Base cliente: {'SÍ (' + str(info_po.get('declarados', 0)) + ' declarados)' if info_po.get('con_base') else 'NO (desde cero)'}")
            if info_po.get("asignada_a"):
                st.sidebar.caption(f"🔒 Asignada a: {info_po['asignada_a']}")
            if info_po.get("tomada_por"):
                st.sidebar.caption(f"🎯 Tomada por: {info_po['tomada_por']} ({info_po.get('tomada_fecha', '')})")

pos_existentes = sorted(set(st.session_state.df["PO"])) if len(st.session_state.df) else []
if es_admin:
    po_vista = st.sidebar.selectbox("🔎 PO a visualizar", ["TODAS"] + pos_existentes, index=0)
else:
    po_vista = st.session_state.po_actual
    st.sidebar.caption(f"🔎 Tu vista: PO {po_vista or 'personal'}")

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
        reg_tmp = registrar_po(nombre_po, st.session_state.usuario, con_base=True, declarados=len(dfc_nueva))
        reg_tmp[nombre_po]["con_base"] = True
        reg_tmp[nombre_po]["declarados"] = len(dfc_nueva)
        guardar_json(ARCHIVO_POS_REG, reg_tmp)
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
    for f in [ARCHIVO_CLIENTE, ARCHIVO_HISTORIAL, ARCHIVO_POS_CLIENTE, ARCHIVO_LOGS, ARCHIVO_ALERTAS, ARCHIVO_POS_REG]:
        if os.path.exists(f):
            os.remove(f)
    st.sidebar.info("Sistema reiniciado por completo")

# ================= ENCABEZADO =================
ca, cb = st.columns([1, 5])
with ca:
    mostrar_logo()
with cb:
    st.title("Sistema de Inventario TCG/E-TEST")
    st.success(f"👋 ¡Bienvenido, {st.session_state.usuario.upper()}! | 🏷️ PO activa: {st.session_state.po_actual or 'SIN PO'} | 🔎 Vista: {po_vista}")

# ================= ALERTAS DE EQUIPOS REVUELTOS =================
alertas = cargar_json(ARCHIVO_ALERTAS)
if not isinstance(alertas, list):
    alertas = []
pendientes = [a for a in alertas if st.session_state.usuario not in a.get("leidos", [])]
if pendientes:
    st.divider()
    st.markdown("## 🚨 ALERTAS DE EQUIPOS REVUELTOS")
    st.caption("Aviso para todo el equipo: eviten registrar seriales de POs que no les corresponden.")
    for a in pendientes[-10:]:
        if a["tipo"] == "DUPLICADO_OTRA_PO":
            msg = (f"**{a['usuario']}** intentó registrar el serial **{a['serial']}** en la PO '{a['po_intento']}', "
                   f"pero YA estaba registrado en la PO '**{a['po_correcta']}**' ({a['fecha']}).")
        else:
            msg = (f"**{a['usuario']}** registró el serial **{a['serial']}** en la PO '{a['po_intento']}', "
                   f"pero ese serial aparece en la PO del cliente '**{a['po_correcta']}**' ({a['fecha']}).")
        st.warning(f"⚠️ {msg}")
        if st.button(f"✅ Entendido ({a['serial']})", key=f"ok_{a['id']}"):
            for aa in alertas:
                if aa["id"] == a["id"]:
                    aa.setdefault("leidos", []).append(st.session_state.usuario)
            guardar_json(ARCHIVO_ALERTAS, alertas)
            st.rerun()

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

# ================= VISTA SEGÚN ROL Y PO SELECCIONADA =================
if es_admin:
    if po_vista == "TODAS":
        df_vista = df
    else:
        df_vista = df[df["PO"] == po_vista]
else:
    if st.session_state.po_actual:
        df_vista = df[df["PO"] == st.session_state.po_actual]
    else:
        df_vista = df[df["Usuario"] == st.session_state.usuario]

# ================= CÁLCULOS =================
interno = len(df_vista)
if po_vista == "TODAS":
    cliente = sum(len(v) for v in pos_cliente.values())
elif po_vista:
    cliente = len(pos_cliente.get(po_vista, []))
else:
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
    coinciden = int(df_vista["Serial"].isin(set_cliente).sum())
    no_coinciden = interno - coinciden
else:
    coinciden = "—"
    no_coinciden = "—"

tipos_esc = df_vista["Tipo"].map(norm_tipo)
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

# ================= REGISTRO DE POs =================
st.divider()
st.markdown("## 📋 REGISTRO DE POs (estado de proyectos)")
st.caption("Libro de proyectos: qué POs están iniciadas, quién las inició, a quién están asignadas, quién las tomó, si tienen base y su avance.")
rows_reg = []
for po in sorted(set(list(pos_reg.keys()) + list(set(df["PO"])))):
    if not po or po == "SIN_PO":
        continue
    info = pos_reg.get(po, {})
    capt = len(df[df["PO"] == po])
    decl = info.get("declarados", 0) or len(pos_cliente.get(po, []))
    rows_reg.append({
        "PO": po,
        "Iniciada": info.get("inicio", "sin registrar"),
        "Por": info.get("usuario", "—"),
        "Asignada a": info.get("asignada_a") or "— libre —",
        "Tomada por": info.get("tomada_por") or "— nadie aún —",
        "Base cliente": f"CON BASE ({decl})" if (info.get("con_base") or po in pos_cliente) else "SIN BASE (desde cero)",
        "Capturados": capt,
        "Avance": f"{capt / decl:.0%}" if decl else "—",
        "Estado": info.get("estado", "ABIERTA"),
    })
if rows_reg:
    st.dataframe(pd.DataFrame(rows_reg), use_container_width=True, hide_index=True)
else:
    st.info("Aún no hay POs registradas. El admin puede crear una en la barra lateral.")

# ================= ESTADÍSTICAS Y GRÁFICAS =================
st.divider()
st.markdown("## 📈 ESTADÍSTICAS Y GRÁFICAS")
st.caption(f"Vista actual: **{po_vista}** | Todo lo de abajo se filtra según la PO seleccionada.")
if not PLOTLY_OK:
    st.warning("Falta plotly para las gráficas. Ejecuta: pip install plotly")
elif len(df_vista) == 0:
    st.info("Sin datos para graficar en esta vista.")
else:
    dfv = df_vista.copy()
    dfv["Dia"] = dfv["Fecha"].str[:10]
    dias = dfv["Dia"].nunique()
    promedio = len(dfv) / dias if dias else 0
    por_dia = dfv.groupby("Dia").size().reset_index(name="Equipos")
    mejor = por_dia.loc[por_dia["Equipos"].idxmax()] if len(por_dia) else None
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Días activos", dias)
    g2.metric("Promedio por día", f"{promedio:.1f}")
    g3.metric("Mejor día", mejor["Dia"] if mejor is not None else "—")
    g4.metric("Equipos ese día", int(mejor["Equipos"]) if mejor is not None else 0)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Capturas por día**")
        fig = px.bar(por_dia, x="Dia", y="Equipos", color="Equipos", color_continuous_scale="Blues")
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.markdown("**Avance acumulado**")
        acum = dfv.groupby("Dia").size().cumsum().reset_index(name="Acumulado")
        fig2 = px.line(acum, x="Dia", y="Acumulado", markers=True)
        fig2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Por marca**")
        tmp = dfv["Marca"].value_counts().reset_index()
        tmp.columns = ["Marca", "Equipos"]
        fig3 = px.pie(tmp, names="Marca", values="Equipos", hole=0.55)
        fig3.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig3, use_container_width=True)
    with c2:
        st.markdown("**Por tipo de equipo**")
        tmp2 = dfv["Tipo"].replace("", "SIN TIPO").value_counts().reset_index()
        tmp2.columns = ["Tipo", "Equipos"]
        fig4 = px.bar(tmp2, x="Tipo", y="Equipos", color="Tipo")
        fig4.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(fig4, use_container_width=True)
    with c3:
        st.markdown("**Status de captura**")
        tmp3 = dfv["Status"].value_counts().reset_index()
        tmp3.columns = ["Status", "Equipos"]
        fig5 = px.pie(tmp3, names="Status", values="Equipos", hole=0.55)
        fig5.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig5, use_container_width=True)

    st.markdown("**Avance por PO (procesados vs declarados)**")
    rows_po = []
    for po in sorted(set(list(dfv["PO"]) + list(pos_cliente.keys()))):
        if po in ("", "SIN_PO"):
            continue
        rows_po.append({
            "PO": po,
            "Procesados": len(dfv[dfv["PO"] == po]),
            "Declarados": len(pos_cliente.get(po, [])),
        })
    if rows_po:
        fig6 = px.bar(pd.DataFrame(rows_po), x="PO", y=["Procesados", "Declarados"], barmode="group",
                      color_discrete_sequence=["#2ca02c", "#7f7f7f"])
        fig6.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig6, use_container_width=True)

    if es_admin:
        st.markdown("**Rendimiento por usuario**")
        tmp4 = dfv["Usuario"].value_counts().reset_index()
        tmp4.columns = ["Usuario", "Equipos"]
        fig7 = px.bar(tmp4, x="Usuario", y="Equipos", color="Usuario")
        fig7.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(fig7, use_container_width=True)

# ================= SUPERVISIÓN + USUARIOS (SOLO ADMIN) =================
if es_admin:
    st.divider()
    st.markdown("## 🕵️ SUPERVISIÓN DE EQUIPO (solo admin)")
    if len(df) == 0:
        st.info("Aún no hay capturas registradas.")
    else:
        rows = []
        for u in sorted(df["Usuario"].unique()):
            du = df[df["Usuario"] == u]
            pos_u = ", ".join(sorted(set(du["PO"])))
            pcts = []
            for po in sorted(set(du["PO"])):
                decl = len(pos_cliente.get(po, [])) if po in pos_cliente else 0
                n_po = len(du[du["PO"] == po])
                if decl:
                    pcts.append(f"{po}: {n_po / decl:.0%}")
                else:
                    pcts.append(f"{po}: {n_po} eq.")
            rows.append({
                "Usuario": u,
                "PO(s) asignada(s)": pos_u,
                "Equipos registrados": len(du),
                "Avance por PO": ", ".join(pcts),
                "Último registro": du["Fecha"].max(),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.markdown("**Avance global por PO:**")
        rows2 = []
        for po in sorted(set(df["PO"])):
            decl = len(pos_cliente.get(po, [])) if po in pos_cliente else 0
            tot = len(df[df["PO"] == po])
            usuarios_po = ", ".join(sorted(set(df[df["PO"] == po]["Usuario"])))
            rows2.append({
                "PO": po,
                "Declarados cliente": decl if decl else "—",
                "Procesados": tot,
                "Avance": f"{tot / decl:.0%}" if decl else "—",
                "Usuarios trabajando": usuarios_po,
            })
        st.dataframe(pd.DataFrame(rows2), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("## 👥 ADMINISTRACIÓN DE USUARIOS (solo admin)")
    usuarios_locales = cargar_json(ARCHIVO_USUARIOS)
    usuarios_totales = get_usuarios()
    cu1, cu2 = st.columns(2)
    with cu1:
        st.markdown("**Usuarios base (del código):**")
        for u in USUARIOS_BASE:
            if u == "admin":
                st.write(f"👑 {u} — protegido (dueño del sistema)")
        st.markdown("**Usuarios creados por admin:**")
        if usuarios_locales:
            for u in list(usuarios_locales.keys()):
                cc1, cc2 = st.columns([3, 1])
                cc1.write(f"👤 {u}")
                if cc2.button("🗑️", key=f"eliminar_usuario_{u}"):
                    usuarios_locales.pop(u, None)
                    guardar_json(ARCHIVO_USUARIOS, usuarios_locales)
                    st.success(f"Usuario {u} eliminado ✅")
                    st.rerun()
        else:
            st.info("Aún no hay usuarios creados.")
    with cu2:
        with st.form("form_crear_usuario"):
            nuevo_u = st.text_input("Nuevo usuario")
            nuevo_p = st.text_input("Contraseña", type="password")
            crear = st.form_submit_button("➕ Crear usuario")
        if crear:
            nuevo_u = nuevo_u.strip().lower()
            nuevo_p = nuevo_p.strip()
            if not nuevo_u or not nuevo_p:
                st.error("Escribe usuario y contraseña.")
            elif nuevo_u in usuarios_totales:
                st.error(f"El usuario '{nuevo_u}' ya existe.")
            elif not re.match(r'^[a-z0-9_.-]{3,20}$', nuevo_u):
                st.error("Usuario: 3-20 caracteres (letras, números, punto, guion).")
            else:
                usuarios_locales[nuevo_u] = nuevo_p
                guardar_json(ARCHIVO_USUARIOS, usuarios_locales)
                st.success(f"Usuario '{nuevo_u}' creado ✅")
                st.rerun()

    st.markdown("### 🔑 Cambiar contraseña (solo admin)")
    with st.form("form_cambiar_pass"):
        lista_us = sorted(get_usuarios().keys())
        u_sel = st.selectbox("Usuario a modificar", lista_us)
        nueva_pass = st.text_input("Nueva contraseña", type="password")
        cambiar = st.form_submit_button("🔑 Cambiar contraseña")
    if cambiar:
        if not nueva_pass.strip():
            st.error("Escribe una contraseña nueva.")
        elif len(nueva_pass.strip()) < 4:
            st.error("La contraseña debe tener al menos 4 caracteres.")
        else:
            datos = cargar_json(ARCHIVO_USUARIOS)
            if not isinstance(datos, dict):
                datos = {}
            datos[u_sel] = nueva_pass.strip()
            guardar_json(ARCHIVO_USUARIOS, datos)
            st.success(f"Contraseña de '{u_sel}' actualizada ✅")
            st.rerun()

    st.markdown("### 🎯 Asignar PO a usuario (cuando quieras)")
    with st.form("form_asignar_po"):
        pos_asig = sorted([p for p in pos_reg.keys() if pos_reg[p].get("estado", "ABIERTA") == "ABIERTA"])
        if pos_asig:
            po_a = st.selectbox("PO", pos_asig)
            u_a = st.selectbox("Asignar a", ["— sin asignar —"] + sorted(get_usuarios().keys()))
            asignar = st.form_submit_button("🎯 Asignar")
            if asignar:
                reg_a = cargar_json(ARCHIVO_POS_REG)
                if isinstance(reg_a, dict) and po_a in reg_a:
                    reg_a[po_a]["asignada_a"] = "" if u_a == "— sin asignar —" else u_a
                    guardar_json(ARCHIVO_POS_REG, reg_a)
                    st.success(f"PO {po_a} asignada a {u_a} ✅")
                    st.rerun()
        else:
            st.info("No hay POs abiertas para asignar.")

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
        if not st.session_state.po_actual:
            st.error("🏷️ Primero selecciona/toma una PO en la barra lateral.")
        elif not longitud_ok(serial):
            st.error(f"⚠️ LONGITUD INCORRECTA: {len(serial)} caracteres. Debe ser entre 5 y 20.")
        elif serial in existentes:
            fila = df[df["Serial"] == serial].iloc[0]
            po_previa = fila["PO"] or "SIN PO"
            if po_previa != (st.session_state.po_actual or "SIN PO"):
                st.error(f"🚨 TRAZABILIDAD: Este serial YA fue registrado en la PO '{po_previa}' por {fila['Usuario'] or 'alguien'} el {fila['Fecha']}. Revisa si está en la PO equivocada.")
                agregar_alerta(serial, st.session_state.usuario, st.session_state.po_actual or "SIN_PO", po_previa, "DUPLICADO_OTRA_PO")
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
            if po_nueva != "SIN_PO":
                registrar_po(po_nueva, st.session_state.usuario)
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
                agregar_alerta(serial, st.session_state.usuario, po_nueva, mapa_pos_cliente[serial], "REGISTRADO_PO_AJENA")
            elif status_nuevo == "No está en PO":
                st.warning(f"⚠️ {serial} registrado en '{po_nueva}', pero NO está en la PO del cliente.")
            else:
                st.success(f"✅ {serial} registrado en PO '{po_nueva}' ({detectar_marca(serial)})")
            st.rerun()

with tab2:
    with st.form("form_masiva"):
        texto = st.text_area("Pega los seriales aquí (uno por línea)")
        enviar2 = st.form_submit_button("✅ Registrar todos")
    if enviar2 and texto and not st.session_state.po_actual:
        st.error("🏷️ Primero selecciona/toma una PO en la barra lateral.")
    elif enviar2 and texto:
        existentes = set(df["Serial"])
        nuevas = []
        ok = dup = mal = 0
        po_nueva = st.session_state.po_actual or "SIN_PO"
        if po_nueva != "SIN_PO":
            registrar_po(po_nueva, st.session_state.usuario)
        for linea in texto.splitlines():
            serial = limpiar_serial(linea)
            if not serial:
                continue
            if not longitud_ok(serial):
                mal += 1
                continue
            if serial in existentes:
                fila = df[df["Serial"] == serial].iloc[0]
                if (fila["PO"] or "SIN_PO") != po_nueva:
                    agregar_alerta(serial, st.session_state.usuario, po_nueva, fila["PO"] or "SIN_PO", "DUPLICADO_OTRA_PO")
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

df_final = df_vista
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as w:
    df_final.to_excel(w, index=False, sheet_name="TABLA_CAPTURA")
bc.download_button("⬇️ Descargar Excel", buffer.getvalue(),
                   file_name=f"inventario_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx")

# ================= TABLA CAPTURA =================
st.markdown("## 📋 TABLA CAPTURA")
if es_admin:
    st.caption("Edita Modelo, Tipo, Costumer o PO directamente aquí si necesitas corregir algo.")
    df_editado = st.data_editor(st.session_state.df, use_container_width=True, hide_index=True, num_rows="dynamic")
    if st.button("💾 Guardar cambios de la tabla"):
        st.session_state.df = df_editado.fillna("")
        guardar_datos(st.session_state.df)
        st.success("Cambios guardados ✅")
        st.rerun()
else:
    st.caption(f"Viendo únicamente tu PO: **{st.session_state.po_actual or 'tu captura'}** (solo lectura; captura en las pestañas de arriba).")
    st.dataframe(df_vista, use_container_width=True, hide_index=True)

# ================= FASE 2: LOGS XERASE =================
st.divider()
st.markdown("## 💾 FASE 2 — LOGS DE BORRADO XERASE")
st.caption("Sube la CARPETA COMPLETA de borrados: selecciona todos los archivos (Ctrl+A) o sube el ZIP. El sistema detecta solo los logs XErase reales y descarta .xml, .pdf y demás.")

logs_borrado = cargar_json(ARCHIVO_LOGS)

log_files = st.file_uploader("Subir carpeta de borrado (ZIP o archivos sueltos)", type=None, accept_multiple_files=True, key="logs")
if log_files:
    candidatos = []
    for lf in log_files:
        data = lf.getvalue()
        if lf.name.lower().endswith(".zip"):
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    candidatos.append((info.filename, z.read(info)))
            except Exception:
                continue
        else:
            candidatos.append((lf.name, data))
    procesados = 0
    ignorados = 0
    rows_log = []
    for nombre, data in candidatos:
        if nombre in st.session_state.get("logs_procesados", set()):
            continue
        st.session_state.setdefault("logs_procesados", set()).add(nombre)
        raw = data.decode("utf-8", errors="ignore")
        if not re.search(r'XERAS|Erasure Results|Serial Number\s*:', raw, re.IGNORECASE):
            ignorados += 1
            continue
        d = leer_log_xerase(raw)
        clave = d["serial_disco"] or nombre
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
                msg = f"Enlazado vía ISPF ({d['ispf']})"
        rows_log.append({
            "Archivo": nombre,
            "Serial disco": d["serial_disco"],
            "Modelo disco": f"{d['fabricante_disco']} {d['modelo_disco']}".strip(),
            "Capacidad": d["capacidad"],
            "Grado": d["grade"],
            "ISPF": d["ispf"],
            "Resultado": d["resultado"],
            "Nota": msg,
        })
        procesados += 1
    if procesados or ignorados:
        st.success(f"✅ {procesados} logs XErase procesados | 🗑️ {ignorados} archivos ignorados (xml, pdf, etc.)")
    if rows_log:
        with st.expander("Ver resumen de logs procesados", expanded=True):
            st.dataframe(pd.DataFrame(rows_log), use_container_width=True, hide_index=True)

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
        filas, debug = leer_certificado_xerase(cf.getvalue())
        if not filas:
            st.error(f"❌ {cf.name}: no se detectó la tabla del certificado. (debug: {debug})")
            continue
        with st.expander("🔎 Ver filas leídas del certificado"):
            st.dataframe(pd.DataFrame(filas), use_container_width=True)
        filas = limpiar_filas_certificado(filas)
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

# ================= CONVERSOR DE CERTIFICADOS (PDF → EXCEL) =================
st.divider()
st.markdown("## 🔄 CONVERSOR DE CERTIFICADOS (PDF → EXCEL)")
st.caption("Sube uno o VARIOS certificados XErase (mismo formato). Si algo vino pegado (fecha+hora), se separa solo. Y puedes editar cualquier celda aquí mismo antes de descargar.")

conv_files = st.file_uploader("Subir certificados para convertir", type=["pdf"], accept_multiple_files=True, key="conv")
if conv_files:
    for cf in conv_files:
        filas, debug = leer_certificado_xerase(cf.getvalue())
        if not filas:
            st.error(f"❌ {cf.name}: no se detectó la tabla. (debug: {debug})")
            continue
        filas_limpias = limpiar_filas_certificado(filas)
        st.markdown(f"### 📄 {cf.name} — {len(filas_limpias)} discos")
        df_edit = st.data_editor(pd.DataFrame(filas_limpias), key=f"ed_{cf.name}", use_container_width=True, hide_index=True, num_rows="dynamic")
        buf = io.BytesIO()
        pd.DataFrame(df_edit).to_excel(buf, index=False, engine="openpyxl")
        st.download_button(f"⬇️ Descargar Excel de {cf.name}", data=buf.getvalue(),
                           file_name=f"{os.path.splitext(cf.name)[0]}_convertido.xlsx", key=f"dl_{cf.name}")

# ================= CIERRE Y ARCHIVO DE POs (SOLO ADMIN) =================
if es_admin:
    st.divider()
    st.markdown("## 🗄️ CIERRE DE PO (archivar y revisar después)")
    cerradas = cargar_json(ARCHIVO_CERRADAS)
    if not isinstance(cerradas, dict):
        cerradas = {}
    pos_abiertas = sorted(set(df["PO"])) if len(df) else []
    pos_abiertas = [p for p in pos_abiertas if p and p != "SIN_PO"]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 🔒 Cerrar una PO")
        if pos_abiertas:
            po_cerrar = st.selectbox("PO a cerrar", pos_abiertas, key="po_cerrar")
            n_filas = len(df[df["PO"] == po_cerrar])
            st.caption(f"Se archivarán {n_filas} equipos de esta PO y desaparecerán de la tabla activa.")
            if st.button("🔒 Cerrar PO y archivar", key="btn_cerrar"):
                filas_po = df[df["PO"] == po_cerrar]
                decl = len(pos_cliente.get(po_cerrar, []))
                cerradas[po_cerrar] = {
                    "fecha_cierre": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "usuario": st.session_state.usuario,
                    "metricas": {
                        "equipos": len(filas_po),
                        "declarados": decl,
                        "avance": f"{len(filas_po) / decl:.0%}" if decl else "—",
                        "coinciden": int(filas_po["Serial"].isin(set_cliente).sum()) if set_cliente else "—",
                    },
                    "filas": filas_po.to_dict(orient="records"),
                }
                guardar_json(ARCHIVO_CERRADAS, cerradas)
                reg_c = cargar_json(ARCHIVO_POS_REG)
                if isinstance(reg_c, dict) and po_cerrar in reg_c:
                    reg_c[po_cerrar]["estado"] = "CERRADA"
                    guardar_json(ARCHIVO_POS_REG, reg_c)
                st.session_state.df = df[df["PO"] != po_cerrar].reset_index(drop=True)
                guardar_datos(st.session_state.df)
                st.success(f"PO '{po_cerrar}' cerrada y archivada ✅")
                st.rerun()
        else:
            st.info("No hay POs abiertas con equipos registrados.")
    with c2:
        st.markdown("### 👁️ Revisar POs cerradas")
        if cerradas:
            po_ver = st.selectbox("PO cerrada a revisar", sorted(cerradas.keys()), key="po_ver")
            info = cerradas[po_ver]
            st.caption(f"Cerrada el {info['fecha_cierre']} por {info['usuario']}")
            m = info["metricas"]
            st.write(f"**Equipos:** {m['equipos']} | **Declarados:** {m['declarados']} | **Avance:** {m['avance']} | **Coinciden:** {m['coinciden']}")
            dfc_err = pd.DataFrame(info["filas"])
            st.dataframe(dfc_err, use_container_width=True, hide_index=True)
            bufc = io.BytesIO()
            with pd.ExcelWriter(bufc, engine="openpyxl") as w:
                dfc_err.to_excel(w, index=False, sheet_name=po_ver[:28])
            st.download_button(f"⬇️ Excel de la PO cerrada {po_ver}", data=bufc.getvalue(),
                               file_name=f"PO_CERRADA_{po_ver}.xlsx", key=f"dl_cerrada_{po_ver}")
        else:
            st.info("Aún no hay POs cerradas.")

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
st.caption(f"Sistema de Inventario TCG/E-TEST | PO activa: {st.session_state.po_actual or 'SIN PO'} | Vista: {po_vista}")

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