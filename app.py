import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import imaplib
import email
from email.header import decode_header
import zipfile
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SQRapp | Robot Administrativo", layout="wide", page_icon="🧠")

# --- ESTILOS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stButton>button { width: 100%; border-radius: 8px; font-weight: bold; }
    .manual-box { border: 1px solid #4CAF50; padding: 15px; border-radius: 10px; margin-bottom: 20px; }
    </style>
""", unsafe_allow_html=True)

# --- CONEXIÓN GOOGLE SHEETS ---
def get_client():
    try:
        secrets = st.secrets["gcp_service_account"]
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(secrets, scopes=scopes)
        return gspread.authorize(creds)
    except: return None

def load_data():
    client = get_client()
    if not client: return None, None, None, None
    try:
        sh = client.open("APP_SQR")
        def get_df(name, cols):
            try:
                ws = sh.worksheet(name)
                df = pd.DataFrame(ws.get_all_records())
                for c in cols: 
                    if c not in df.columns: df[c] = 0 if 'Valor' in c or 'IVA' in c or 'Total' in c else ""
                for c in df.columns:
                    if any(x in c for x in ['Valor', 'Total', 'IVA', 'Monto', 'Pagado', 'Saldo']):
                        df[c] = pd.to_numeric(df[c].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)
                return df
            except: return pd.DataFrame(columns=cols)

        df_p = get_df("proyectos", ['ID', 'Cliente', 'Proyecto', 'Total Venta', 'IVA Generado', 'Pagado Cliente', 'Saldo Pendiente', 'Estado'])
        df_g = get_df("gastos", ['Fecha', 'Proveedor', 'Concepto', 'Proyecto Asignado', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria', 'Origen'])
        df_n = get_df("nomina", ['Fecha', 'Especialista', 'Rol', 'Proyecto', 'Valor Pactado', 'Pagado', 'Saldo Debe'])
        return df_p, df_g, df_n, sh
    except Exception as e:
        st.error(f"Error DB: {e}")
        return None, None, None, None

# --- LÓGICA XML ---
def extract_xml_data(xml_content):
    try:
        tree = ET.ElementTree(ET.fromstring(xml_content))
        root = tree.getroot()
        ns = {'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
              'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'}
        if "Invoice" not in root.tag: return None, 0, 0, 0, None
        
        provider = root.find('.//cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:RegistrationName', ns)
        prov_name = provider.text if provider is not None else "Prov. Desconocido"
        inv_id = root.find('.//cbc:ID', ns)
        factura_ref = inv_id.text if inv_id is not None else "S/N"
        tax_total = root.find('.//cac:TaxTotal/cbc:TaxAmount', ns)
        iva = float(tax_total.text) if tax_total is not None else 0.0
        legal_total = root.find('.//cac:LegalMonetaryTotal/cbc:PayableAmount', ns)
        total = float(legal_total.text) if legal_total is not None else 0.0
        base = total - iva
        return prov_name, base, iva, total, factura_ref
    except: return None, 0, 0, 0, None

# --- ROBOT CORREO ---
def run_email_sync(sheet_instance):
    st.info("🤖 Buscando facturas en correos recientes...")
    reporte_log = []
    nuevos_gastos = 0
    try:
        EMAIL_USER = st.secrets["email"]["user"]
        EMAIL_PASS = st.secrets["email"]["password"]
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
        date_since = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{date_since}")')
        email_ids = messages[0].split()
        
        ws_gastos = sheet_instance.worksheet("gastos")
        existing_refs = [str(x) for x in ws_gastos.col_values(3)] 

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    for part in msg.walk():
                        filename = part.get_filename()
                        if filename:
                            xml_content = None
                            if "zip" in filename.lower():
                                try:
                                    zfile = zipfile.ZipFile(io.BytesIO(part.get_payload(decode=True)))
                                    for name in zfile.namelist():
                                        if name.lower().endswith('.xml'):
                                            xml_content = zfile.read(name)
                                            prov, base, iva, total, ref = extract_xml_data(xml_content)
                                            if prov: break
                                except: pass
                            elif "xml" in filename.lower():
                                xml_content = part.get_payload(decode=True)

                            if xml_content:
                                prov, base, iva, total, ref = extract_xml_data(xml_content)
                                if prov and ref:
                                    is_duplicate = False
                                    for ex_ref in existing_refs:
                                        if ref in ex_ref: is_duplicate = True
                                    if not is_duplicate:
                                        ws_gastos.append_row([str(datetime.now().date()), prov, f"Factura {ref}", "POR CLASIFICAR", base, iva, total, "Gasto General", "Auto-Email"])
                                        reporte_log.append(f"✅ Factura {ref} de {prov} detectada.")
                                        nuevos_gastos += 1
                                        existing_refs.append(ref)
        mail.close()
        mail.logout()
        return reporte_log, nuevos_gastos
    except Exception as e: return [f"❌ Error: {str(e)}"], 0

# --- EMAIL RESUMEN ---
def send_summary_email(log_sync, df_p, df_n):
    try:
        EMAIL_USER = st.secrets["email"]["user"]
        EMAIL_PASS = st.secrets["email"]["password"]
        DESTINATARIO = st.secrets["email"]["admin_email"]
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = DESTINATARIO
        msg['Subject'] = f"📊 Reporte SQR - {datetime.now().date()}"
        cobros = df_p[df_p['Saldo Pendiente'] > 0]['Saldo Pendiente'].sum()
        deudas = df_n[df_n['Saldo Debe'] > 0]['Saldo Debe'].sum()
        body = f"Resumen:\n{chr(10).join(log_sync) if log_sync else 'Sin facturas nuevas.'}\n\nPor Cobrar: ${cobros:,.0f}\nPor Pagar: ${deudas:,.0f}"
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except: return False

# --- MAIN ---
df_p, df_g, df_n, sh = load_data()

st.sidebar.title("🧠 SQRapp Brain")
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Escanear Correo"):
    with st.spinner("Leyendo correos..."):
        logs, count = run_email_sync(sh)
        if count > 0: 
            st.success(f"{count} facturas nuevas.")
            df_p, df_g, df_n, sh = load_data()
        else: st.info("Sin novedades.")
        send_summary_email(logs, df_p, df_n)
        st.expander("Log").write(logs)
st.sidebar.markdown("---")

menu = st.sidebar.radio("Ir a:", ["🏠 Panel de Control", "📥 Gastos y Compras", "💰 Proyectos", "👥 Equipo"])

# --- 1. PANEL ---
if menu == "🏠 Panel de Control":
    st.title("Panel Gerencial")
    c1, c2, c3 = st.columns(3)
    por_cobrar = df_p['Saldo Pendiente'].sum()
    por_pagar = df_n['Saldo Debe'].sum()
    gastos_sin_clasificar = len(df_g[df_g['Proyecto Asignado'] == "POR CLASIFICAR"])
    c1.metric("Por Cobrar (Clientes)", f"${por_cobrar:,.0f}")
    c2.metric("Por Pagar (Equipo)", f"${por_pagar:,.0f}")
    c3.metric("Facturas sin Clasificar", f"{gastos_sin_clasificar}", delta="Revisar" if gastos_sin_clasificar > 0 else "Ok")

# --- 2. GASTOS (HÍBRIDO) ---
elif menu == "📥 Gastos y Compras":
    st.title("Control de Gastos")
    
    # SECCIÓN 1: MANUAL
    st.markdown("### 📝 Registrar Gasto Manual")
    st.caption("Usa esto para taxis, compras sin factura o gastos menores.")
    
    with st.form("gasto_manual", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        m_fecha = c1.date_input("Fecha", datetime.now())
        m_prov = c2.text_input("Proveedor (Ej: Taxi, Ferretería)")
        m_concepto = c3.text_input("Concepto (Ej: Transporte a obra)")
        
        c4, c5 = st.columns(2)
        lista_proyectos = df_p['Proyecto'].unique().tolist()
        m_proy = c4.selectbox("Asignar a Proyecto", lista_proyectos + ["Gasto General"])
        m_cat = c5.selectbox("Categoría", ["Viáticos/Transporte", "Materiales", "Alimentación", "Servicios", "Otros"])
        
        m_valor = st.number_input("Valor Total Pagado", min_value=0.0)
        
        if st.form_submit_button("💾 Guardar Gasto Manual"):
            try:
                # Guardamos con Base=Valor y IVA=0 (asumiendo régimen simplificado/sin factura)
                sh.worksheet("gastos").append_row([
                    str(m_fecha), 
                    m_prov, 
                    m_concepto, 
                    m_proy, 
                    m_valor, # Base
                    0,       # IVA (Manual suele ser 0)
                    m_valor, # Total
                    m_cat, 
                    "Manual" # Origen
                ])
                st.success("Gasto manual registrado exitosamente.")
                st.rerun()
            except Exception as e:
                st.error(f"Error guardando: {e}")

    st.markdown("---")
    
    # SECCIÓN 2: ROBOT
    st.markdown("### 🤖 Facturas Detectadas (Robot)")
    pendientes = df_g[df_g['Proyecto Asignado'] == "POR CLASIFICAR"].copy()
    
    if not pendientes.empty:
        st.info(f"Tienes {len(pendientes)} facturas que llegaron al correo y necesitan asignación.")
        st.dataframe(pendientes[['Fecha', 'Proveedor', 'Total Gasto', 'Concepto']])
        
        st.subheader("Clasificar Factura")
        factura_a_editar = st.selectbox("Seleccionar Factura para Asignar", pendientes['Concepto'].tolist())
        
        with st.form("clasificar"):
            nuevo_proy = st.selectbox("Asignar al Proyecto:", lista_proyectos + ["Gasto General"])
            nueva_cat = st.selectbox("Categoría:", ["Equipos", "Licencias", "Arriendo", "Marketing", "Insumos"])
            
            if st.form_submit_button("✅ Confirmar Clasificación"):
                cell = sh.worksheet("gastos").find(factura_a_editar)
                if cell:
                    sh.worksheet("gastos").update_cell(cell.row, 4, nuevo_proy)
                    sh.worksheet("gastos").update_cell(cell.row, 8, nueva_cat)
                    st.success("Factura asignada.")
                    st.rerun()
    else:
        st.write("✅ No hay facturas automáticas pendientes.")

# --- 3. PROYECTOS ---
elif menu == "💰 Proyectos":
    st.title("Proyectos")
    with st.expander("Nuevo Proyecto"):
        with st.form("new_p"):
            nom = st.text_input("Nombre")
            cli = st.text_input("Cliente")
            val = st.number_input("Valor Total", min_value=0)
            if st.form_submit_button("Crear"):
                sh.worksheet("proyectos").append_row([int(datetime.now().timestamp()), cli, nom, val, val*0.19, 0, val, "Activo"])
                st.rerun()
    
    st.subheader("Registrar Abono")
    proy_activos = df_p[df_p['Saldo Pendiente'] > 0]
    if not proy_activos.empty:
        p_sel = st.selectbox("Proyecto", proy_activos['Proyecto'].unique())
        abono = st.number_input("Monto Abono", min_value=0.0)
        if st.button("Registrar Abono"):
            idx = df_p[df_p['Proyecto'] == p_sel].index[0] + 2
            pagado = df_p.loc[df_p['Proyecto'] == p_sel, 'Pagado Cliente'].values[0] + abono
            saldo = df_p.loc[df_p['Proyecto'] == p_sel, 'Total Venta'].values[0] - pagado
            sh.worksheet("proyectos").update_cell(idx, 6, pagado)
            sh.worksheet("proyectos").update_cell(idx, 7, saldo)
            st.success("Abono registrado")
            st.rerun()
    st.dataframe(df_p)

# --- 4. EQUIPO ---
elif menu == "👥 Equipo":
    st.title("Equipo")
    with st.expander("Asignar Tarea"):
        with st.form("asignar"):
            nom = st.text_input("Nombre")
            rol = st.text_input("Rol")
            proy = st.selectbox("Proyecto", df_p['Proyecto'].unique())
            val = st.number_input("Valor", min_value=0)
            if st.form_submit_button("Asignar"):
                sh.worksheet("nomina").append_row([str(datetime.now().date()), nom, rol, proy, val, 0, val])
                st.rerun()
    
    st.subheader("Pagar a Equipo")
    deudas = df_n[df_n['Saldo Debe'] > 0]
    if not deudas.empty:
        sel_pago = st.selectbox("Pagar a:", deudas['Especialista'] + " - " + deudas['Proyecto'])
        monto = st.number_input("Monto", min_value=0.0)
        if st.button("Registrar Pago"):
            # Lógica simplificada
            idx = deudas[deudas['Especialista'] + " - " + deudas['Proyecto'] == sel_pago].index[0]
            row = idx + 2
            pagado_ant = float(deudas.loc[idx, 'Pagado'])
            nuevo_pagado = pagado_ant + monto
            nuevo_saldo = float(deudas.loc[idx, 'Valor Pactado']) - nuevo_pagado
            sh.worksheet("nomina").update_cell(row, 6, nuevo_pagado)
            sh.worksheet("nomina").update_cell(row, 7, nuevo_saldo)
            st.success("Pago registrado")
            st.rerun()
