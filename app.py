import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from imap_tools import MailBox, AND
from datetime import datetime
import zipfile
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import time

# --- CONFIGURACIÓN VISUAL SQRapp ---
st.set_page_config(page_title="SQRapp Secure", layout="wide", page_icon="🔒")

# ==========================================
# 🔐 SISTEMA DE LOGIN (SEGURIDAD)
# ==========================================
def check_password():
    """Retorna True si el usuario ingresó correctamente."""
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    # Diseño del Login
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("## 🔒 Acceso Restringido SQRapp")
        st.info("Por favor ingrese sus credenciales de gerencia.")
        
        user = st.text_input("Usuario", key="login_user")
        pwd = st.text_input("Contraseña", type="password", key="login_pwd")
        
        if st.button("Ingresar al Sistema"):
            # --- AQUÍ CONFIGURAS TU USUARIO Y CONTRASEÑA ---
            if user == "admin" and pwd == "1234":
                st.session_state["password_correct"] = True
                st.success("Acceso concedido. Cargando sistema...")
                time.sleep(1)
                st.rerun()
            else:
                st.error("❌ Usuario o contraseña incorrectos")
    
    return False

# SI NO ESTÁ LOGUEADO, DETENER TODO AQUÍ
if not check_password():
    st.stop()

# ==========================================
# 🚀 APLICACIÓN PRINCIPAL (Solo carga si pasó el login)
# ==========================================

# --- CONEXIÓN A GOOGLE SHEETS ---
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive"]

def conectar_google_sheets():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', SCOPE)
        
        client = gspread.authorize(creds)
        sheet = client.open("APP_SQR") 
        return sheet
    except Exception as e:
        return None

# --- FUNCIONES DE BASE DE DATOS ---
def cargar_datos():
    sheet = conectar_google_sheets()
    if sheet:
        try:
            p = pd.DataFrame(sheet.worksheet("proyectos").get_all_records())
            g = pd.DataFrame(sheet.worksheet("gastos").get_all_records())
            n = pd.DataFrame(sheet.worksheet("nomina").get_all_records())
            
            for col in ['Subtotal Venta', 'IVA Generado', 'Total Venta', 'Pagado por Cliente']:
                if col in p.columns: p[col] = pd.to_numeric(p[col], errors='coerce').fillna(0)
            
            for col in ['Subtotal', 'IVA Descontable', 'Total']:
                if col in g.columns: g[col] = pd.to_numeric(g[col], errors='coerce').fillna(0)
            
            for col in ['Valor Pactado', 'Total Abonado']:
                if col in n.columns: n[col] = pd.to_numeric(n[col], errors='coerce').fillna(0)

            return p, g, n, True
        except Exception as e:
            st.error(f"Error leyendo APP_SQR: {e}")
            return None, None, None, False
    else:
        return None, None, None, False

def guardar_fila(hoja_nombre, datos_dict):
    sheet = conectar_google_sheets()
    if sheet:
        ws = sheet.worksheet(hoja_nombre)
        if hoja_nombre == "proyectos":
            orden = ["Nombre Proyecto", "Subtotal Venta", "IVA Generado", "Total Venta", "Pagado por Cliente"]
        elif hoja_nombre == "gastos":
            orden = ["Fecha", "Proveedor", "Artículos", "Subtotal", "IVA Descontable", "Total", "Asignado A", "Tipo"]
        elif hoja_nombre == "nomina":
            orden = ["Trabajador", "Proyecto Asignado", "Valor Pactado", "Total Abonado"]
        
        fila = [datos_dict.get(c, "") for c in orden]
        ws.append_row(fila)
        return True
    return False

# --- CARGA INICIAL ---
df_proyectos, df_gastos, df_nomina, conectado = cargar_datos()

if not conectado:
    st.warning("⚠️ MODO OFFLINE: No se encontró 'credentials.json' o la hoja 'APP_SQR'.")
    if 'proyectos' not in st.session_state:
        st.session_state['proyectos'] = pd.DataFrame(columns=["Nombre Proyecto", "Subtotal Venta", "IVA Generado", "Total Venta", "Pagado por Cliente"])
    if 'gastos' not in st.session_state:
        st.session_state['gastos'] = pd.DataFrame(columns=["Fecha", "Proveedor", "Artículos", "Subtotal", "IVA Descontable", "Total", "Asignado A", "Tipo"])
    if 'nomina_contratos' not in st.session_state:
        st.session_state['nomina_contratos'] = pd.DataFrame(columns=["Trabajador", "Proyecto Asignado", "Valor Pactado", "Total Abonado"])
