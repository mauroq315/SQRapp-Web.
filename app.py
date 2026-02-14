import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
from datetime import datetime
import numpy as np

# --- CONFIGURACIÓN VISUAL ---
st.set_page_config(page_title="SQRapp Pro", layout="wide", page_icon="🏗️")

# --- ESTILOS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { 
        height: 50px; 
        background-color: #1e2130; 
        border-radius: 8px; 
        color: white; 
        font-weight: bold;
    }
    .stTabs [aria-selected="true"] { 
        background-color: #4CAF50; 
        color: white; 
    }
    /* Botón de guardar cambios */
    .stButton button[kind="primary"] {
        background-color: #FF4B4B;
        color: white;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# --- FUNCIONES DE LIMPIEZA (Para arreglar el error de $0) ---
def clean_money_value(val):
    """Convierte texto sucio ($ 1.000.000) a numero limpio (1000000.0)"""
    if pd.isna(val) or str(val).strip() == "": return 0.0
    s = str(val).strip().replace('$', '').replace(' ', '')
    # Manejo de puntos y comas estilo Colombia
    if '.' in s and ',' in s: s = s.replace('.', '').replace(',', '.')
    elif '.' in s: # Asumimos que puntos son miles
        if len(s.split('.')[-1]) == 3: s = s.replace('.', '')
    elif ',' in s: s = s.replace(',', '.')
    try: return float(s)
    except: return 0.0

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

# --- CARGA Y GUARDADO ---
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

        # Limpieza de números para cálculos
        if not df_p.empty:
            df_p['Total Venta Num'] = df_p['Total Venta'].apply(clean_money_value)
            df_p['Saldo Pendiente Num'] = df_p['Saldo Pendiente'].apply(clean_money_value)
        
        if not df_g.empty:
            df_g['Base Num'] = df_g['Base'].apply(clean_money_value)
            df_g['Total Gasto Num'] = df_g['Total Gasto'].apply(clean_money_value)

        if not df_n.empty:
            df_n['Valor Pactado Num'] = df_n['Valor Pactado'].apply(clean_money_value)

        return df_p, df_g, df_n, sh
    except Exception as e:
        st.error(f"Error cargando: {e}")
        return None, None, None, None

def save_changes(sh, sheet_name, df_edited):
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
        # Convertir todo a string para asegurar formato en sheets
        df_str = df_edited.astype(str)
        data = [df_str.columns.tolist()] + df_str.values.tolist()
        ws.update(data)
        st.success("✅ ¡Cambios guardados en la base de datos!")
        st.rerun()
    except Exception as e:
        st.error(f"Error guardando: {e}")

# --- UI PRINCIPAL ---
df_p, df_g, df_n, sh = load_data()

st.sidebar.title("SQRapp Manager")
menu = st.sidebar.radio("Menú", ["🏠 Inicio", "🏗️ Proyectos", "💸 Gastos", "👷 Nómina"])

if st.sidebar.button("🔄 Actualizar Todo"):
    st.rerun()

# --- 1. INICIO ---
if menu == "🏠 Inicio":
    st.title("Panel de Control")
    
    if not df_p.empty and not df_g.empty:
        ventas = df_p['Total Venta Num'].sum()
        gastos = df_g['Base Num'].sum()
        utilidad = ventas - gastos
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Ventas Totales", fmt_money(ventas))
        c2.metric("Gastos Totales", fmt_money(gastos))
        c3.metric("Utilidad Neta", fmt_money(utilidad))
        
        st.subheader("Resumen por Proyecto")
        # Agrupación simple
        p_v = df_p.groupby('Proyecto')['Total Venta Num'].sum().reset_index()
        g_v = df_g.groupby('Proyecto Asignado')['Base Num'].sum().reset_index()
        
        merged = pd.merge(p_v, g_v, left_on='Proyecto', right_on='Proyecto Asignado', how='left').fillna(0)
        merged['Ganancia'] = merged['Total Venta Num'] - merged['Base Num']
        
        st.dataframe(merged[['Proyecto', 'Total Venta Num', 'Base Num', 'Ganancia']].rename(
            columns={'Total Venta Num': 'Ventas', 'Base Num': 'Gastos'}
        ).style.format(fmt_money), use_container_width=True)

# --- 2. PROYECTOS ---
elif menu == "🏗️ Proyectos":
    st.title("Mis Proyectos")
    
    tab1, tab2, tab3 = st.tabs(["📂 LISTADO EDITABLE", "➕ CREAR NUEVO", "🔍 DETALLE 360°"])
    
    with tab1:
        st.info("💡 Haz doble clic en cualquier celda para corregirla. Si borras una fila, dale a 'Guardar Cambios'.")
        # Columnas que queremos mostrar/editar
        cols_view = ['Fecha', 'Cliente', 'Proyecto', 'Total Venta', 'Pagado Cliente', 'Saldo Pendiente', 'Estado']
        
        if not df_p.empty:
            # Editor de datos
            edited_p = st.data_editor(
                df_p[cols_view],
                num_rows="dynamic",
                use_container_width=True,
                key="editor_proyectos"
            )
            
            if st.button("💾 GUARDAR CAMBIOS EN PROYECTOS", type="primary"):
                # Reconstruir DF completo preservando columnas ocultas si las hubiera
                save_changes(sh, "proyectos", edited_p)
        else:
            st.warning("No hay proyectos aún.")

    with tab2:
        st.write("### Registrar Nuevo Proyecto")
        with st.form("new_proy"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Nombre Proyecto")
            cli = c2.text_input("Cliente")
            val = st.number_input("Valor Venta", min_value=0)
            iva_b = st.checkbox("Aplica IVA")
            
            if st.form_submit_button("Crear Proyecto"):
                iva = val * 0.19 if iva_b else 0
                total = val + iva
                sh.worksheet("proyectos").append_row([
                    str(datetime.now().date()), cli, nom, val, iva, 0, total, "Activo", "Sí" if iva_b else "No"
                ])
                st.success("Creado!")
                st.rerun()

    with tab3:
        if not df_p.empty:
            proy = st.selectbox("Seleccionar Proyecto", df_p['Proyecto'].unique())
            
            # Filtrar
            mis_gastos = df_g[df_g['Proyecto Asignado'] == proy] if not df_g.empty else pd.DataFrame()
            mi_nomina = df_n[df_n['Proyecto'] == proy] if not df_n.empty else pd.DataFrame()
            
            c1, c2 = st.columns(2)
            c1.metric("Gastos Aquí", fmt_money(mis_gastos['Total Gasto Num'].sum()) if not mis_gastos.empty else "$0")
            c2.metric("Nómina Aquí", fmt_money(mi_nomina['Valor Pactado Num'].sum()) if not mi_nomina.empty else "$0")
            
            st.write("📋 **Gastos de este proyecto (Editable):**")
            if not mis_gastos.empty:
                # Permitir editar gastos DESDE la vista 360
                edited_g_360 = st.data_editor(mis_gastos[['Fecha', 'Proveedor', 'Concepto', 'Total Gasto']], key="edit_360", num_rows="dynamic")
                # Nota: Guardar desde aquí es complejo, mejor solo visualizar o ir a pestaña gastos
                st.caption("Para editar detalles profundos, ve a la pestaña 'Gastos'.")
            else:
                st.info("Sin gastos registrados.")

# --- 3. GASTOS ---
elif menu == "💸 Gastos":
    st.title("Control de Gastos")
    
    tab1, tab2 = st.tabs(["📝 REGISTRAR GASTO", "📂 HISTORIAL EDITABLE"])
    
    with tab1:
        st.write("### Registrar Compra / Gasto")
        with st.form("add_gasto"):
            c1, c2 = st.columns(2)
            proy = c1.selectbox("Proyecto", ["Gasto General"] + (df_p['Proyecto'].unique().tolist() if not df_p.empty else []))
            prov = c2.text_input("Proveedor")
            conc = st.text_input("Concepto (Qué compraste)")
            
            cc1, cc2, cc3 = st.columns(3)
            val = cc1.number_input("Valor Total Pagado", min_value=0)
            cat = cc2.selectbox("Categoría", ["Materiales", "Mano de Obra", "Transporte", "Alimentación", "Servicios"])
            tiene_iva = cc3.checkbox("Tiene IVA discriminado")
            
            if st.form_submit_button("Guardar Gasto"):
                base = val / 1.19 if tiene_iva else val
                iva = val - base if tiene_iva else 0
                sh.worksheet("gastos").append_row([
                    str(datetime.now().date()), proy, prov, conc, base, iva, val, cat, "Manual"
                ])
                st.success("Guardado!")
                st.rerun()
    
    with tab2:
        st.write("### Historial Completo (Puedes corregir y borrar)")
        if not df_g.empty:
            # Columnas clave
            cols_g = ['Fecha', 'Proyecto Asignado', 'Proveedor', 'Concepto', 'Base', 'IVA Descontable', 'Total Gasto', 'Categoria']
            # Asegurar que existan
            for c in cols_g: 
                if c not in df_g.columns: df_g[c] = ""
                
            edited_g = st.data_editor(
                df_g[cols_g],
                num_rows="dynamic",
                use_container_width=True,
                key="editor_gastos_full"
            )
            
            if st.button("💾 GUARDAR CORRECCIONES DE GASTOS", type="primary"):
                save_changes(sh, "gastos", edited_g)

# --- 4. NÓMINA ---
elif menu == "👷 Nómina":
    st.title("Nómina y Equipo")
    
    tab1, tab2 = st.tabs(["👷 ASIGNAR TRABAJO", "💰 VER Y PAGAR (EDITABLE)"])
    
    with tab1:
        with st.form("add_nom"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Nombre Persona")
            rol = c2.selectbox("Rol", ["Oficial", "Ayudante", "Ingeniero"])
            proy = st.selectbox("Proyecto", df_p['Proyecto'].unique() if not df_p.empty else [])
            val = st.number_input("Valor a Pagar", min_value=0)
            
            if st.form_submit_button("Asignar"):
                sh.worksheet("nomina").append_row([
                    str(datetime.now().date()), proy, nom, rol, val, 0, val
                ])
                st.success("Asignado!")
                st.rerun()
    
    with tab2:
        st.write("### Estado de Cuenta Personal")
        if not df_n.empty:
            cols_n = ['Fecha', 'Proyecto', 'Especialista', 'Rol', 'Valor Pactado', 'Pagado', 'Saldo Debe']
            for c in cols_n:
                if c not in df_n.columns: df_n[c] = ""
                
            edited_n = st.data_editor(
                df_n[cols_n],
                num_rows="dynamic",
                use_container_width=True,
                key="editor_nomina_full"
            )
            
            if st.button("💾 GUARDAR CAMBIOS NÓMINA", type="primary"):
                save_changes(sh, "nomina", edited_n)
