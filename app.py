import streamlit as st
import pandas as pd
from google.oauth2 import service_account
import gspread
from datetime import datetime

# --- CONFIGURACIÓN VISUAL (IGUAL A LA QUE TE GUSTÓ) ---
st.set_page_config(page_title="SQRapp", layout="wide", page_icon="🏗️")

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
    </style>
""", unsafe_allow_html=True)

# --- FUNCIONES BÁSICAS ---
def get_client():
    try:
        secrets = st.secrets["gcp_service_account"]
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(secrets, scopes=scopes)
        return gspread.authorize(creds)
    except: return None

def clean_money(val):
    """Convierte texto a numero para calculos internos"""
    if pd.isna(val) or str(val).strip() == "": return 0.0
    s = str(val).replace('$', '').replace(' ', '').replace('.', '').replace(',', '.')
    try: return float(s)
    except: return 0.0

def fmt_money(x):
    return "${:,.0f}".format(x).replace(",", ".")

# --- CARGA DE DATOS ---
def load_data():
    client = get_client()
    if not client: return None, None, None, None
    sh = client.open("APP_SQR")
    
    def get_df(name):
        ws = sh.worksheet(name)
        data = ws.get_all_values()
        if len(data) < 2: return pd.DataFrame(), ws
        return pd.DataFrame(data[1:], columns=data[0]), ws

    df_p, ws_p = get_df("proyectos")
    df_g, ws_g = get_df("gastos")
    df_n, ws_n = get_df("nomina")
    return df_p, df_g, df_n, sh

# --- GUARDAR CAMBIOS (CRUD) ---
def save_sheet(sh, name, df):
    try:
        ws = sh.worksheet(name)
        ws.clear()
        # Convertir a string para evitar errores de formato en sheets
        df_str = df.astype(str)
        data = [df_str.columns.tolist()] + df_str.values.tolist()
        ws.update(data)
        st.success(f"✅ Cambios guardados en {name}")
        st.rerun()
    except Exception as e:
        st.error(f"Error guardando: {e}")

# --- APP PRINCIPAL ---
df_p, df_g, df_n, sh = load_data()

st.sidebar.title("Menú Principal")
menu = st.sidebar.radio("Ir a:", ["🏠 Inicio", "🏗️ Proyectos", "💸 Gastos", "👷 Nómina"])

if st.sidebar.button("🔄 Actualizar"):
    st.rerun()

# 1. INICIO
if menu == "🏠 Inicio":
    st.title("Resumen General")
    if not df_p.empty and not df_g.empty:
        # Calculos simples
        v = sum([clean_money(x) for x in df_p['Total Venta']])
        g = sum([clean_money(x) for x in df_g['Total Gasto']])
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Ventas", fmt_money(v))
        c2.metric("Total Gastos", fmt_money(g))
        c3.metric("Ganancia", fmt_money(v-g))

# 2. PROYECTOS
elif menu == "🏗️ Proyectos":
    st.title("Mis Proyectos")
    t1, t2, t3 = st.tabs(["📂 LISTADO (EDITABLE)", "➕ CREAR NUEVO", "🔍 DETALLE 360"])
    
    with t1:
        st.info("Puedes editar los datos aquí mismo. Al finalizar, presiona el botón rojo.")
        if not df_p.empty:
            edited_p = st.data_editor(df_p, num_rows="dynamic", use_container_width=True, key="edit_p")
            if st.button("💾 GUARDAR CAMBIOS PROYECTOS", type="primary"):
                save_sheet(sh, "proyectos", edited_p)
    
    with t2:
        with st.form("new_p"):
            st.write("Registrar Nuevo Proyecto")
            nom = st.text_input("Nombre")
            cli = st.text_input("Cliente")
            val = st.number_input("Valor", min_value=0)
            if st.form_submit_button("Crear"):
                sh.worksheet("proyectos").append_row([
                    str(datetime.now().date()), cli, nom, val, 0, 0, val, "Activo", "No"
                ])
                st.success("Creado")
                st.rerun()

    with t3:
        if not df_p.empty:
            p = st.selectbox("Ver proyecto:", df_p['Proyecto'].unique())
            # Filtros simples
            mis_g = df_g[df_g['Proyecto Asignado'] == p] if not df_g.empty else pd.DataFrame()
            
            st.write(f"**Gastos de {p}:**")
            st.dataframe(mis_g, use_container_width=True)
            
            total_g = sum([clean_money(x) for x in mis_g['Total Gasto']]) if not mis_g.empty else 0
            st.metric("Total Gastado Aquí", fmt_money(total_g))

# 3. GASTOS
elif menu == "💸 Gastos":
    st.title("Control de Gastos")
    t1, t2 = st.tabs(["📝 REGISTRAR GASTO", "📂 HISTORIAL (EDITABLE)"])
    
    with t1:
        with st.form("new_g"):
            st.write("Registrar Gasto")
            l_proy = df_p['Proyecto'].unique().tolist() if not df_p.empty else []
            p = st.selectbox("Proyecto", ["Gasto General"] + l_proy)
            prov = st.text_input("Proveedor")
            desc = st.text_input("Concepto")
            val = st.number_input("Valor Total", min_value=0)
            cat = st.selectbox("Categoría", ["Materiales", "Mano de Obra", "Transporte", "Varios"])
            
            if st.form_submit_button("Guardar Gasto"):
                sh.worksheet("gastos").append_row([
                    str(datetime.now().date()), p, prov, desc, val, 0, val, cat, "Manual"
                ])
                st.success("Guardado")
                st.rerun()
                
    with t2:
        st.info("Corrige valores o borra filas aquí.")
        if not df_g.empty:
            edited_g = st.data_editor(df_g, num_rows="dynamic", use_container_width=True, key="edit_g")
            if st.button("💾 GUARDAR CAMBIOS GASTOS", type="primary"):
                save_sheet(sh, "gastos", edited_g)

# 4. NOMINA
elif menu == "👷 Nómina":
    st.title("Nómina")
    t1, t2 = st.tabs(["👷 ASIGNAR", "💰 VER LISTADO (EDITABLE)"])
    
    with t1:
        with st.form("new_n"):
            nom = st.text_input("Nombre")
            rol = st.selectbox("Rol", ["Oficial", "Ayudante"])
            proy = st.selectbox("Proyecto", df_p['Proyecto'].unique() if not df_p.empty else [])
            val = st.number_input("Valor", min_value=0)
            if st.form_submit_button("Asignar"):
                sh.worksheet("nomina").append_row([
                    str(datetime.now().date()), proy, nom, rol, val, 0, val
                ])
                st.success("Asignado")
                st.rerun()
                
    with t2:
        if not df_n.empty:
            edited_n = st.data_editor(df_n, num_rows="dynamic", use_container_width=True, key="edit_n")
            if st.button("💾 GUARDAR CAMBIOS NÓMINA", type="primary"):
                save_sheet(sh, "nomina", edited_n)