else:
    # Botón de Cerrar Sesión en la barra lateral
    if st.sidebar.button("🔒 Cerrar Sesión"):
        st.session_state["password_correct"] = False
        st.rerun()
        
    st.toast("🚀 Conectado a SQRapp Database")
    st.session_state['proyectos'] = df_proyectos
    st.session_state['gastos'] = df_gastos
    st.session_state['nomina_contratos'] = df_nomina

# --- INTERFAZ PRINCIPAL ---
st.title("🚀 SQRapp | Gerencia")

# --- LÓGICA DE FACTURAS (XML) ---
def extraer_datos_xml(contenido_xml):
    try:
        tree = ET.ElementTree(ET.fromstring(contenido_xml))
        root = tree.getroot()
        prov = root.find(".//{*}AccountingSupplierParty//{*}RegistrationName").text
        iva = 0.0
        tax_total = root.find(".//{*}TaxTotal")
        if tax_total:
            tax_amount = tax_total.find(".//{*}TaxAmount")
            if tax_amount is not None: iva = float(tax_amount.text)
        legal = root.find(".//{*}LegalMonetaryTotal")
        total_pagar = float(legal.find(".//{*}PayableAmount").text)
        subtotal = total_pagar - iva
        fecha = root.find(".//{*}IssueDate").text
        items_desc = []
        for line in root.findall(".//{*}InvoiceLine"):
            item = line.find(".//{*}Item")
            if item:
                desc = item.find(".//{*}Description")
                if desc is not None: items_desc.append(desc.text)
        resumen_items = ", ".join(items_desc) if items_desc else "General"
        return {"Fecha": fecha, "Proveedor": prov, "Artículos": resumen_items, "Subtotal": subtotal, "IVA Descontable": iva, "Total": total_pagar}
    except: return None

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "1. 👥 Equipo", 
    "2. 💰 Ventas", 
    "3. 💳 Gastos", 
    "4. 📊 Rentabilidad", 
    "5. 🏛️ Impuestos"
])

# ==========================================
# TAB 1: EQUIPO / NÓMINA
# ==========================================
with tab1:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Nuevo Contrato / Servicio")
        with st.form("add_nom"):
            trab = st.text_input("Nombre Colaborador/Proveedor")
            opciones_proy = st.session_state['proyectos']['Nombre Proyecto'].unique() if not st.session_state['proyectos'].empty else ["General"]
            proy = st.selectbox("Proyecto Asignado", options=opciones_proy)
            val = st.number_input("Valor Total del Acuerdo", min_value=0)
            
            if st.form_submit_button("Registrar Contrato"):
                nuevo = {"Trabajador": trab, "Proyecto Asignado": proy, "Valor Pactado": val, "Total Abonado": 0}
                st.session_state['nomina_contratos'] = pd.concat([st.session_state['nomina_contratos'], pd.DataFrame([nuevo])], ignore_index=True)
                if conectado:
                    guardar_fila("nomina", nuevo)
                    st.success(f"Contrato con {trab} guardado en SQRapp.")
                else:
                    st.warning("Guardado temporalmente (Offline).")

    with c2:
        st.subheader("Registrar Pago / Anticipo")
        if not st.session_state['nomina_contratos'].empty:
            df_n = st.session_state['nomina_contratos']
            df_n['Ref'] = df_n['Trabajador'] + " - " + df_n['Proyecto Asignado']
            sel = st.selectbox("Seleccionar Contrato", df_n['Ref'])
            abono = st.number_input("Valor a Pagar Hoy", min_value=0)
            
            if st.button("💸 Registrar Pago"):
                row = df_n[df_n['Ref'] == sel].iloc[0]
                gasto = {
                    "Fecha": str(datetime.now().date()),
                    "Proveedor": f"Nómina: {row['Trabajador']}",
                    "Artículos": "Servicios Profesionales / Mano de Obra",
                    "Subtotal": abono, "IVA Descontable": 0, "Total": abono,
                    "Asignado A": row['Proyecto Asignado'], "Tipo": "Nómina"
                }
                st.session_state['gastos'] = pd.concat([st.session_state['gastos'], pd.DataFrame([gasto])], ignore_index=True)
                
                if conectado:
                    guardar_fila("gastos", gasto)
                    st.success("Pago registrado correctamente.")
                else:
                    st.success("Pago registrado en memoria.")

