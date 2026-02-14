import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
from datetime import datetime
import numpy as np

# --- CONFIGURACIÓN DE LA APP ---
st.set_page_config(page_title="SQRapp Enterprise", layout="wide", page_icon="🏢")

# --- ESTILOS CSS PROFESIONALES ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    h1, h2, h3 { color: #e0e0e0; }
    .stMetric { background-color: #1a1c24; border: 1px solid #333; padding: 15px; border-radius: 10px; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { height: 50px; background-color: #1a1c24; border-radius: 5px; color: white; border: 1px solid #333; }
    .stTabs [aria-selected="true"] { background-color: #2196F3; color: white; border: none; }
    .highlight-box { background-color: #263238; padding: 20px; border-radius: 10px; border-left: 5px solid #00bcd4; margin-bottom: 20px; }
    </style>
""", unsafe_allow_html=True)

# --- FUNCIONES DE LIMPIEZA DE DATOS (CRÍTICO) ---
def clean_money_value(val):
    """Limpia cualquier formato de moneda y retorna un float puro."""
    if pd.isna(val) or str(val).strip() == "": return 0.0
    s = str(val).strip().replace('$', '').replace(' ', '')
    # Lógica para Colombia: 1.000.000,00 o 1.000.000
    if '.' in s and ',' in s: s = s.replace('.', '').replace(',', '.')
    elif '.' in s: 
        if len(s.split('.')[-1]) == 3: s = s.replace('.', '') # Es mil
    elif ',' in s: s = s.replace(',', '.') # Es decimal
    try: return float(s)
    except: return 0.0

def fmt_money(x):
    return "${:,.0f}".format(x).replace(",", ".")

# --- CONEXIÓN A GOOGLE SHEETS ---
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
        
        def get_df(name):
            ws = sh.worksheet(name)
            data = ws.get_all_values()
            if len(data) < 2: return pd.DataFrame(), ws
            df = pd.DataFrame(data[1:], columns=data[0])
            return df, ws

        df_p, ws_p = get_df("proyectos")
        df_g, ws_g = get_df("gastos")
        df_n, ws_n = get_df("nomina")

        # --- PROCESAMIENTO DE DATOS ---
        # 1. Proyectos
        if not df_p.empty:
            df_p['Total Venta Num'] = df_p['Total Venta'].apply(clean_money_value)
            df_p['IVA Generado Num'] = df_p['IVA Generado'].apply(clean_money_value) # Asumiendo columna existe
            df_p['Pagado Cliente Num'] = df_p['Pagado Cliente'].apply(clean_money_value)
            df_p['Saldo Pendiente'] = df_p['Total Venta Num'] - df_p['Pagado Cliente Num']
        
        # 2. Gastos
        if not df_g.empty:
            df_g['Base Num'] = df_g['Base'].apply(clean_money_value)
            df_g['IVA Descontable Num'] = df_g['IVA Descontable'].apply(clean_money_value)
            df_g['Total Gasto Num'] = df_g['Total Gasto'].apply(clean_money_value)
            # Si Total es 0 pero Base tiene dato, recalcular
            mask = (df_g['Total Gasto Num'] == 0) & (df_g['Base Num'] > 0)
            df_g.loc[mask, 'Total Gasto Num'] = df_g.loc[mask, 'Base Num'] + df_g.loc[mask, 'IVA Descontable Num']

        # 3. Nómina
        if not df_n.empty:
            df_n['Valor Pactado Num'] = df_n['Valor Pactado'].apply(clean_money_value)
            df_n['Pagado Num'] = df_n['Pagado'].apply(clean_money_value)
            df_n['Saldo Debe'] = df_n['Valor Pactado Num'] - df_n['Pagado Num']

        return df_p, df_g, df_n, sh
    except Exception as e:
        st.error(f"Error crítico cargando datos: {e}")
        return None, None, None, None

def save_changes(sh, sheet_name, df_edited):
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
        df_str = df_edited.astype(str)
        data = [df_str.columns.tolist()] + df_str.values.tolist()
        ws.update(data)
        st.toast(f"✅ {sheet_name.upper()} actualizado correctamente.")
        st.rerun()
    except Exception as e:
        st.error(f"Error guardando: {e}")

# --- INTERFAZ GRÁFICA ---
df_p, df_g, df_n, sh = load_data()

st.sidebar.image("https://cdn-icons-png.flaticon.com/512/900/900782.png", width=60)
st.sidebar.title("SQRapp Enterprise")

menu = st.sidebar.radio("Navegación", [
    "📊 Dashboard General",
    "🏗️ Gestión de Proyectos",
    "💸 Control de Gastos",
    "👥 Nómina & Equipo",
    "🏛️ Impuestos (IVA)"
])

if st.sidebar.button("🔄 Sincronizar Datos"):
    st.rerun()

# ==========================================
# 1. DASHBOARD GENERAL
# ==========================================
if menu == "📊 Dashboard General":
    st.title("📊 Visión Global de la Empresa")
    
    if not df_p.empty:
        ventas_totales = df_p['Total Venta Num'].sum()
        cobrado = df_p['Pagado Cliente Num'].sum()
        por_cobrar = ventas_totales - cobrado
        
        gastos_totales = df_g['Total Gasto Num'].sum() if not df_g.empty else 0
        nomina_total = df_n['Valor Pactado Num'].sum() if not df_n.empty else 0
        
        utilidad_bruta = ventas_totales - (gastos_totales + nomina_total)
        margen = (utilidad_bruta / ventas_totales * 100) if ventas_totales > 0 else 0

        # KPIs Principales
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Ventas Totales", fmt_money(ventas_totales), f"Por cobrar: {fmt_money(por_cobrar)}")
        k2.metric("Gastos Operativos", fmt_money(gastos_totales))
        k3.metric("Nómina", fmt_money(nomina_total))
        k4.metric("Utilidad Real", fmt_money(utilidad_bruta), f"Margen: {margen:.1f}%")

        st.divider()

        # Gráficos
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("💰 Rentabilidad por Proyecto")
            # Preparar datos
            p_data = df_p[['Proyecto', 'Total Venta Num']].copy()
            g_data = df_g.groupby('Proyecto Asignado')['Total Gasto Num'].sum().reset_index() if not df_g.empty else pd.DataFrame(columns=['Proyecto Asignado', 'Total Gasto Num'])
            n_data = df_n.groupby('Proyecto')['Valor Pactado Num'].sum().reset_index() if not df_n.empty else pd.DataFrame(columns=['Proyecto', 'Valor Pactado Num'])
            
            merged = p_data.merge(g_data, left_on='Proyecto', right_on='Proyecto Asignado', how='left')
            merged = merged.merge(n_data, on='Proyecto', how='left').fillna(0)
            merged['Costo Total'] = merged['Total Gasto Num'] + merged['Valor Pactado Num']
            merged['Utilidad'] = merged['Total Venta Num'] - merged['Costo Total']
            
            fig = px.bar(merged, x='Proyecto', y=['Utilidad', 'Costo Total'], title="Utilidad vs Costos", barmode='stack')
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            st.subheader("📉 Gastos por Categoría")
            if not df_g.empty:
                fig2 = px.pie(df_g, values='Total Gasto Num', names='Categoria', hole=0.4)
                st.plotly_chart(fig2, use_container_width=True)

# ==========================================
# 2. GESTIÓN DE PROYECTOS (360 + CRUD)
# ==========================================
elif menu == "🏗️ Gestión de Proyectos":
    st.title("🏗️ Proyectos")
    
    tab_360, tab_create, tab_edit = st.tabs(["🔍 VISTA 360° (Detalle)", "➕ CREAR NUEVO", "📝 EDITAR MAESTRO (CRUD)"])

    # --- TAB 1: VISTA 360 ---
    with tab_360:
        st.markdown("<div class='highlight-box'>Selecciona un proyecto para ver su radiografía completa: Finanzas, Gastos y Personal.</div>", unsafe_allow_html=True)
        
        lista_proyectos = df_p['Proyecto'].unique().tolist() if not df_p.empty else []
        seleccion = st.selectbox("Seleccionar Proyecto:", lista_proyectos)
        
        if seleccion:
            # Filtrar datos
            p_info = df_p[df_p['Proyecto'] == seleccion].iloc[0]
            g_info = df_g[df_g['Proyecto Asignado'] == seleccion] if not df_g.empty else pd.DataFrame()
            n_info = df_n[df_n['Proyecto'] == seleccion] if not df_n.empty else pd.DataFrame()
            
            # Métricas del Proyecto
            v_proy = p_info['Total Venta Num']
            g_proy = g_info['Total Gasto Num'].sum() if not g_info.empty else 0
            n_proy = n_info['Valor Pactado Num'].sum() if not n_info.empty else 0
            u_proy = v_proy - (g_proy + n_proy)
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Venta Proyecto", fmt_money(v_proy))
            col2.metric("Costos (Gastos+Nómina)", fmt_money(g_proy + n_proy))
            col3.metric("Ganancia Neta", fmt_money(u_proy), delta_color="normal")
            
            # Tablas de detalle
            c_t1, c_t2 = st.columns(2)
            with c_t1:
                st.write("**📥 Gastos de este proyecto**")
                if not g_info.empty:
                    st.dataframe(g_info[['Fecha', 'Proveedor', 'Concepto', 'Total Gasto Num']].style.format({'Total Gasto Num': fmt_money}), use_container_width=True)
                else: st.info("Sin gastos.")
            
            with c_t2:
                st.write("**👷 Equipo en este proyecto**")
                if not n_info.empty:
                    st.dataframe(n_info[['Especialista', 'Rol', 'Valor Pactado Num']].style.format({'Valor Pactado Num': fmt_money}), use_container_width=True)
                else: st.info("Sin personal asignado.")

    # --- TAB 2: CREAR ---
    with tab_create:
        st.write("### 🆕 Registrar Nuevo Contrato")
        with st.form("form_proy"):
            c1, c2 = st.columns(2)
            nombre = c1.text_input("Nombre del Proyecto")
            cliente = c2.text_input("Cliente")
            valor = st.number_input("Valor Venta (Antes de IVA)", min_value=0.0)
            iva_bool = st.checkbox("¿Lleva IVA (19%)?")
            
            if st.form_submit_button("🚀 Crear Proyecto"):
                iva = valor * 0.19 if iva_bool else 0
                total = valor + iva
                sh.worksheet("proyectos").append_row([
                    str(datetime.now().date()), cliente, nombre, valor, iva, 0, total, "Activo", "Sí" if iva_bool else "No"
                ])
                st.success(f"Proyecto {nombre} creado.")
                st.rerun()

    # --- TAB 3: EDITAR (CRUD) ---
    with tab_edit:
        st.warning("⚠️ Modo Edición: Modifica valores o elimina filas seleccionándolas y pulsando 'Supr'.")
        if not df_p.empty:
            # Columnas visibles para editar
            cols_edit = ['Fecha', 'Cliente', 'Proyecto', 'Total Venta', 'IVA Generado', 'Pagado Cliente', 'Estado']
            # Asegurar que existan
            for c in cols_edit: 
                if c not in df_p.columns: df_p[c] = ""
            
            edited_p = st.data_editor(df_p[cols_edit], num_rows="dynamic", use_container_width=True, key="edit_p_master")
            
            if st.button("💾 GUARDAR CAMBIOS PROYECTOS", type="primary"):
                save_changes(sh, "proyectos", edited_p)

# ==========================================
# 3. CONTROL DE GASTOS
# ==========================================
elif menu == "💸 Control de Gastos":
    st.title("💸 Centro de Costos")
    
    tab_add, tab_list = st.tabs(["📝 REGISTRAR GASTO", "📂 HISTORIAL & EDICIÓN"])
    
    with tab_add:
        st.write("### Registrar Compra")
        with st.form("form_gasto"):
            c1, c2 = st.columns(2)
            # Lista de proyectos + General
            opciones_p = ["Gasto General"] + (df_p['Proyecto'].unique().tolist() if not df_p.empty else [])
            proy_sel = c1.selectbox("¿A qué proyecto se carga?", opciones_p)
            prov = c2.text_input("Proveedor (Ej: Homecenter)")
            
            c3, c4 = st.columns(2)
            conc = c3.text_input("Concepto (Detalle)")
            cat = c4.selectbox("Categoría", ["Materiales", "Transporte", "Alimentación", "Mano de Obra Extra", "Servicios", "Equipos"])
            
            st.divider()
            cc1, cc2 = st.columns(2)
            monto_total = cc1.number_input("Valor TOTAL a Pagar", min_value=0.0)
            tiene_iva = cc2.radio("Impuestos", ["Régimen Simplificado (Sin IVA)", "Factura Electrónica (Con IVA 19%)"])
            
            if st.form_submit_button("💾 Guardar Gasto"):
                if "Con IVA" in tiene_iva:
                    base = monto_total / 1.19
                    iva = monto_total - base
                else:
                    base = monto_total
                    iva = 0
                
                sh.worksheet("gastos").append_row([
                    str(datetime.now().date()), proy_sel, prov, conc, base, iva, monto_total, cat, "Manual"
                ])
                st.success("Gasto registrado exitosamente.")
                st.rerun()

    with tab_list:
        st.write("### 📋 Base de Datos de Gastos")
        st.info("Aquí puedes corregir montos, cambiar categorías o reasignar proyectos.")
        
        if not df_g.empty:
            cols_g_edit = ['Fecha', 'Proyecto Asignado', 'Proveedor', 'Concepto', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria']
            for c in cols_g_edit:
                 if c not in df_g.columns: df_g[c] = ""

            edited_g = st.data_editor(
                df_g[cols_g_edit],
                num_rows="dynamic",
                use_container_width=True,
                key="edit_g_master"
            )
            
            if st.button("💾 GUARDAR CAMBIOS GASTOS", type="primary"):
                save_changes(sh, "gastos", edited_g)

# ==========================================
# 4. NÓMINA
# ==========================================
elif menu == "👥 Nómina & Equipo":
    st.title("👥 Gestión de Personal")
    
    tab_n1, tab_n2 = st.tabs(["👷 ASIGNAR TRABAJO", "💰 PAGOS Y SALDOS"])
    
    with tab_n1:
        with st.form("form_nom"):
            c1, c2 = st.columns(2)
            nombre = c1.text_input("Nombre del Colaborador")
            rol = c2.selectbox("Cargo", ["Oficial", "Ayudante", "Ingeniero", "Contratista"])
            
            c3, c4 = st.columns(2)
            proy = c3.selectbox("Proyecto Asignado", df_p['Proyecto'].unique() if not df_p.empty else [])
            valor = c4.number_input("Valor Pactado", min_value=0.0)
            
            if st.form_submit_button("Asignar"):
                sh.worksheet("nomina").append_row([
                    str(datetime.now().date()), proy, nombre, rol, valor, 0, valor
                ])
                st.success("Asignación guardada.")
                st.rerun()
    
    with tab_n2:
        st.write("### Control de Pagos")
        if not df_n.empty:
            cols_n_edit = ['Fecha', 'Proyecto', 'Especialista', 'Rol', 'Valor Pactado', 'Pagado']
            for c in cols_n_edit:
                if c not in df_n.columns: df_n[c] = ""
            
            edited_n = st.data_editor(
                df_n[cols_n_edit],
                num_rows="dynamic",
                use_container_width=True,
                key="edit_n_master"
            )
            
            if st.button("💾 GUARDAR CAMBIOS NÓMINA", type="primary"):
                save_changes(sh, "nomina", edited_n)

# ==========================================
# 5. IMPUESTOS (IVA)
# ==========================================
elif menu == "🏛️ Impuestos (IVA)":
    st.title("🏛️ Balance Fiscal (IVA)")
    st.markdown("Aquí puedes ver el cruce entre el IVA que has cobrado y el que has pagado.")
    
    if not df_p.empty and not df_g.empty:
        iva_generado = df_p['IVA Generado Num'].sum()
        iva_descontable = df_g['IVA Descontable Num'].sum()
        balance = iva_generado - iva_descontable
        
        c1, c2, c3 = st.columns(3)
        
        c1.error(f"IVA Generado (Debes): {fmt_money(iva_generado)}")
        c2.success(f"IVA Descontable (Tienes): {fmt_money(iva_descontable)}")
        
        if balance > 0:
            c3.warning(f"A PAGAR A DIAN: {fmt_money(balance)}")
        else:
            c3.info(f"SALDO A FAVOR: {fmt_money(abs(balance))}")
        
        st.write("### Detalle de Facturas con IVA Descontable")
        con_iva = df_g[df_g['IVA Descontable Num'] > 0]
        st.dataframe(con_iva[['Fecha', 'Proveedor', 'Concepto', 'Base Num', 'IVA Descontable Num']].style.format({'Base Num': fmt_money, 'IVA Descontable Num': fmt_money}), use_container_width=True)
