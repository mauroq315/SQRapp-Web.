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

# --- CONFIGURACIÓN VISUAL ---
st.set_page_config(page_title="SQRapp", layout="wide", page_icon="🏗️")

# --- ESTILOS MODERNOS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    /* Tabs grandes y fáciles de tocar */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { 
        height: 60px; 
        background-color: #1e2130; 
        border-radius: 10px; 
        color: white; 
        font-size: 18px;
        font-weight: bold;
        padding: 0 20px;
    }
    .stTabs [aria-selected="true"] { 
        background-color: #4CAF50; 
        color: white; 
        border: 2px solid #81C784;
    }
    /* Tarjetas de métricas */
    div[data-testid="metric-container"] {
        background-color: #262730;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #4CAF50;
    }
    </style>
""", unsafe_allow_html=True)

# --- FUNCIONES DE SOPORTE (Iguales a la versión robusta) ---
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
                df = df.loc[:, ~df.columns.duplicated()]
                for col in required_cols:
                    if col not in df.columns: df[col] = ""
                df = df[required_cols].copy()
                cols_num = [c for c in df.columns if any(x in c for x in ['Valor', 'Total', 'IVA', 'Monto', 'Pagado', 'Saldo', 'Base'])]
                for c in cols_num: df[c] = clean_colombian_money(df[c])
                return df
            except: return pd.DataFrame(columns=required_cols)

        cols_p = ['ID', 'Cliente', 'Proyecto', 'Total Venta', 'IVA Generado', 'Pagado Cliente', 'Saldo Pendiente', 'Estado', 'Tiene IVA']
        cols_g = ['Fecha', 'Proyecto Asignado', 'Proveedor', 'Concepto', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria', 'Origen']
        cols_n = ['Fecha', 'Proyecto', 'Especialista', 'Rol', 'Valor Pactado', 'Pagado', 'Saldo Debe']

        df_p = get_df_robust("proyectos", cols_p)
        df_g = get_df_robust("gastos", cols_g)
        df_n = get_df_robust("nomina", cols_n)
        
        if not df_p.empty: df_p['Key'] = clean_text_key(df_p['Proyecto'])
        else: df_p['Key'] = ""
        if not df_g.empty: df_g['Key'] = clean_text_key(df_g['Proyecto Asignado'])
        else: df_g['Key'] = ""
        if not df_n.empty: df_n['Key'] = clean_text_key(df_n['Proyecto'])
        else: df_n['Key'] = ""
        
        return df_p, df_g, df_n, sh
    except: return None, None, None, None

# --- ROBOT SIMPLIFICADO ---
def run_email_sync(sheet_instance):
    # Lógica de sincronización (simplificada para visualización)
    try:
        # ... (código de email igual al anterior) ...
        return 0 # Placeholder
    except: return 0

# --- INTERFAZ PRINCIPAL ---
df_p, df_g, df_n, sh = load_data()

# BARRA LATERAL SIMPLE
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/25/25694.png", width=50)
st.sidebar.title("Menú Principal")
menu = st.sidebar.radio("", ["🏠 Inicio", "🏗️ Proyectos", "💸 Gastos", "👷 Nómina"])

st.sidebar.divider()
if st.sidebar.button("🔄 Actualizar Datos"):
    st.cache_data.clear()
    st.rerun()

# --- 1. INICIO ---
if menu == "🏠 Inicio":
    st.title("Bienvenido a SQRapp")
    st.write("Aquí tienes el resumen de tu empresa hoy.")
    
    # Tarjetas Grandes
    c1, c2, c3 = st.columns(3)
    ventas = df_p['Total Venta'].sum()
    gastos = df_g['Base'].sum()
    utilidad = ventas - gastos
    
    c1.metric("💰 Total Vendido", fmt_money(ventas))
    c2.metric("📉 Total Gastado", fmt_money(gastos))
    c3.metric("📈 Ganancia Neta", fmt_money(utilidad))
    
    st.divider()
    st.subheader("📊 ¿Cómo va la plata?")
    
    # Gráfico simple
    if not df_p.empty:
        # Preparar datos
        p_resumen = df_p[['Proyecto', 'Total Venta']].copy()
        p_resumen['Gastos'] = 0
        
        # Sumar gastos por proyecto
        if not df_g.empty:
            g_sum = df_g.groupby('Key')['Base'].sum()
            p_resumen['Key'] = clean_text_key(p_resumen['Proyecto'])
            p_resumen['Gastos'] = p_resumen['Key'].map(g_sum).fillna(0)
        
        # Graficar
        fig = px.bar(p_resumen, x='Proyecto', y=['Total Venta', 'Gastos'], barmode='group', title="Ventas vs Gastos por Proyecto")
        st.plotly_chart(fig, use_container_width=True)

# --- 2. PROYECTOS ---
elif menu == "🏗️ Proyectos":
    st.title("Mis Proyectos")
    
    # PESTAÑAS CLARAS: VER vs CREAR
    tab_ver, tab_crear, tab_detalle = st.tabs(["📂 VER LISTADO", "➕ CREAR NUEVO", "🔍 DETALLE 360°"])
    
    with tab_ver:
        st.dataframe(df_p[['Proyecto', 'Cliente', 'Total Venta', 'Saldo Pendiente']].style.format(fmt_money), use_container_width=True)
    
    with tab_crear:
        st.write("### 📝 Registrar Nueva Obra")
        with st.form("form_proyecto"):
            col1, col2 = st.columns(2)
            nombre = col1.text_input("Nombre de la Obra (Ej: Camaras Edificio X)")
            cliente = col2.text_input("Nombre del Cliente")
            
            col3, col4 = st.columns(2)
            valor = col3.number_input("Valor del Contrato (Antes de IVA)", min_value=0)
            iva_opcion = col4.radio("¿Lleva IVA?", ["Sí (19%)", "No (0%)"], horizontal=True)
            
            if st.form_submit_button("🚀 Crear Proyecto Ahora"):
                iva = valor * 0.19 if "Sí" in iva_opcion else 0
                total = valor + iva
                sh.worksheet("proyectos").append_row([
                    int(datetime.now().timestamp()), cliente, nombre, valor, iva, 0, total, "Activo", "Sí" if "Sí" in iva_opcion else "No"
                ])
                st.success(f"¡Proyecto {nombre} creado con éxito!")
                st.balloons()
                st.rerun()

    with tab_detalle:
        st.info("Selecciona un proyecto para ver sus cuentas específicas.")
        sel_proy = st.selectbox("Seleccionar Proyecto:", df_p['Proyecto'].unique())
        
        # Filtrar datos
        key = clean_text_key(pd.Series([sel_proy]))[0]
        mis_gastos = df_g[df_g['Key'] == key]
        
        st.write(f"**Gastos de: {sel_proy}**")
        if not mis_gastos.empty:
            st.dataframe(mis_gastos[['Fecha', 'Proveedor', 'Concepto', 'Total Gasto']].style.format({'Total Gasto': fmt_money}), use_container_width=True)
            st.metric("Total Gastado en esta obra", fmt_money(mis_gastos['Total Gasto'].sum()))
        else:
            st.warning("No hay gastos registrados en esta obra aún.")

# --- 3. GASTOS ---
elif menu == "💸 Gastos":
    st.title("Control de Gastos")
    
    tab_reg, tab_hist = st.tabs(["📝 REGISTRAR GASTO", "📊 VER HISTORIAL"])
    
    with tab_reg:
        st.write("### 🧾 Nuevo Gasto o Compra")
        with st.form("form_gasto"):
            # 1. ¿Para quién es el gasto?
            st.markdown("#### 1. ¿A qué proyecto pertenece?")
            lista_proyectos = ["Gasto General (Oficina/Varios)"] + df_p['Proyecto'].unique().tolist()
            proyecto_destino = st.selectbox("Selecciona el Proyecto:", lista_proyectos)
            
            st.divider()
            
            # 2. Detalles
            st.markdown("#### 2. Datos de la Factura")
            c1, c2, c3 = st.columns(3)
            fecha = c1.date_input("Fecha")
            proveedor = c2.text_input("Proveedor (Ej: Homecenter)")
            concepto = c3.text_input("¿Qué se compró? (Ej: Cable UTP)")
            
            # 3. Plata
            st.divider()
            st.markdown("#### 3. ¿Cuánto costó?")
            cc1, cc2 = st.columns(2)
            valor_pagado = cc1.number_input("Valor TOTAL Pagado", min_value=0)
            tiene_iva = cc2.checkbox("¿La factura tiene IVA discriminado?")
            
            categoria = st.selectbox("Categoría", ["Materiales", "Transporte", "Alimentación", "Mano de Obra Extra", "Servicios"])
            
            if st.form_submit_button("💾 Guardar Gasto"):
                base = valor_pagado / 1.19 if tiene_iva else valor_pagado
                iva = valor_pagado - base if tiene_iva else 0
                
                sh.worksheet("gastos").append_row([
                    str(fecha), proyecto_destino, proveedor, concepto, base, iva, valor_pagado, categoria, "Manual"
                ])
                st.success("¡Gasto guardado correctamente!")
                st.rerun()
    
    with tab_hist:
        st.dataframe(df_g[['Fecha', 'Proyecto Asignado', 'Proveedor', 'Concepto', 'Total Gasto']].style.format({'Total Gasto': fmt_money}), use_container_width=True)

# --- 4. NÓMINA ---
elif menu == "👷 Nómina":
    st.title("Equipo de Trabajo")
    
    tab_pagar, tab_asignar = st.tabs(["💰 PAGAR A GENTE", "👷 ASIGNAR TRABAJO"])
    
    with tab_asignar:
        with st.form("form_nomina"):
            st.write("### Asignar Tarea a Empleado")
            c1, c2 = st.columns(2)
            nombre = c1.text_input("Nombre del Trabajador")
            rol = c2.selectbox("Rol", ["Técnico", "Ayudante", "Ingeniero"])
            
            proy = st.selectbox("¿En qué proyecto va a trabajar?", df_p['Proyecto'].unique())
            valor = st.number_input("¿Cuánto se le va a pagar?", min_value=0)
            
            if st.form_submit_button("Asignar"):
                sh.worksheet("nomina").append_row([str(datetime.now().date()), nombre, rol, proy, valor, 0, valor])
                st.success("Asignado correctamente")
                st.rerun()
                
    with tab_pagar:
        st.write("### Estado de Cuentas")
        resumen_nomina = df_n.groupby(['Especialista'])[['Valor Pactado', 'Pagado', 'Saldo Debe']].sum().reset_index()
        st.dataframe(resumen_nomina.style.format(fmt_money), use_container_width=True)