# ==========================================
# TAB 2: VENTAS (PROYECTOS)
# ==========================================
with tab2:
    c_form, c_data = st.columns([1, 2])
    with c_form:
        st.subheader("Nuevo Proyecto")
        with st.form("add_proy"):
            nom = st.text_input("Nombre del Proyecto")
            val = st.number_input("Valor Venta (Sin IVA)", min_value=0)
            iva = st.checkbox("Aplica IVA 19%", value=True)
            abo = st.number_input("Anticipo Recibido", min_value=0)
            
            if st.form_submit_button("Crear Proyecto"):
                iva_v = val * 0.19 if iva else 0
                nuevo = {
                    "Nombre Proyecto": nom, "Subtotal Venta": val, "IVA Generado": iva_v, 
                    "Total Venta": val + iva_v, "Pagado por Cliente": abo
                }
                st.session_state['proyectos'] = pd.concat([st.session_state['proyectos'], pd.DataFrame([nuevo])], ignore_index=True)
                if conectado: guardar_fila("proyectos", nuevo)
                st.rerun()
    
    with c_data:
        st.subheader("Proyectos Activos")
        st.dataframe(st.session_state['proyectos'], use_container_width=True)

# ==========================================
# TAB 3: GASTOS (AUTOMATIZACIÓN)
# ==========================================
with tab3:
    st.info("💡 Conecta tu Gmail para descargar facturas electrónicas automáticamente (XML/ZIP).")
    col_cred, col_btn = st.columns([2,1])
    with col_cred:
        u = st.text_input("Gmail Usuario")
        p = st.text_input("Contraseña de Aplicación (App Password)", type="password")
    with col_btn:
        st.write("")
        if st.button("📥 Descargar Facturas"):
            if u and p:
                try:
                    with MailBox('imap.gmail.com').login(u, p) as mb:
                        msgs = mb.fetch(AND(date_gte=datetime.date(datetime.now().replace(day=1)), has_attachments=True), limit=5)
                        nuevos = []
                        for m in msgs:
                            for a in m.attachments:
                                d = None
                                if a.filename.endswith('.xml'): d = extraer_datos_xml(a.payload)
                                elif a.filename.endswith('.zip'):
                                    with zipfile.ZipFile(io.BytesIO(a.payload)) as z:
                                        for zf in z.namelist():
                                            if zf.endswith('.xml'): 
                                                with z.open(zf) as f: d = extraer_datos_xml(f.read())
                                if d: 
                                    d["Asignado A"] = "Sin Asignar"
                                    d["Tipo"] = "Auto"
                                    nuevos.append(d)
                        
                        if nuevos:
                            df_nuevos = pd.DataFrame(nuevos)
                            st.session_state['gastos'] = pd.concat([st.session_state['gastos'], df_nuevos], ignore_index=True)
                            if conectado:
                                for n in nuevos: guardar_fila("gastos", n)
                            st.success(f"Se encontraron {len(nuevos)} facturas nuevas.")
                        else:
                            st.warning("No se encontraron facturas recientes con XML.")
                except Exception as e: st.error(f"Error de conexión: {str(e)}")

    st.divider()
    st.subheader("Historial de Gastos")
    st.dataframe(st.session_state['gastos'], use_container_width=True)

# ==========================================
# TAB 4: RENTABILIDAD
# ==========================================
with tab4:
    st.header("📊 Estado Financiero por Proyecto")
    if not st.session_state['proyectos'].empty:
        g = st.session_state['gastos'].groupby("Asignado A")['Subtotal'].sum().reset_index()
        f = pd.merge(st.session_state['proyectos'], g, left_on="Nombre Proyecto", right_on="Asignado A", how="left").fillna(0)
        
        f['Utilidad'] = f['Subtotal Venta'] - f['Subtotal']
        f['Margen %'] = (f['Utilidad'] / f['Subtotal Venta']) * 100
        f['Margen %'] = f['Margen %'].fillna(0)

        st.dataframe(
            f[["Nombre Proyecto", "Subtotal Venta", "Subtotal", "Utilidad", "Margen %"]]
            .style.format({
                "Subtotal Venta": "${:,.0f}", 
                "Subtotal": "${:,.0f}", 
                "Utilidad": "${:,.0f}",
                "Margen %": "{:.1f}%"
            })
            .bar(subset=["Utilidad"], color=["#ff4b4b", "#00cc96"], align=0),
            use_container_width=True
        )

# ==========================================
# TAB 5: IMPUESTOS
# ==========================================
with tab5:
    st.header("🏛️ Control de IVA")
    ig = st.session_state['proyectos']['IVA Generado'].sum()
    id = st.session_state['gastos']['IVA Descontable'].sum()
    dif = ig - id
    
    c1, c2, c3 = st.columns(3)
    c1.metric("IVA Cobrado (Generado)", f"${ig:,.0f}")
    c2.metric("IVA Pagado (Descontable)", f"${id:,.0f}")
    
    if dif > 0:
        c3.metric("A PAGAR A LA DIAN", f"${dif:,.0f}", delta=-dif, delta_color="inverse")
    else:
        c3.metric("SALDO A FAVOR", f"${abs(dif):,.0f}", delta=abs(dif))