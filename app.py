import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import imaplib
import email
import zipfile
import io
import numpy as np

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SQRapp Pro", layout="wide", page_icon="🚀")

# --- ESTILOS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #1e2130; padding: 10px; border-radius: 8px; border: 1px solid #333; }
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #1e2130; border-radius: 4px; color: white; }
    .stTabs [aria-selected="true"] { background-color: #4CAF50; color: white; }
    </style>
""", unsafe_allow_html=True)

# --- UTILIDADES ---
def fmt_money(x):
    if pd.isna(x) or x == "": return "$ 0"
    try: return "${:,.0f}".format(float(x)).replace(",", ".")
    except: return str(x)

def clean_colombian_money(series):
    s = series.astype(str).str.replace('$', '', regex=False).str.replace(' ', '', regex=False)
    s = s.str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
    return pd.to_numeric(s, errors='coerce').fillna(0)

def clean_text_key(series):
    """Normaliza nombres para asegurar que crucen bien (quita espacios y mayúsculas)"""
    return series.astype(str).str.strip().str.upper()

# --- CONEXIÓN ---
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
                    if c not in df.columns: df[c] = 0 if any(x in c for x in ['Valor', 'Total', 'IVA', 'Base']) else ""
                
                # Limpieza Numérica
                cols_num = [c for c in df.columns if any(x in c for x in ['Valor', 'Total', 'IVA', 'Monto', 'Pagado', 'Saldo', 'Base'])]
                for c in cols_num: df[c] = clean_colombian_money(df[c])
                
                return df
            except: return pd.DataFrame(columns=cols)

        df_p = get_df("proyectos", ['ID', 'Cliente', 'Proyecto', 'Total Venta', 'IVA Generado', 'Pagado Cliente', 'Saldo Pendiente', 'Estado', 'Tiene IVA'])
        df_g = get_df("gastos", ['Fecha', 'Proveedor', 'Concepto', 'Proyecto Asignado', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria', 'Origen'])
        df_n = get_df("nomina", ['Fecha', 'Especialista', 'Rol', 'Proyecto', 'Valor Pactado', 'Pagado', 'Saldo Debe'])
        
        # --- LIMPIEZA CLAVE PARA CRUCES ---
        # Creamos columnas "Key" limpias para asegurar que "Proyecto A" cruce con "proyecto a "
        if not df_p.empty: df_p['Key'] = clean_text_key(df_p['Proyecto'])
        if not df_g.empty: df_g['Key'] = clean_text_key(df_g['Proyecto Asignado'])
        if not df_n.empty: df_n['Key'] = clean_text_key(df_n['Proyecto'])
        
        return df_p, df_g, df_n, sh
    except Exception as e:
        st.error(f"Error DB: {e}")
        return None, None, None, None

# --- ROBOT FACTURAS ---
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
        ref = inv_id.text if inv_id is not None else "S/N"
        tax_total = root.find('.//cac:TaxTotal/cbc:TaxAmount', ns)
        iva = float(tax_total.text) if tax_total is not None else 0.0
        legal_total = root.find('.//cac:LegalMonetaryTotal/cbc:PayableAmount', ns)
        total = float(legal_total.text) if legal_total is not None else 0.0
        return prov_name, total - iva, iva, total, ref
    except: return None, 0, 0, 0, None

def run_email_sync(sheet_instance):
    st.toast("🤖 Buscando facturas en Gmail...")
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
        count = 0
        
        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    for part in msg.walk():
                        filename = part.get_filename() or ""
                        xml_content = None
                        if "zip" in filename.lower():
                            try:
                                zfile = zipfile.ZipFile(io.BytesIO(part.get_payload(decode=True)))
                                for name in zfile.namelist():
                                    if name.lower().endswith('.xml'):
                                        xml_content = zfile.read(name)
                                        break
                            except: pass
                        elif "xml" in filename.lower():
                            xml_content = part.get_payload(decode=True)

                        if xml_content:
                            prov, base, iva, total, ref = extract_xml_data(xml_content)
                            if prov and ref and not any(ref in x for x in existing_refs):
                                ws_gastos.append_row([str(datetime.now().date()), prov, f"Factura {ref}", "POR CLASIFICAR", base, iva, total, "Gasto General", "Auto-Email"])
                                count += 1
                                existing_refs.append(ref)
        mail.close()
        mail.logout()
        return count
    except Exception as e: 
        st.error(f"Error Email: {e}")
        return 0

# --- UI ---
df_p, df_g, df_n, sh = load_data()

st.sidebar.title("🚀 SQRapp Manager")
if st.sidebar.button("🔄 Sincronizar Todo"):
    c = run_email_sync(sh)
    if c > 0: st.success(f"{c} facturas nuevas.")
    st.rerun()

menu = st.sidebar.radio("Menú Principal", ["📊 Dashboard Gerencial", "💰 Proyectos & Obras", "📥 Centro de Gastos", "👥 Equipo & Nómina"])

# --- 1. DASHBOARD GERENCIAL ---
if menu == "📊 Dashboard Gerencial":
    st.title("📊 Visión Global del Negocio")
    
    # KPIs Principales
    ventas = df_p['Total Venta'].sum()
    gastos = df_g['Base'].sum()
    nomina = df_n['Valor Pactado'].sum()
    utilidad = ventas - (gastos + nomina)
    
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Ventas Totales", fmt_money(ventas))
    k2.metric("Gastos Operativos", fmt_money(gastos))
    k3.metric("Nómina Total", fmt_money(nomina))
    k4.metric("Utilidad Neta", fmt_money(utilidad), delta=f"{(utilidad/ventas)*100:.1f}%" if ventas>0 else "0%")
    
    st.divider()
    
    # TABLA MAESTRA DE RENTABILIDAD
    st.subheader("🏆 Rentabilidad Real por Proyecto")
    
    # Agrupación usando la KEY limpia para asegurar cruces
    g_proy = df_g.groupby('Key')['Base'].sum().reset_index()
    n_proy = df_n.groupby('Key')['Valor Pactado'].sum().reset_index()
    
    # Merge usando KEY
    df_master = df_p[['Proyecto', 'Key', 'Total Venta']].copy()
    df_master = df_master.merge(g_proy, on='Key', how='left').rename(columns={'Base': 'Gastos'})
    df_master = df_master.merge(n_proy, on='Key', how='left').rename(columns={'Valor Pactado': 'Nomina'})
    df_master = df_master.fillna(0)
    
    df_master['Costo Total'] = df_master['Gastos'] + df_master['Nomina']
    df_master['Ganancia'] = df_master['Total Venta'] - df_master['Costo Total']
    df_master['Margen'] = np.where(df_master['Total Venta']>0, (df_master['Ganancia']/df_master['Total Venta']*100), 0)
    
    # Visualización
    st.dataframe(df_master[['Proyecto', 'Total Venta', 'Gastos', 'Nomina', 'Ganancia', 'Margen']].style.format({
        'Total Venta': fmt_money, 'Gastos': fmt_money, 'Nomina': fmt_money, 
        'Ganancia': fmt_money, 'Margen': '{:.1f}%'
    }).background_gradient(subset=['Ganancia'], cmap='RdYlGn'), use_container_width=True)
    
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.caption("Distribución de Costos por Proyecto")
        fig = px.bar(df_master, x='Proyecto', y=['Gastos', 'Nomina'], title="Gastos vs Nómina", barmode='stack')
        st.plotly_chart(fig, use_container_width=True)

# --- 2. PROYECTOS ---
elif menu == "💰 Proyectos & Obras":
    st.title("Gestión de Proyectos")
    
    tab_p1, tab_p2 = st.tabs(["📂 Mis Proyectos", "➕ Nuevo Proyecto"])
    
    with tab_p1:
        st.subheader("Estado de Cartera")
        df_cobro = df_p[['Proyecto', 'Cliente', 'Total Venta', 'Pagado Cliente', 'Saldo Pendiente']]
        st.dataframe(df_cobro.style.format({
            'Total Venta': fmt_money, 'Pagado Cliente': fmt_money, 'Saldo Pendiente': fmt_money
        }), use_container_width=True)
        
        with st.expander("💸 Registrar Abono de Cliente"):
            with st.form("abono"):
                p_sel = st.selectbox("Proyecto", df_p[df_p['Saldo Pendiente']>1]['Proyecto'].unique())
                m_abono = st.number_input("Monto Abono", min_value=0.0)
                if st.form_submit_button("Registrar Pago"):
                    cell = sh.worksheet("proyectos").find(p_sel)
                    row = cell.row
                    curr = float(str(sh.worksheet("proyectos").cell(row, 6).value).replace('.','').replace(',','.') or 0)
                    total = float(str(sh.worksheet("proyectos").cell(row, 7).value).replace('.','').replace(',','.') or 0)
                    sh.worksheet("proyectos").update_cell(row, 6, curr + m_abono)
                    sh.worksheet("proyectos").update_cell(row, 7, total - (curr + m_abono))
                    st.success("Pago registrado")
                    st.rerun()

    with tab_p2:
        with st.form("crear_proy"):
            c1, c2 = st.columns(2)
            n_proy = c1.text_input("Nombre Proyecto")
            n_cli = c2.text_input("Cliente")
            val = st.number_input("Valor Venta", min_value=0)
            iva_bool = st.checkbox("Aplica IVA (19%)")
            if st.form_submit_button("Crear"):
                iva = val * 0.19 if iva_bool else 0
                sh.worksheet("proyectos").append_row([
                    int(datetime.now().timestamp()), n_cli, n_proy, val, iva, 0, val+iva, "Activo", "Sí" if iva_bool else "No"
                ])
                st.success("Proyecto Creado")
                st.rerun()

# --- 3. GASTOS (NUEVO DISEÑO) ---
elif menu == "📥 Centro de Gastos":
    st.title("Control de Gastos y Compras")
    
    # Pestañas para organizar la vista
    t_resumen, t_detalle, t_registro = st.tabs(["📊 Análisis de Gastos", "📑 Detalle por Proyecto", "📝 Registrar / Clasificar"])
    
    with t_resumen:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Gastos Generales vs Proyectos")
            # Separar gastos asignados de generales
            df_g['Tipo Gasto'] = np.where(df_g['Proyecto Asignado'].isin(['Gasto General', 'Oficina', 'Varios']), 'Administrativo', 'De Proyecto')
            fig_tipo = px.pie(df_g, names='Tipo Gasto', values='Base', hole=0.4)
            st.plotly_chart(fig_tipo, use_container_width=True)
        with c2:
            st.subheader("Top Categorías")
            fig_cat = px.bar(df_g.groupby('Categoria')['Base'].sum().reset_index(), x='Categoria', y='Base')
            st.plotly_chart(fig_cat, use_container_width=True)

    with t_detalle:
        st.subheader("Desglose: ¿En qué se gasta cada proyecto?")
        # Tabla dinámica (Pivot Table)
        if not df_g.empty:
            pivot = df_g.pivot_table(index='Proyecto Asignado', columns='Categoria', values='Base', aggfunc='sum', fill_value=0)
            # Formatear pivot para visualización
            st.dataframe(pivot.style.format(fmt_money), use_container_width=True)
        else:
            st.info("No hay gastos registrados aún.")

    with t_registro:
        c_man, c_bot = st.columns([1, 1])
        
        with c_man:
            st.info("📝 Registro Manual")
            with st.form("gasto_manual"):
                f = st.date_input("Fecha")
                p = st.text_input("Proveedor")
                c = st.text_input("Concepto")
                # Dropdown estricto desde Proyectos
                lista_proyectos = ["Gasto General"] + df_p['Proyecto'].unique().tolist()
                proy_dest = st.selectbox("Asignar a:", lista_proyectos)
                cat = st.selectbox("Categoría", ["Materiales", "Transporte", "Alimentación", "Servicios", "Nómina Extra"])
                v = st.number_input("Valor", min_value=0)
                if st.form_submit_button("Guardar Gasto"):
                    sh.worksheet("gastos").append_row([str(f), p, c, proy_dest, v, 0, v, cat, "Manual"])
                    st.success("Guardado")
                    st.rerun()
        
        with c_bot:
            st.warning("🤖 Facturas del Email (Pendientes)")
            pend = df_g[df_g['Proyecto Asignado'] == "POR CLASIFICAR"]
            if not pend.empty:
                st.dataframe(pend[['Proveedor', 'Total Gasto']])
                with st.form("bot_assign"):
                    fact_sel = st.selectbox("Factura", pend['Concepto'].unique())
                    p_bot = st.selectbox("Mover a:", lista_proyectos)
                    c_bot_cat = st.selectbox("Cat:", ["Materiales", "Equipos", "Servicios"])
                    if st.form_submit_button("Clasificar"):
                        cell = sh.worksheet("gastos").find(fact_sel, in_column=3)
                        sh.worksheet("gastos").update_cell(cell.row, 4, p_bot)
                        sh.worksheet("gastos").update_cell(cell.row, 8, c_bot_cat)
                        st.rerun()
            else:
                st.success("Todo al día.")

# --- 4. NÓMINA ---
elif menu == "👥 Equipo & Nómina":
    st.title("Gestión de Personal")
    
    tn1, tn2 = st.tabs(["💰 Pagos y Deudas", "👷 Asignar Trabajo"])
    
    with tn1:
        st.subheader("Estado de Cuenta por Persona")
        df_team = df_n.groupby(['Especialista'])[['Valor Pactado', 'Pagado', 'Saldo Debe']].sum().reset_index()
        st.dataframe(df_team.style.format({
            'Valor Pactado': fmt_money, 'Pagado': fmt_money, 'Saldo Debe': fmt_money
        }), use_container_width=True)
        
        with st.expander("💸 Registrar Pago a Personal"):
            with st.form("pago_nom"):
                deudores = df_n[df_n['Saldo Debe']>0]
                if not deudores.empty:
                    opc = deudores.apply(lambda x: f"{x['Especialista']} | {x['Proyecto']} | Debe: {fmt_money(x['Saldo Debe'])}", axis=1)
                    sel = st.selectbox("Seleccionar Item a Pagar", opc)
                    monto = st.number_input("Valor a Pagar", min_value=0.0)
                    if st.form_submit_button("Pagar"):
                        # Parsing seguro
                        parts = sel.split(" | ")
                        nom_s, proy_s = parts[0], parts[1]
                        
                        # Buscar fila exacta
                        all_rows = sh.worksheet("nomina").get_all_records()
                        idx = next((i for i, r in enumerate(all_rows) if r['Especialista']==nom_s and r['Proyecto']==proy_s), -1)
                        
                        if idx != -1:
                            row_n = idx + 2
                            curr = float(str(sh.worksheet("nomina").cell(row_n, 6).value).replace('.','').replace(',','.') or 0)
                            pact = float(str(sh.worksheet("nomina").cell(row_n, 5).value).replace('.','').replace(',','.') or 0)
                            sh.worksheet("nomina").update_cell(row_n, 6, curr + monto)
                            sh.worksheet("nomina").update_cell(row_n, 7, pact - (curr + monto))
                            st.success("Pago registrado")
                            st.rerun()
                else: st.info("No hay deudas pendientes.")

    with tn2:
        with st.form("add_task"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Nombre Especialista")
            rol = c2.selectbox("Rol", ["Instalador", "Ayudante", "Ingeniero", "Arquitecto"])
            proy = st.selectbox("Proyecto", df_p['Proyecto'].unique())
            val = st.number_input("Valor Pactado", min_value=0)
            if st.form_submit_button("Asignar"):
                sh.worksheet("nomina").append_row([str(datetime.now().date()), nom, rol, proy, val, 0, val])
                st.success("Asignado")
                st.rerun()
