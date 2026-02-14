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
    .metric-card { background-color: #1e2130; padding: 15px; border-radius: 10px; border-left: 5px solid #4CAF50; }
    .stDataFrame { border: 1px solid #333; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

# --- UTILIDADES DE FORMATO ---
def fmt_money(x):
    """Convierte número a formato visual $ 1.000.000"""
    if pd.isna(x) or x == "": return "$ 0"
    try:
        return "${:,.0f}".format(float(x)).replace(",", ".")
    except:
        return str(x)

def clean_colombian_money(series):
    """Limpia columnas con formato 1.000.000,00 para que Python entienda"""
    s = series.astype(str)
    s = s.str.replace('$', '', regex=False).str.replace(' ', '', regex=False)
    s = s.str.replace('.', '', regex=False) # Quita puntos de miles
    s = s.str.replace(',', '.', regex=False) # Cambia coma decimal a punto
    return pd.to_numeric(s, errors='coerce').fillna(0)

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
                data = ws.get_all_records()
                df = pd.DataFrame(data)
                
                # Asegurar columnas
                for c in cols: 
                    if c not in df.columns: 
                        df[c] = 0 if any(x in c for x in ['Valor', 'Total', 'IVA', 'Monto', 'Pagado', 'Saldo', 'Base']) else ""
                
                # Limpieza numérica
                cols_to_clean = [c for c in df.columns if any(x in c for x in ['Valor', 'Total', 'IVA', 'Monto', 'Pagado', 'Saldo', 'Base'])]
                for c in cols_to_clean:
                    df[c] = clean_colombian_money(df[c])
                return df
            except: return pd.DataFrame(columns=cols)

        df_p = get_df("proyectos", ['ID', 'Cliente', 'Proyecto', 'Total Venta', 'IVA Generado', 'Pagado Cliente', 'Saldo Pendiente', 'Estado', 'Tiene IVA'])
        df_g = get_df("gastos", ['Fecha', 'Proveedor', 'Concepto', 'Proyecto Asignado', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria', 'Origen'])
        df_n = get_df("nomina", ['Fecha', 'Especialista', 'Rol', 'Proyecto', 'Valor Pactado', 'Pagado', 'Saldo Debe'])
        return df_p, df_g, df_n, sh
    except Exception as e:
        st.error(f"Error DB: {e}")
        return None, None, None, None

# --- LÓGICA ROBOT ---
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
                                        # Se asigna "POR CLASIFICAR" inicialmente
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
st.sidebar.title("🚀 SQRapp Pro v3")
if st.sidebar.button("🔄 Sincronizar Todo"):
    logs, count = run_email_sync(sh)
    if count > 0: st.success(f"{count} facturas nuevas.")
    st.rerun()

menu = st.sidebar.radio("Navegación:", 
    ["📊 Inteligencia de Negocio", "💰 Gestión de Proyectos", "📥 Gastos & Compras", "👥 Nómina & Equipo"])

# --- 1. INTELIGENCIA DE NEGOCIO ---
if menu == "📊 Inteligencia de Negocio":
    st.title("📊 Radiografía del Negocio")
    
    k1, k2, k3, k4 = st.columns(4)
    total_ventas = df_p['Total Venta'].sum()
    total_gastos = df_g['Base'].sum()
    total_nomina = df_n['Valor Pactado'].sum()
    utilidad_bruta = total_ventas - (total_gastos + total_nomina)
    
    k1.metric("Ventas Totales", fmt_money(total_ventas))
    k2.metric("Gastos (Sin IVA)", fmt_money(total_gastos))
    k3.metric("Costo Nómina", fmt_money(total_nomina))
    k4.metric("Utilidad Neta", fmt_money(utilidad_bruta), delta=f"{(utilidad_bruta/total_ventas)*100:.1f}% Margen" if total_ventas > 0 else "0%")

    st.markdown("---")
    
    tab1, tab2, tab3 = st.tabs(["📈 Rentabilidad por Proyecto", "👥 Deudas Equipo", "🏛️ Impuestos (IVA)"])
    
    with tab1:
        st.subheader("Rentabilidad por Proyecto")
        
        # Agrupación estricta por nombre de proyecto
        gastos_por_proy = df_g.groupby('Proyecto Asignado')['Base'].sum().reset_index()
        nomina_por_proy = df_n.groupby('Proyecto')['Valor Pactado'].sum().reset_index()
        
        df_rent = df_p[['Proyecto', 'Total Venta']].copy()
        # Merge Left para mantener todos los proyectos activos
        df_rent = df_rent.merge(gastos_por_proy, left_on='Proyecto', right_on='Proyecto Asignado', how='left').fillna(0)
        df_rent = df_rent.merge(nomina_por_proy, left_on='Proyecto', right_on='Proyecto', how='left').fillna(0)
        
        df_rent['Costo Total'] = df_rent['Base'] + df_rent['Valor Pactado']
        df_rent['Utilidad'] = df_rent['Total Venta'] - df_rent['Costo Total']
        df_rent['Margen %'] = np.where(df_rent['Total Venta'] > 0, (df_rent['Utilidad'] / df_rent['Total Venta'] * 100), 0)
        
        df_final = df_rent[['Proyecto', 'Total Venta', 'Base', 'Valor Pactado', 'Utilidad', 'Margen %']]
        df_final.columns = ['Proyecto', 'Venta', 'Gastos (Mat/Var)', 'Mano de Obra', 'Ganancia', 'Margen %']
        
        st.dataframe(df_final.style.format({
            'Venta': fmt_money, 'Gastos (Mat/Var)': fmt_money, 'Mano de Obra': fmt_money, 
            'Ganancia': fmt_money, 'Margen %': '{:.1f}%'
        }).background_gradient(subset=['Ganancia'], cmap='RdYlGn'))

        # Gráfico Resumen
        fig = px.bar(df_rent, x='Proyecto', y=['Utilidad', 'Costo Total'], title="Composición de Proyectos", barmode='stack')
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        st.subheader("Estado de Cuenta del Equipo")
        df_team = df_n.groupby(['Especialista'])[['Valor Pactado', 'Pagado', 'Saldo Debe']].sum().reset_index()
        st.dataframe(df_team.style.format({'Valor Pactado': fmt_money, 'Pagado': fmt_money, 'Saldo Debe': fmt_money}))
        
    with tab3:
        st.subheader("Cruce de IVA")
        iva_gen = df_p['IVA Generado'].sum()
        iva_desc = df_g['IVA Descontable'].sum()
        st.metric("IVA A Pagar DIAN", fmt_money(iva_gen - iva_desc))

# --- 2. PROYECTOS ---
elif menu == "💰 Gestión de Proyectos":
    st.title("Gestión de Proyectos")
    
    # REPORTE RÁPIDO DE PROYECTOS
    col_r1, col_r2 = st.columns([2, 1])
    with col_r1:
        fig_p = px.bar(df_p, x="Proyecto", y=["Pagado Cliente", "Saldo Pendiente"], title="Estado de Cartera (Cobros)")
        st.plotly_chart(fig_p, use_container_width=True)
    with col_r2:
        st.info("💡 Este gráfico muestra cuánto dinero ya entró (Azul) y cuánto falta por cobrar (Rojo).")

    with st.expander("➕ Crear Nuevo Proyecto"):
        with st.form("new_p"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Nombre del Proyecto")
            cli = c2.text_input("Cliente")
            c3, c4 = st.columns(2)
            val = c3.number_input("Valor Venta (Antes de IVA)", min_value=0)
            tiene_iva = c4.radio("¿Aplica IVA?", ["Sí (19%)", "No (Exento)"])
            
            if st.form_submit_button("Crear Proyecto"):
                iva_calc = val * 0.19 if "Sí" in tiene_iva else 0
                total_con_iva = val + iva_calc
                sh.worksheet("proyectos").append_row([
                    int(datetime.now().timestamp()), cli, nom, val, iva_calc, 0, total_con_iva, "Activo", tiene_iva
                ])
                st.success("Proyecto Creado")
                st.rerun()
    
    st.subheader("Cartera (Cuentas por Cobrar)")
    df_cobrar = df_p[['Cliente', 'Proyecto', 'Total Venta', 'Pagado Cliente', 'Saldo Pendiente']].copy()
    st.dataframe(df_cobrar.style.format({
        'Total Venta': fmt_money, 'Pagado Cliente': fmt_money, 'Saldo Pendiente': fmt_money
    }))
    
    with st.form("abono_cliente"):
        st.write("Registrar Abono")
        col1, col2 = st.columns(2)
        proy_abono = col1.selectbox("Proyecto", df_p[df_p['Saldo Pendiente'] > 1]['Proyecto'].unique())
        monto_abono = col2.number_input("Valor Abono", min_value=0.0)
        
        if st.form_submit_button("Registrar Pago"):
            cell = sh.worksheet("proyectos").find(proy_abono)
            if cell:
                row = cell.row
                # Lectura segura de valores
                curr_pagado = float(str(sh.worksheet("proyectos").cell(row, 6).value).replace('.','').replace(',','.') or 0)
                venta_base = float(str(sh.worksheet("proyectos").cell(row, 4).value).replace('.','').replace(',','.') or 0)
                iva_val = float(str(sh.worksheet("proyectos").cell(row, 5).value).replace('.','').replace(',','.') or 0)
                total_real = venta_base + iva_val
                
                nuevo_pagado = curr_pagado + monto_abono
                sh.worksheet("proyectos").update_cell(row, 6, nuevo_pagado)
                sh.worksheet("proyectos").update_cell(row, 7, total_real - nuevo_pagado)
                st.success("Abono registrado")
                st.rerun()

# --- 3. GASTOS ---
elif menu == "📥 Gastos & Compras":
    st.title("Control de Gastos")
    
    # REPORTE RÁPIDO DE GASTOS
    c_g1, c_g2 = st.columns(2)
    with c_g1:
        # Agrupar gastos por categoría
        if not df_g.empty:
            fig_cat = px.pie(df_g, values='Base', names='Categoria', title="Gastos por Categoría", hole=0.4)
            st.plotly_chart(fig_cat, use_container_width=True)
    with c_g2:
        # Agrupar gastos por Proyecto
        if not df_g.empty:
            gastos_clean = df_g[df_g['Proyecto Asignado'] != "POR CLASIFICAR"]
            fig_proy = px.bar(gastos_clean, x='Proyecto Asignado', y='Base', title="Gastos por Proyecto")
            st.plotly_chart(fig_proy, use_container_width=True)

    with st.expander("📝 Registrar Gasto Manual", expanded=False):
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
                sh.worksheet("gastos").append_row([str(fecha), prov, conc, proy, valor, 0, valor, cat, "Manual"])
                st.success("Guardado")
                st.rerun()
    
    st.subheader("🤖 Facturas Detectadas (Pendientes)")
    pendientes = df_g[df_g['Proyecto Asignado'] == "POR CLASIFICAR"]
    
    if not pendientes.empty:
        st.dataframe(pendientes.style.format({
            'Base': fmt_money, 'IVA Descontable': fmt_money, 'Total Gasto': fmt_money
        }))
        
        st.write("### ⚡ Clasificación Rápida")
        with st.form("clasif_express"):
            c_sel, c_proy, c_cat = st.columns(3)
            f_id = c_sel.selectbox("Seleccionar Factura", pendientes['Concepto'].unique())
            p_dest = c_proy.selectbox("Mover a Proyecto:", df_p['Proyecto'].unique().tolist() + ["Gasto General"])
            c_dest = c_cat.selectbox("Categoría:", ["Materiales", "Equipos", "Servicios", "Transporte"])
            
            if st.form_submit_button("Asignar Gasto"):
                # Búsqueda más robusta: Buscar en la columna C (Concepto)
                try:
                    cell = sh.worksheet("gastos").find(f_id, in_column=3)
                    if cell:
                        # Actualizar Proyecto (Col 4) y Categoría (Col 8)
                        sh.worksheet("gastos").update_cell(cell.row, 4, p_dest)
                        sh.worksheet("gastos").update_cell(cell.row, 8, c_dest)
                        st.success(f"Factura asignada a {p_dest}")
                        st.rerun()
                    else:
                        st.error("No se encontró la factura en la hoja.")
                except Exception as e:
                    st.error(f"Error al actualizar: {e}")
    else: 
        st.info("✅ No tienes facturas pendientes de clasificar.")
    
    st.markdown("### Historial Completo")
    st.dataframe(df_g.style.format({
        'Base': fmt_money, 'IVA Descontable': fmt_money, 'Total Gasto': fmt_money
    }))

# --- 4. NÓMINA ---
elif menu == "👥 Nómina & Equipo":
    st.title("Gestión de Equipo")
    
    # REPORTE DE NÓMINA
    if not df_n.empty:
        fig_nom = px.bar(df_n, x="Especialista", y=["Pagado", "Saldo Debe"], title="Pagos vs Deuda por Persona", barmode='group')
        st.plotly_chart(fig_nom, use_container_width=True)
    
    c1, c2 = st.columns(2)
    with c1.form("asignar_tarea"):
        st.write("👷 Asignar Tarea")
        nom = st.text_input("Nombre")
        rol = st.selectbox("Rol", ["Instalador", "Ingeniero", "Ayudante", "Trafficker", "Diseñador"])
        proy = st.selectbox("Proyecto", df_p['Proyecto'].unique())
        val = st.number_input("Valor", min_value=0)
        if st.form_submit_button("Asignar"):
            sh.worksheet("nomina").append_row([str(datetime.now().date()), nom, rol, proy, val, 0, val])
            st.rerun()

    with c2.form("pagar_nomina"):
        st.write("💸 Pagar")
        deudores = df_n[df_n['Saldo Debe'] > 0]
        if not deudores.empty:
            opciones = deudores.apply(lambda x: f"{x['Especialista']} - {x['Proyecto']} ({fmt_money(x['Saldo Debe'])})", axis=1)
            seleccion = st.selectbox("Seleccionar Pago", opciones)
            monto = st.number_input("Valor a Abonar", min_value=0.0)
            
            if st.form_submit_button("Pagar"):
                nombre_sel = seleccion.split(" - ")[0]
                proy_sel = seleccion.split(" - ")[1].split(" ($")[0]
                
                # Lógica de búsqueda manual para evitar errores de find()
                all_data = sh.worksheet("nomina").get_all_records()
                row_idx = -1
                for i, row in enumerate(all_data):
                    if row['Especialista'] == nombre_sel and row['Proyecto'] == proy_sel:
                        row_idx = i + 2
                        break
                if row_idx > 0:
                    curr_pagado = float(str(sh.worksheet("nomina").cell(row_idx, 6).value).replace('.','').replace(',','.'))
                    curr_pactado = float(str(sh.worksheet("nomina").cell(row_idx, 5).value).replace('.','').replace(',','.'))
                    nuevo_pagado = curr_pagado + monto
                    sh.worksheet("nomina").update_cell(row_idx, 6, nuevo_pagado)
                    sh.worksheet("nomina").update_cell(row_idx, 7, curr_pactado - nuevo_pagado)
                    st.success("Pago registrado")
                    st.rerun()
        else: st.info("Paz y salvo.")
    
    st.dataframe(df_n.style.format({
        'Valor Pactado': fmt_money, 'Pagado': fmt_money, 'Saldo Debe': fmt_money
    }))
