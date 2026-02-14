import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
from datetime import datetime
import numpy as np

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SQRapp Control", layout="wide", page_icon="🛠️")

# --- ESTILOS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stButton button { width: 100%; border-radius: 5px; font-weight: bold; }
    div[data-testid="stMetricValue"] { font-size: 24px; }
    .save-btn { background-color: #4CAF50 !important; color: white !important; }
    </style>
""", unsafe_allow_html=True)

# --- FUNCIONES DE LIMPIEZA ROBUSTA ---
def clean_money_value(val):
    """Convierte cualquier formato de moneda (texto o numero) a float puro"""
    if pd.isna(val) or str(val).strip() == "": return 0.0
    s = str(val).strip()
    # Quitar símbolos de moneda y espacios
    s = s.replace('$', '').replace(' ', '')
    # Lógica Colombia: 
    # 1. Si hay puntos, asumo que son miles y los quito (2.000.000 -> 2000000)
    # 2. Si hay comas, asumo que son decimales y los vuelvo puntos (50,5 -> 50.5)
    if '.' in s and ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif '.' in s:
        # Caso peligroso: ¿Es 2.000 (dos mil) o 2.5 (dos punto cinco)?
        # Asumimos formato COP: Puntos son miles.
        if len(s.split('.')[-1]) == 3: # Es mil (ej: 2.000)
            s = s.replace('.', '')
        else: # Es decimal (ej: 2.5)
            pass 
    elif ',' in s:
        s = s.replace(',', '.')
    
    try:
        return float(s)
    except:
        return 0.0

def fmt_money(x):
    return "${:,.0f}".format(x).replace(",", ".")

# --- CONEXIÓN ---
def get_client():
    try:
        secrets = st.secrets["gcp_service_account"]
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(secrets, scopes=scopes)
        return gspread.authorize(creds)
    except: return None

# --- CARGA DE DATOS ---
def load_data():
    client = get_client()
    if not client: return None, None, None, None
    try:
        sh = client.open("APP_SQR")
        
        def get_df(name, cols):
            ws = sh.worksheet(name)
            data = ws.get_all_values()
            if len(data) < 2: return pd.DataFrame(columns=cols)
            df = pd.DataFrame(data[1:], columns=data[0])
            # Ajustar columnas si faltan o sobran
            df = df.loc[:, ~df.columns.duplicated()]
            for c in cols: 
                if c not in df.columns: df[c] = ""
            return df[cols] # Retornar solo columnas esperadas

        # Definimos columnas ESTRICTAS para evitar errores
        cols_p = ['Fecha', 'Cliente', 'Proyecto', 'Total Venta', 'IVA', 'Pagado', 'Estado']
        cols_g = ['Fecha', 'Proyecto Asignado', 'Proveedor', 'Concepto', 'Base', 'IVA', 'Categoria']
        cols_n = ['Fecha', 'Proyecto', 'Especialista', 'Rol', 'Valor Pactado', 'Pagado']

        df_p = get_df("proyectos", cols_p)
        df_g = get_df("gastos", cols_g)
        df_n = get_df("nomina", cols_n)

        # --- LIMPIEZA Y CÁLCULO AUTOMÁTICO ---
        # Proyectos
        df_p['Total Venta'] = df_p['Total Venta'].apply(clean_money_value)
        df_p['IVA'] = df_p['IVA'].apply(clean_money_value)
        df_p['Pagado'] = df_p['Pagado'].apply(clean_money_value)
        df_p['Total con IVA'] = df_p['Total Venta'] + df_p['IVA']
        df_p['Saldo'] = df_p['Total con IVA'] - df_p['Pagado']

        # Gastos
        df_g['Base'] = df_g['Base'].apply(clean_money_value)
        df_g['IVA'] = df_g['IVA'].apply(clean_money_value)
        df_g['Total Gasto'] = df_g['Base'] + df_g['IVA'] # Calculado aquí, no leído

        # Nómina
        df_n['Valor Pactado'] = df_n['Valor Pactado'].apply(clean_money_value)
        df_n['Pagado'] = df_n['Pagado'].apply(clean_money_value)
        df_n['Saldo'] = df_n['Valor Pactado'] - df_n['Pagado']

        return df_p, df_g, df_n, sh
    except Exception as e:
        st.error(f"Error cargando datos: {e}")
        return None, None, None, None

# --- FUNCIÓN DE GUARDADO (CRUD) ---
def save_to_sheet(sh, sheet_name, df):
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear() # Borrar todo
        # Preparar datos para subir (convertir a string para evitar problemas de formato)
        df_str = df.astype(str)
        # Subir header + datos
        data_to_upload = [df_str.columns.tolist()] + df_str.values.tolist()
        ws.update(data_to_upload)
        st.toast(f"✅ Cambios guardados en {sheet_name} exitosamente!")
        return True
    except Exception as e:
        st.error(f"Error guardando: {e}")
        return False

# --- UI ---
df_p, df_g, df_n, sh = load_data()

st.sidebar.title("SQRapp Control 🛠️")
menu = st.sidebar.radio("Ir a:", ["🏠 Dashboard", "🏗️ Proyectos (Editar)", "💸 Gastos (Editar)", "👥 Nómina (Editar)"])

if st.sidebar.button("🔄 Recargar Datos"):
    st.rerun()

# --- 1. DASHBOARD ---
if menu == "🏠 Dashboard":
    st.title("Resumen Ejecutivo")
    
    # KPIs
    ventas = df_p['Total Venta'].sum()
    gastos = df_g['Base'].sum()
    utilidad = ventas - gastos
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Ventas (Subtotal)", fmt_money(ventas))
    c2.metric("Gastos (Subtotal)", fmt_money(gastos))
    c3.metric("Utilidad Bruta", fmt_money(utilidad), delta_color="normal")
    
    st.divider()
    
    # Selector de Proyecto para ver detalle rápido
    st.subheader("🔍 Lupa por Proyecto")
    proy_sel = st.selectbox("Selecciona Proyecto:", ["Todos"] + df_p['Proyecto'].unique().tolist())
    
    if proy_sel != "Todos":
        # Filtrar
        p_data = df_p[df_p['Proyecto'] == proy_sel]
        g_data = df_g[df_g['Proyecto Asignado'] == proy_sel]
        
        # Métricas del proyecto
        v_p = p_data['Total Venta'].sum()
        g_p = g_data['Base'].sum()
        
        col1, col2 = st.columns(2)
        col1.info(f"Vendido: {fmt_money(v_p)}")
        col2.warning(f"Gastado: {fmt_money(g_p)}")
        
        st.write("📋 **Últimos Gastos de este proyecto:**")
        st.dataframe(g_data[['Fecha', 'Proveedor', 'Concepto', 'Total Gasto']])
    else:
        st.info("Selecciona un proyecto arriba para ver sus cuentas específicas.")

# --- 2. PROYECTOS (CRUD) ---
elif menu == "🏗️ Proyectos (Editar)":
    st.title("Gestión de Proyectos")
    st.info("💡 Puedes editar las celdas directamente. Para borrar una fila, selecciónala y presiona Supr. Al final, pulsa 'Guardar Cambios'.")
    
    # Editor
    edited_p = st.data_editor(
        df_p,
        num_rows="dynamic", # Permite agregar filas
        column_config={
            "Total Venta": st.column_config.NumberColumn(format="$%d"),
            "IVA": st.column_config.NumberColumn(format="$%d"),
            "Pagado": st.column_config.NumberColumn(format="$%d"),
            "Total con IVA": st.column_config.NumberColumn(format="$%d", disabled=True), # Calculado
            "Saldo": st.column_config.NumberColumn(format="$%d", disabled=True), # Calculado
            "Estado": st.column_config.SelectboxColumn(options=["Activo", "Finalizado", "Cotización"])
        },
        use_container_width=True,
        key="editor_proyectos"
    )
    
    if st.button("💾 GUARDAR CAMBIOS EN PROYECTOS", type="primary"):
        # Recalcular antes de guardar para asegurar consistencia
        save_to_sheet(sh, "proyectos", edited_p[['Fecha', 'Cliente', 'Proyecto', 'Total Venta', 'IVA', 'Pagado', 'Estado']])
        st.rerun()

# --- 3. GASTOS (CRUD) ---
elif menu == "💸 Gastos (Editar)":
    st.title("Gestión de Gastos")
    st.info("💡 Edita montos, proveedores o asigna proyectos aquí.")
    
    # Lista de proyectos para el dropdown
    lista_proyectos = df_p['Proyecto'].unique().tolist()
    if "Gasto General" not in lista_proyectos: lista_proyectos.append("Gasto General")
    
    edited_g = st.data_editor(
        df_g,
        num_rows="dynamic",
        column_config={
            "Proyecto Asignado": st.column_config.SelectboxColumn(options=lista_proyectos, required=True),
            "Base": st.column_config.NumberColumn(format="$%d", help="Valor antes de IVA"),
            "IVA": st.column_config.NumberColumn(format="$%d"),
            "Total Gasto": st.column_config.NumberColumn(format="$%d", disabled=True), # Calculado
            "Categoria": st.column_config.SelectboxColumn(options=["Materiales", "Mano de Obra", "Transporte", "Alimentación", "Servicios", "Equipos"])
        },
        use_container_width=True,
        key="editor_gastos"
    )
    
    if st.button("💾 GUARDAR CAMBIOS EN GASTOS", type="primary"):
        # Guardamos solo las columnas base, el total se recalcula al leer
        save_to_sheet(sh, "gastos", edited_g[['Fecha', 'Proyecto Asignado', 'Proveedor', 'Concepto', 'Base', 'IVA', 'Categoria']])
        st.rerun()

# --- 4. NÓMINA (CRUD) ---
elif menu == "👥 Nómina (Editar)":
    st.title("Gestión de Nómina")
    
    edited_n = st.data_editor(
        df_n,
        num_rows="dynamic",
        column_config={
            "Proyecto": st.column_config.SelectboxColumn(options=df_p['Proyecto'].unique().tolist()),
            "Valor Pactado": st.column_config.NumberColumn(format="$%d"),
            "Pagado": st.column_config.NumberColumn(format="$%d"),
            "Saldo": st.column_config.NumberColumn(format="$%d", disabled=True)
        },
        use_container_width=True,
        key="editor_nomina"
    )
    
    if st.button("💾 GUARDAR CAMBIOS EN NÓMINA", type="primary"):
        save_to_sheet(sh, "nomina", edited_n[['Fecha', 'Proyecto', 'Especialista', 'Rol', 'Valor Pactado', 'Pagado']])
        st.rerun()
