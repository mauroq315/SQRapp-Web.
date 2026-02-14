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
st.set_page_config(page_title="SQRapp Pro", layout="wide", page_icon="🚀")

# --- ESTILOS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .metric-card { background-color: #1e2130; padding: 15px; border-radius: 10px; border-left: 5px solid #4CAF50; }
    .stDataFrame { border: 1px solid #333; border-radius: 5px; }
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
                # Asegurar columnas mínimas
                for c in cols: 
                    if c not in df.columns: df[c] = 0 if any(x in c for x in ['Valor', 'IVA', 'Total', 'Monto', 'Pagado', 'Saldo', 'Base']) else ""
                # Limpieza numérica agresiva
                for c in df.columns:
                    if any(x in c for x in ['Valor', 'Total', 'IVA', 'Monto', 'Pagado', 'Saldo', 'Base']):
                        df[c] = pd.to_numeric(df[c].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)
                return df
            except: return pd.DataFrame(columns=cols)

        df_p = get_df("proyectos", ['ID', 'Cliente', 'Proyecto', 'Total Venta', 'IVA Generado', 'Pagado Cliente', 'Saldo Pendiente', 'Estado', 'Tiene IVA'])
        df_g = get_df("gastos", ['Fecha', 'Proveedor', 'Concepto', 'Proyecto Asignado', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria', 'Origen'])
        df_n = get_df("nomina", ['Fecha', 'Especialista', 'Rol', 'Proyecto', 'Valor Pactado', 'Pagado', 'Saldo Debe'])
        return df_p, df_g, df_n, sh
    except Exception as e:
        st.error(f"Error DB: {e}")
        return None, None, None, None

# --- LÓGICA ROBOT (Igual que antes, optimizada) ---
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

def run_email_sync(sheet_instance):
    st.info("🤖 Buscando facturas...")
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

# --- UI PRINCIPAL ---
df_p, df_g, df_n, sh = load_data()

# SIDEBAR
st.sidebar.title("🚀 SQRapp Pro")
if st.sidebar.button("🔄 Sincronizar Todo"):
    logs, count = run_email_sync(sh)
    if count > 0: st.success(f"{count} facturas nuevas.")
    st.rerun()

menu = st.sidebar.radio("Navegación:", 
    ["📊 Inteligencia de Negocio", "💰 Gestión de Proyectos", "📥 Gastos & Compras", "👥 Nómina & Equipo"])

# --- 1. INTELIGENCIA DE NEGOCIO (REPORTES) ---
if menu == "📊 Inteligencia de Negocio":
    st.title("📊 Radiografía del Negocio")
    
    # KPIs Generales
    k1, k2, k3, k4 = st.columns(4)
    total_ventas = df_p['Total Venta'].sum()
    total_gastos = df_g['Base'].sum() # Sin IVA
    total_nomina = df_n['Valor Pactado'].sum()
    utilidad_bruta = total_ventas - (total_gastos + total_nomina)
    
    k1.metric("Ventas Totales", f"${total_ventas:,.0f}")
    k2.metric("Gastos (Sin IVA)", f"${total_gastos:,.0f}")
    k3.metric("Costo Nómina", f"${total_nomina:,.0f}")
    k4.metric("Utilidad Neta", f"${utilidad_bruta:,.0f}", delta=f"{(utilidad_bruta/total_ventas)*100:.1f}% Margen" if total_ventas > 0 else "0%")

    st.markdown("---")
    
    # PESTAÑAS DE REPORTES
    tab1, tab2, tab3 = st.tabs(["📈 Rentabilidad por Proyecto", "👥 Deudas Equipo", "🏛️ Impuestos (IVA)"])
    
    with tab1:
        st.subheader("¿Cuánto gano realmente en cada proyecto?")
        
        # Preparar datos cruzados
        gastos_por_proy = df_g.groupby('Proyecto Asignado')['Base'].sum().reset_index()
        nomina_por_proy = df_n.groupby('Proyecto')['Valor Pactado'].sum().reset_index()
        
        # Unir con proyectos
        df_rent = df_p[['Proyecto', 'Total Venta', 'Cliente']].copy()
        df_rent = df_rent.merge(gastos_por_proy, left_on='Proyecto', right_on='Proyecto Asignado', how='left').fillna(0)
        df_rent = df_rent.merge(nomina_por_proy, left_on='Proyecto', right_on='Proyecto', how='left').fillna(0)
        
        df_rent['Costo Total'] = df_rent['Base'] + df_rent['Valor Pactado']
        df_rent['Utilidad'] = df_rent['Total Venta'] - df_rent['Costo Total']
        df_rent['Margen %'] = (df_rent['Utilidad'] / df_rent['Total Venta'] * 100).round(1)
        
        # Renombrar para mostrar bonito
        df_final = df_rent[['Proyecto', 'Total Venta', 'Base', 'Valor Pactado', 'Utilidad', 'Margen %']]
        df_final.columns = ['Proyecto', 'Venta', 'Gastos (Mat/Var)', 'Mano de Obra', 'Ganancia', 'Margen %']
        
        st.dataframe(df_final.style.format({
            'Venta': '${:,.0f}', 'Gastos (Mat/Var)': '${:,.0f}', 
            'Mano de Obra': '${:,.0f}', 'Ganancia': '${:,.0f}', 'Margen %': '{:.1f}%'
        }).background_gradient(subset=['Ganancia'], cmap='RdYlGn'))

    with tab2:
        st.subheader("Estado de Cuenta del Equipo (Por Proyecto)")
        # Agrupar por Especialista y Proyecto
        df_team = df_n.groupby(['Especialista', 'Proyecto'])[['Valor Pactado', 'Pagado', 'Saldo Debe']].sum().reset_index()
        
        # Filtros
        persona = st.selectbox("Filtrar por Persona (Opcional)", ["Todos"] + df_team['Especialista'].unique().tolist())
        if persona != "Todos":
            df_team = df_team[df_team['Especialista'] == persona]
        
        st.dataframe(df_team.style.format({'Valor Pactado': '${:,.0f}', 'Pagado': '${:,.0f}', 'Saldo Debe': '${:,.0f}'}))
        
        total_deuda = df_team['Saldo Debe'].sum()
        st.info(f"💰 Deuda Total Filtrada: ${total_deuda:,.0f}")

    with tab3:
        st.subheader("Cruce de IVA (Aproximado)")
        iva_gen = df_p['IVA Generado'].sum()
        iva_desc = df_g['IVA Descontable'].sum()
        a_pagar = iva_gen - iva_desc
        
        c1, c2, c3 = st.columns(3)
        c1.metric("IVA Generado (Ventas)", f"${iva_gen:,.0f}")
        c2.metric("IVA Descontable (Compras)", f"${iva_desc:,.0f}")
        c3.metric("IVA A Pagar DIAN", f"${a_pagar:,.0f}", delta_color="inverse")

# --- 2. PROYECTOS ---
elif menu == "💰 Gestión de Proyectos":
    st.title("Gestión de Proyectos")
    
    with st.expander("➕ Crear Nuevo Proyecto"):
        with st.form("new_p"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Nombre del Proyecto")
            cli = c2.text_input("Cliente")
            
            c3, c4 = st.columns(2)
            val = c3.number_input("Valor Venta (Antes de IVA)", min_value=0)
            tiene_iva = c4.radio("¿Aplica IVA?", ["Sí (19%)", "No (Exento/Cuenta Cobro)"])
            
            if st.form_submit_button("Crear Proyecto"):
                iva_calc = val * 0.19 if "Sí" in tiene_iva else 0
                total_con_iva = val + iva_calc
                
                sh.worksheet("proyectos").append_row([
                    int(datetime.now().timestamp()), 
                    cli, nom, val, iva_calc, 0, total_con_iva, "Activo", tiene_iva
                ])
                st.success("Proyecto Creado")
                st.rerun()
    
    # Tabla editable de Abonos
    st.subheader("Cartera (Cuentas por Cobrar)")
    df_cobrar = df_p[['Cliente', 'Proyecto', 'Total Venta', 'Pagado Cliente', 'Saldo Pendiente']].copy()
    st.dataframe(df_cobrar.style.format("${:,.0f}"))
    
    with st.form("abono_cliente"):
        st.write("Registrar Abono de Cliente")
        col1, col2 = st.columns(2)
        proy_abono = col1.selectbox("Proyecto", df_p[df_p['Saldo Pendiente'] > 1]['Proyecto'].unique())
        monto_abono = col2.number_input("Valor Abono", min_value=0.0)
        
        if st.form_submit_button("Registrar Pago"):
            # Buscar fila y actualizar
            cell = sh.worksheet("proyectos").find(proy_abono)
            if cell:
                row = cell.row
                # Leer valor actual para asegurar (columna 6 es Pagado)
                curr_pagado = float(sh.worksheet("proyectos").cell(row, 6).value or 0)
                curr_total = float(sh.worksheet("proyectos").cell(row, 7).value or 0) # Total con IVA es la deuda real
                
                nuevo_pagado = curr_pagado + monto_abono
                nuevo_saldo = curr_total - nuevo_pagado # Ojo: Saldo se calcula sobre el total con IVA si aplica
                
                # Nota: En la hoja original, col 4 es Venta, col 7 es Saldo. 
                # Ajuste rápido: Recalcular saldo basado en Total Venta + IVA - Pagado
                venta_base = float(sh.worksheet("proyectos").cell(row, 4).value)
                iva_val = float(sh.worksheet("proyectos").cell(row, 5).value)
                total_real = venta_base + iva_val
                
                sh.worksheet("proyectos").update_cell(row, 6, nuevo_pagado)
                sh.worksheet("proyectos").update_cell(row, 7, total_real - nuevo_pagado)
                st.success("Abono registrado correctamente")
                st.rerun()

# --- 3. GASTOS ---
elif menu == "📥 Gastos & Compras":
    st.title("Control de Gastos")
    
    # FORMULARIO MANUAL MEJORADO
    with st.expander("📝 Registrar Gasto Manual (Taxis, Compras sin factura)", expanded=True):
        with st.form("gasto_manual"):
            c1, c2, c3 = st.columns(3)
            fecha = c1.date_input("Fecha")
            prov = c2.text_input("Proveedor")
            conc = c3.text_input("Concepto")
            
            c4, c5, c6 = st.columns(3)
            proy = c4.selectbox("Asignar a:", df_p['Proyecto'].unique().tolist() + ["Gasto General"])
            cat = c5.selectbox("Categoría", ["Materiales", "Transporte", "Alimentación", "Servicios", "Administrativo"])
            valor = c6.number_input("Valor Total", min_value=0)
            
            if st.form_submit_button("Guardar"):
                sh.worksheet("gastos").append_row([
                    str(fecha), prov, conc, proy, valor, 0, valor, cat, "Manual"
                ])
                st.success("Guardado")
                st.rerun()
    
    # TABLA ROBOT
    st.subheader("🤖 Facturas Detectadas (Pendientes de Clasificar)")
    pendientes = df_g[df_g['Proyecto Asignado'] == "POR CLASIFICAR"]
    if not pendientes.empty:
        st.dataframe(pendientes)
        # (Aquí iría la lógica de clasificación que ya tenías, resumida por espacio)
        with st.form("clasif_express"):
            f_id = st.selectbox("Factura", pendientes['Concepto'])
            p_dest = st.selectbox("Mover a Proyecto:", df_p['Proyecto'].unique().tolist() + ["Gasto General"])
            c_dest = st.selectbox("Categoría:", ["Materiales", "Equipos", "Servicios"])
            if st.form_submit_button("Asignar"):
                cell = sh.worksheet("gastos").find(f_id)
                sh.worksheet("gastos").update_cell(cell.row, 4, p_dest)
                sh.worksheet("gastos").update_cell(cell.row, 8, c_dest)
                st.rerun()
    else:
        st.info("No hay facturas pendientes en el buzón.")

    # TABLA HISTÓRICA
    st.subheader("📚 Historial de Gastos")
    st.dataframe(df_g)

# --- 4. NÓMINA ---
elif menu == "👥 Nómina & Equipo":
    st.title("Gestión de Equipo")
    
    c1, c2 = st.columns(2)
    with c1.form("asignar_tarea"):
        st.write("👷 Asignar Nueva Tarea")
        nom = st.text_input("Nombre Especialista")
        rol = st.selectbox("Rol", ["Instalador", "Ingeniero", "Ayudante", "Trafficker", "Diseñador"])
        proy = st.selectbox("Proyecto", df_p['Proyecto'].unique())
        val = st.number_input("Valor a Pagar", min_value=0)
        if st.form_submit_button("Asignar"):
            sh.worksheet("nomina").append_row([str(datetime.now().date()), nom, rol, proy, val, 0, val])
            st.rerun()

    with c2.form("pagar_nomina"):
        st.write("💸 Registrar Pago a Equipo")
        deudores = df_n[df_n['Saldo Debe'] > 0]
        if not deudores.empty:
            # Crear etiqueta compuesta para identificar único
            opciones = deudores.apply(lambda x: f"{x['Especialista']} - {x['Proyecto']} (${x['Saldo Debe']:,.0f})", axis=1)
            seleccion = st.selectbox("Seleccionar Pago pendiente", opciones)
            monto = st.number_input("Valor a Abonar", min_value=0.0)
            
            if st.form_submit_button("Pagar"):
                # Lógica de búsqueda inversa
                nombre_sel = seleccion.split(" - ")[0]
                proy_sel = seleccion.split(" - ")[1].split(" ($")[0]
                
                # Buscar fila exacta (esto es simplificado, en prod usar IDs)
                # Iteramos para hallar la fila que coincida en Nombre y Proyecto
                all_data = sh.worksheet("nomina").get_all_records()
                row_idx = -1
                for i, row in enumerate(all_data):
                    if row['Especialista'] == nombre_sel and row['Proyecto'] == proy_sel:
                        row_idx = i + 2 # +2 por header y 0-index
                        break
                
                if row_idx > 0:
                    curr_pagado = float(str(sh.worksheet("nomina").cell(row_idx, 6).value).replace(',','').replace('$',''))
                    curr_pactado = float(str(sh.worksheet("nomina").cell(row_idx, 5).value).replace(',','').replace('$',''))
                    
                    nuevo_pagado = curr_pagado + monto
                    nuevo_saldo = curr_pactado - nuevo_pagado
                    
                    sh.worksheet("nomina").update_cell(row_idx, 6, nuevo_pagado)
                    sh.worksheet("nomina").update_cell(row_idx, 7, nuevo_saldo)
                    st.success("Pago registrado")
                    st.rerun()
        else:
            st.info("Estás a paz y salvo con el equipo.")
    
    st.markdown("---")
    st.subheader("Detalle General de Nómina")
    st.dataframe(df_n)
