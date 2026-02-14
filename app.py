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
    .iva-box { padding: 20px; border-radius: 10px; text-align: center; color: white; margin-bottom: 10px; }
    </style>
""", unsafe_allow_html=True)

# --- UTILIDADES ---
def fmt_money(x):
    if pd.isna(x) or str(x).strip() == "": return "$ 0"
    try: return "${:,.0f}".format(float(x)).replace(",", ".")
    except: return str(x)

def clean_colombian_money(series):
    s = series.astype(str).str.replace('$', '', regex=False).str.replace(' ', '', regex=False)
    s = s.str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
    return pd.to_numeric(s, errors='coerce').fillna(0)

def clean_text_key(series):
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
        
        def get_df_robust(name, required_cols):
            try:
                ws = sh.worksheet(name)
                data = ws.get_all_values()
                if len(data) < 2: return pd.DataFrame(columns=required_cols)
                
                headers = data[0]
                rows = data[1:]
                df = pd.DataFrame(rows, columns=headers)
                
                # Manejo de columnas duplicadas (como el error de IVA Descontable)
                df = df.loc[:, ~df.columns.duplicated()]
                
                for col in required_cols:
                    if col not in df.columns: df[col] = ""
                
                # Seleccionar columnas requeridas
                df = df[required_cols].copy()
                
                # Limpieza Numérica
                cols_num = [c for c in df.columns if any(x in c for x in ['Valor', 'Total', 'IVA', 'Monto', 'Pagado', 'Saldo', 'Base'])]
                for c in cols_num: df[c] = clean_colombian_money(df[c])
                return df
            except Exception as e:
                st.error(f"Error leyendo {name}: {e}")
                return pd.DataFrame(columns=required_cols)

        cols_p = ['ID', 'Cliente', 'Proyecto', 'Total Venta', 'IVA Generado', 'Pagado Cliente', 'Saldo Pendiente', 'Estado', 'Tiene IVA']
        cols_g = ['Fecha', 'Proveedor', 'Concepto', 'Proyecto Asignado', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria', 'Origen']
        cols_n = ['Fecha', 'Especialista', 'Rol', 'Proyecto', 'Valor Pactado', 'Pagado', 'Saldo Debe']

        df_p = get_df_robust("proyectos", cols_p)
        df_g = get_df_robust("gastos", cols_g)
        df_n = get_df_robust("nomina", cols_n)
        
        # Generar Keys
        if not df_p.empty: df_p['Key'] = clean_text_key(df_p['Proyecto'])
        else: df_p['Key'] = ""
        if not df_g.empty: df_g['Key'] = clean_text_key(df_g['Proyecto Asignado'])
        else: df_g['Key'] = ""
        if not df_n.empty: df_n['Key'] = clean_text_key(df_n['Proyecto'])
        else: df_n['Key'] = ""
        
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
    st.toast("🤖 Buscando facturas...")
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
    except Exception as e: return 0

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
    st.title("📊 Visión Global")
    
    tab_kpi, tab_iva = st.tabs(["📈 Rentabilidad", "🏛️ Impuestos & IVA"])
    
    with tab_kpi:
        ventas = df_p['Total Venta'].sum()
        gastos = df_g['Base'].sum()
        nomina = df_n['Valor Pactado'].sum()
        utilidad = ventas - (gastos + nomina)
        
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Ventas Totales", fmt_money(ventas))
        k2.metric("Gastos (Base)", fmt_money(gastos))
        k3.metric("Nómina", fmt_money(nomina))
        k4.metric("Utilidad Neta", fmt_money(utilidad), delta=f"{(utilidad/ventas)*100:.1f}%" if ventas>0 else "0%")
        
        st.divider()
        st.subheader("🏆 Rentabilidad por Proyecto")
        if not df_p.empty:
            g_proy = df_g.groupby('Key')['Base'].sum().reset_index() if not df_g.empty else pd.DataFrame()
            n_proy = df_n.groupby('Key')['Valor Pactado'].sum().reset_index() if not df_n.empty else pd.DataFrame()
            
            df_master = df_p[['Proyecto', 'Key', 'Total Venta']].copy()
            if not g_proy.empty: df_master = df_master.merge(g_proy, on='Key', how='left').rename(columns={'Base': 'Gastos'})
            else: df_master['Gastos'] = 0
            if not n_proy.empty: df_master = df_master.merge(n_proy, on='Key', how='left').rename(columns={'Valor Pactado': 'Nomina'})
            else: df_master['Nomina'] = 0
            
            df_master = df_master.fillna(0)
            df_master['Ganancia'] = df_master['Total Venta'] - (df_master['Gastos'] + df_master['Nomina'])
            
            st.dataframe(df_master[['Proyecto', 'Total Venta', 'Gastos', 'Nomina', 'Ganancia']].style.format(fmt_money).background_gradient(subset=['Ganancia'], cmap='RdYlGn'), use_container_width=True)

    with tab_iva:
        st.subheader("Cruce de Cuentas con DIAN")
        
        iva_generado = df_p['IVA Generado'].sum()
        iva_descontable = df_g['IVA Descontable'].sum()
        iva_pagar = iva_generado - iva_descontable
        
        c_iva1, c_iva2, c_iva3 = st.columns(3)
        
        c_iva1.markdown(f"""
        <div class="iva-box" style="background-color: #2e7d32;">
            <h3>IVA Generado</h3>
            <h2>{fmt_money(iva_generado)}</h2>
            <p>Cobrado a Clientes</p>
        </div>
        """, unsafe_allow_html=True)
        
        c_iva2.markdown(f"""
        <div class="iva-box" style="background-color: #c62828;">
            <h3>IVA Descontable</h3>
            <h2>{fmt_money(iva_descontable)}</h2>
            <p>Pagado en Compras</p>
        </div>
        """, unsafe_allow_html=True)
        
        color_final = "#fbc02d" if iva_pagar > 0 else "#1565c0"
        texto_final = "A PAGAR A DIAN" if iva_pagar > 0 else "SALDO A FAVOR"
        
        c_iva3.markdown(f"""
        <div class="iva-box" style="background-color: {color_final}; color: black;">
            <h3>{texto_final}</h3>
            <h2>{fmt_money(abs(iva_pagar))}</h2>
            <p>Posición Neta</p>
        </div>
        """, unsafe_allow_html=True)
        
        st.info("💡 Recuerda: El IVA Descontable solo aplica si pediste factura electrónica a nombre de la empresa.")

# --- 2. PROYECTOS ---
elif menu == "💰 Proyectos & Obras":
    st.title("Gestión de Proyectos")
    tab_p1, tab_p2 = st.tabs(["📂 Mis Proyectos", "➕ Nuevo Proyecto"])
    
    with tab_p1:
        st.dataframe(df_p[['Proyecto', 'Cliente', 'Total Venta', 'Pagado Cliente', 'Saldo Pendiente']].style.format(fmt_money), use_container_width=True)
        with st.expander("💸 Registrar Abono"):
            with st.form("abono"):
                pendientes = df_p[df_p['Saldo Pendiente'] > 1]
                if not pendientes.empty:
                    p_sel = st.selectbox("Proyecto", pendientes['Proyecto'].unique())
                    m_abono = st.number_input("Monto Abono", min_value=0.0)
                    if st.form_submit_button("Registrar"):
                        cell = sh.worksheet("proyectos").find(p_sel)
                        row = cell.row
                        curr = float(str(sh.worksheet("proyectos").cell(row, 6).value).replace('.','').replace(',','.') or 0)
                        total = float(str(sh.worksheet("proyectos").cell(row, 7).value).replace('.','').replace(',','.') or 0)
                        sh.worksheet("proyectos").update_cell(row, 6, curr + m_abono)
                        sh.worksheet("proyectos").update_cell(row, 7, total - (curr + m_abono))
                        st.success("Registrado")
                        st.rerun()
                else: st.info("Sin saldos pendientes.")

    with tab_p2:
        with st.form("crear_proy"):
            c1, c2 = st.columns(2)
            n_proy = c1.text_input("Nombre Proyecto")
            n_cli = c2.text_input("Cliente")
            val = st.number_input("Valor Venta (Antes de IVA)", min_value=0)
            iva_bool = st.checkbox("Aplica IVA (19%)")
            if st.form_submit_button("Crear"):
                iva = val * 0.19 if iva_bool else 0
                sh.worksheet("proyectos").append_row([
                    int(datetime.now().timestamp()), n_cli, n_proy, val, iva, 0, val+iva, "Activo", "Sí" if iva_bool else "No"
                ])
                st.success("Creado")
                st.rerun()

# --- 3. GASTOS ---
elif menu == "📥 Centro de Gastos":
    st.title("Control de Gastos")
    t_res, t_reg = st.tabs(["📊 Análisis", "📝 Registrar Gasto"])
    
    with t_res:
        if not df_g.empty:
            c1, c2 = st.columns(2)
            with c1: st.plotly_chart(px.pie(df_g, names='Categoria', values='Base', title="Por Categoría"), use_container_width=True)
            with c2: 
                pivot = df_g.pivot_table(index='Proyecto Asignado', columns='Categoria', values='Base', aggfunc='sum', fill_value=0)
                st.dataframe(pivot.style.format(fmt_money), use_container_width=True)
    
    with t_reg:
        st.write("### Registrar Gasto Manual")
        with st.form("gasto_manual"):
            c1, c2, c3 = st.columns(3)
            f = c1.date_input("Fecha")
            p = c2.text_input("Proveedor")
            conc = c3.text_input("Concepto")
            
            c4, c5 = st.columns(2)
            proy_dest = c4.selectbox("Asignar a:", ["Gasto General"] + df_p['Proyecto'].unique().tolist())
            cat = c5.selectbox("Categoría", ["Materiales", "Transporte", "Alimentación", "Servicios", "Nómina Extra"])
            
            st.divider()
            st.write("💰 **Detalle del Valor**")
            cc1, cc2 = st.columns(2)
            valor_total = cc1.number_input("Valor TOTAL a Pagar", min_value=0.0)
            tiene_iva = cc2.radio("¿Este valor incluye IVA?", ["No (Exento/Régimen Simple)", "Sí (Tiene IVA 19%)"])
            
            if st.form_submit_button("Guardar Gasto"):
                if "Sí" in tiene_iva:
                    base = valor_total / 1.19
                    iva = valor_total - base
                else:
                    base = valor_total
                    iva = 0
                
                sh.worksheet("gastos").append_row([str(f), p, conc, proy_dest, base, iva, valor_total, cat, "Manual"])
                st.success(f"Gasto Guardado. Base: {fmt_money(base)} | IVA: {fmt_money(iva)}")
                st.rerun()
        
        # Botón de Robot
        st.divider()
        pend = df_g[df_g['Proyecto Asignado'] == "POR CLASIFICAR"]
        if not pend.empty:
            st.warning(f"Tienes {len(pend)} facturas del robot pendientes.")
            with st.form("bot_assign"):
                fact_sel = st.selectbox("Factura", pend['Concepto'].unique())
                p_bot = st.selectbox("Mover a:", ["Gasto General"] + df_p['Proyecto'].unique().tolist())
                c_bot_cat = st.selectbox("Cat:", ["Materiales", "Equipos", "Servicios"])
                if st.form_submit_button("Clasificar"):
                    try:
                        cell = sh.worksheet("gastos").find(fact_sel, in_column=3)
                        sh.worksheet("gastos").update_cell(cell.row, 4, p_bot)
                        sh.worksheet("gastos").update_cell(cell.row, 8, c_bot_cat)
                        st.rerun()
                    except: st.error("Error actualizando.")

# --- 4. NÓMINA ---
elif menu == "👥 Equipo & Nómina":
    st.title("Gestión de Personal")
    tn1, tn2 = st.tabs(["💰 Pagos", "👷 Asignar"])
    
    with tn1:
        df_team = df_n.groupby(['Especialista'])[['Valor Pactado', 'Pagado', 'Saldo Debe']].sum().reset_index()
        st.dataframe(df_team.style.format(fmt_money), use_container_width=True)
        
        with st.expander("💸 Pagar"):
            with st.form("pago_nom"):
                deudores = df_n[df_n['Saldo Debe']>0]
                if not deudores.empty:
                    opc = deudores.apply(lambda x: f"{x['Especialista']} | {x['Proyecto']} | Debe: {fmt_money(x['Saldo Debe'])}", axis=1)
                    sel = st.selectbox("Item", opc)
                    monto = st.number_input("Valor", min_value=0.0)
                    if st.form_submit_button("Pagar"):
                        parts = sel.split(" | ")
                        nom_s, proy_s = parts[0], parts[1]
                        all_rows = sh.worksheet("nomina").get_all_values()
                        row_idx = -1
                        for i, row in enumerate(all_rows):
                            if i==0: continue
                            if len(row)>3 and row[1]==nom_s and row[3]==proy_s:
                                row_idx = i+1
                                break
                        if row_idx != -1:
                            curr = float(str(sh.worksheet("nomina").cell(row_idx, 6).value).replace('.','').replace(',','.') or 0)
                            pact = float(str(sh.worksheet("nomina").cell(row_idx, 5).value).replace('.','').replace(',','.') or 0)
                            sh.worksheet("nomina").update_cell(row_idx, 6, curr + monto)
                            sh.worksheet("nomina").update_cell(row_idx, 7, pact - (curr + monto))
                            st.success("Pagado")
                            st.rerun()
                else: st.info("Al día.")

    with tn2:
        with st.form("add_task"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Nombre")
            rol = c2.selectbox("Rol", ["Instalador", "Ayudante", "Ingeniero"])
            proy = st.selectbox("Proyecto", df_p['Proyecto'].unique())
            val = st.number_input("Valor", min_value=0)
            if st.form_submit_button("Asignar"):
                sh.worksheet("nomina").append_row([str(datetime.now().date()), nom, rol, proy, val, 0, val])
                st.success("Asignado")
                st.rerun()
