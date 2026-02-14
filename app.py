import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from google.oauth2 import service_account
import gspread
from datetime import datetime
import toml

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="SQRapp | Soluciones Integrales", layout="wide", page_icon="🚀")

# --- ESTILOS CSS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #ffffff; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #00d2ff; color: black; font-weight: bold;}
    .metric-card { background-color: #262730; padding: 20px; border-radius: 10px; border: 1px solid #41424C; text-align: center; }
    h1, h2, h3 { color: #ffffff; }
    </style>
""", unsafe_allow_html=True)

# --- CONEXIÓN CON GOOGLE SHEETS ---
def get_google_sheet_client():
    try:
        secrets = st.secrets["gcp_service_account"]
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = service_account.Credentials.from_service_account_info(secrets, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Error de credenciales: {e}")
        return None

def load_data():
    client = get_google_sheet_client()
    if not client:
        return None, None, None, None

    try:
        sheet = client.open("APP_SQR")
        
        def leer_hoja(nombre_hoja, columnas_esperadas):
            try:
                ws = sheet.worksheet(nombre_hoja)
                data = ws.get_all_records()
                df = pd.DataFrame(data)
                if df.empty: return pd.DataFrame(columns=columnas_esperadas)
                for col in columnas_esperadas:
                    if col not in df.columns:
                        df[col] = 0 if 'Valor' in col or 'Total' in col or 'IVA' in col else ""
                return df
            except gspread.exceptions.WorksheetNotFound:
                return pd.DataFrame(columns=columnas_esperadas)

        cols_nomina = ['Trabajador', 'Proyecto Asignado', 'Valor Pactado', 'Pagado', 'Pendiente', 'Estado']
        cols_proyectos = ['Nombre Proyecto', 'Subtotal Venta', 'IVA Generado', 'Total Venta', 'Pagado por Cliente']
        cols_gastos = ['Concepto', 'Categoria', 'Proyecto', 'Monto', 'Fecha']
        
        df_nomina = leer_hoja("nomina", cols_nomina)
        df_proyectos = leer_hoja("proyectos", cols_proyectos)
        df_gastos = leer_hoja("gastos", cols_gastos)

        # Limpieza de datos numéricos
        if not df_nomina.empty:
            df_nomina['Trabajador'] = df_nomina['Trabajador'].fillna('').astype(str)
            df_nomina['Proyecto Asignado'] = df_nomina['Proyecto Asignado'].fillna('').astype(str)
            for col in ['Valor Pactado', 'Pagado', 'Pendiente']:
                if col in df_nomina.columns:
                    df_nomina[col] = pd.to_numeric(df_nomina[col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)

        if not df_proyectos.empty:
            for col in ['Subtotal Venta', 'IVA Generado', 'Total Venta', 'Pagado por Cliente']:
                if col in df_proyectos.columns:
                    df_proyectos[col] = pd.to_numeric(df_proyectos[col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)

        if not df_gastos.empty:
            if 'Monto' in df_gastos.columns:
                df_gastos['Monto'] = pd.to_numeric(df_gastos['Monto'].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)

        return df_nomina, df_proyectos, df_gastos, sheet

    except Exception as e:
        st.error(f"Error leyendo APP_SQR: {e}")
        return None, None, None, None

# --- LOGIN ---
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

def check_password():
    if st.session_state['username'] == 'admin' and st.session_state['password'] == '1234':
        st.session_state['authenticated'] = True
    else:
        st.error("Usuario o contraseña incorrectos")

if not st.session_state['authenticated']:
    st.title("🔐 SQRapp Login")
    st.text_input("Usuario", key="username")
    st.text_input("Contraseña", type="password", key="password")
    st.button("Ingresar", on_click=check_password)
    st.stop()

# --- INTERFAZ PRINCIPAL ---
st.sidebar.button("🔒 Cerrar Sesión", on_click=lambda: st.session_state.update({'authenticated': False}))

df_n, df_p, df_g, sheet_instance = load_data()

if df_n is None:
    st.warning("⚠️ MODO OFFLINE: Revisa la conexión.")
    st.stop()
else:
    st.session_state['nomina'] = df_n
    st.session_state['proyectos'] = df_p
    st.session_state['gastos'] = df_g

st.title("🚀 SQRapp | Gerencia de Proyectos")

# --- TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["💰 1. Ventas (Proyectos)", "👥 2. Equipo & Especialistas", "🛒 3. Compras y Gastos", "📊 Rentabilidad", "🏛 Impuestos"])

# --- TAB 1: PROYECTOS (VENTAS) ---
with tab1:
    st.header("Paso 1: Registrar Venta / Contrato")
    st.info("Registra aquí el contrato con el cliente (Ej: Instalación CCTV, Campaña Ads, Desarrollo Web).")
    
    with st.form("form_venta"):
        v_nombre = st.text_input("Cliente / Descripción del Proyecto")
        
        col_a, col_b = st.columns(2)
        
        # Input 1: Base Gravable
        with col_a:
            st.markdown("##### 1. Base Gravable (19%)")
            st.caption("Servicios, Mano de Obra, Diseño, Pauta (si aplica).")
            v_base = st.number_input("Monto Gravable", min_value=0.0, key="base_gravable")
            
        # Input 2: Excluido
        with col_b:
            st.markdown("##### 2. Excluido / No Gravable")
            st.caption("Hardware exento, Licencias específicas, Reembolsos.")
            v_excluido = st.number_input("Monto Excluido", min_value=0.0, key="base_excluida")

        # Cálculos automáticos
        v_iva = v_base * 0.19
        v_subtotal = v_base + v_excluido
        v_total = v_subtotal + v_iva
        
        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        c1.metric("Subtotal Venta", f"${v_subtotal:,.2f}")
        c2.metric("IVA (19%)", f"${v_iva:,.2f}")
        c3.metric("Total a Cobrar", f"${v_total:,.2f}")
        
        if st.form_submit_button("Registrar Venta"):
            try:
                ws_proy = sheet_instance.worksheet("proyectos")
                ws_proy.append_row([v_nombre, v_subtotal, v_iva, v_total, 0])
                st.success(f"Venta '{v_nombre}' registrada correctamente.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    st.markdown("---")
    st.subheader("Historial de Ventas")
    if not st.session_state['proyectos'].empty:
        st.dataframe(st.session_state['proyectos'])

# --- TAB 2: EQUIPO (SQUADS) ---
with tab2:
    st.header("Paso 2: Asignar Especialistas")
    st.info("Asigna instaladores, programadores, traffickers o diseñadores al proyecto.")
    
    # Obtener lista de proyectos
    lista_proyectos = []
    if not st.session_state['proyectos'].empty and 'Nombre Proyecto' in st.session_state['proyectos'].columns:
        lista_proyectos = st.session_state['proyectos']['Nombre Proyecto'].unique().tolist()
        lista_proyectos = [p for p in lista_proyectos if str(p).strip() != ""]
    
    if not lista_proyectos:
        st.warning("⚠️ Registra una venta primero en la pestaña 1.")
    
    with st.form("form_nomina"):
        col1, col2 = st.columns(2)
        nombre = col1.text_input("Nombre y Rol (Ej: Juan - Instalador CCTV)")
        proyecto = col2.selectbox("Asignar al Proyecto:", options=lista_proyectos if lista_proyectos else ["General"])
        valor = st.number_input("Costo del Servicio / Honorarios", min_value=0.0, step=1000.0)
        
        submitted = st.form_submit_button("Asignar Especialista")
        if submitted and nombre:
            try:
                ws_nom = sheet_instance.worksheet("nomina")
                ws_nom.append_row([nombre, proyecto, valor, 0, valor, "Pendiente"])
                st.success(f"{nombre} asignado a {proyecto}!")
                st.rerun()
            except Exception as e:
                st.error(f"Error guardando: {e}")

    st.markdown("---")
    st.header("Pagos a Especialistas")
    
    if not st.session_state['nomina'].empty:
        df_display = st.session_state['nomina'].copy()
        df_display['Ref'] = df_display['Trabajador'].astype(str) + " - " + df_display['Proyecto Asignado'].astype(str)
        pendientes = df_display[df_display['Pendiente'] > 0]
        
        if not pendientes.empty:
            opcion = st.selectbox("Seleccionar Pago Pendiente", pendientes['Ref'].tolist())
            pago = st.number_input("Monto a Pagar Hoy", min_value=0.0, step=1000.0)
            
            if st.button("Registrar Pago"):
                try:
                    idx = df_display[df_display['Ref'] == opcion].index[0]
                    row_num = idx + 2 
                    ws_nom = sheet_instance.worksheet("nomina")
                    pagado_actual = float(str(df_display.at[idx, 'Pagado']).replace(',',''))
                    valor_total = float(str(df_display.at[idx, 'Valor Pactado']).replace(',',''))
                    nuevo_pagado = pagado_actual + pago
                    nuevo_pendiente = valor_total - nuevo_pagado
                    nuevo_estado = "Pagado" if nuevo_pendiente <= 0 else "Pendiente"
                    ws_nom.update_cell(row_num, 4, nuevo_pagado)
                    ws_nom.update_cell(row_num, 5, nuevo_pendiente)
                    ws_nom.update_cell(row_num, 6, nuevo_estado)
                    st.success("Pago registrado!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error actualizando: {e}")
        else:
            st.info("Todos al día.")

# --- TAB 3: GASTOS ---
with tab3:
    st.header("Paso 3: Compras y Gastos Operativos")
    st.warning("Registra aquí la compra de equipos (Cámaras, PCs) y gastos de operación.")
    
    with st.form("form_gasto"):
        g_concepto = st.text_input("Descripción (Ej: Compra 10 Cámaras Hikvision, Pago Hosting)")
        g_proyecto = st.selectbox("Proyecto Relacionado (Opcional)", ["General"] + lista_proyectos)
        
        g_cat = st.selectbox("Categoría", [
            "Compra de Equipos/Hardware", 
            "Licencias de Software", 
            "Pauta Publicitaria (Ads)", 
            "Nómina Administrativa", 
            "Impuestos",
            "Otros Operativos"
        ])
        g_monto = st.number_input("Monto Total Pagado", min_value=0.0)
        
        if st.form_submit_button("Registrar Gasto/Compra"):
            try:
                ws_gas = sheet_instance.worksheet("gastos")
                ws_gas.append_row([g_concepto, g_cat, g_proyecto, g_monto, str(datetime.now().date())])
                st.success("Registrado correctamente")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

# --- TAB 4: RENTABILIDAD ---
with tab4:
    st.header("Estado de Resultados Global")
    
    ventas = st.session_state['proyectos']['Subtotal Venta'].sum() if not st.session_state['proyectos'].empty else 0
    gastos = st.session_state['gastos']['Monto'].sum() if not st.session_state['gastos'].empty else 0
    nomina = st.session_state['nomina']['Valor Pactado'].sum() if not st.session_state['nomina'].empty else 0
    
    utilidad = ventas - gastos - nomina
    margen = (utilidad / ventas * 100) if ventas > 0 else 0
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Ventas", f"${ventas:,.0f}")
    c2.metric("Costo Especialistas", f"${nomina:,.0f}")
    c3.metric("Compras y Gastos", f"${gastos:,.0f}")
    c4.metric("Utilidad Neta", f"${utilidad:,.0f}", delta=f"{margen:.1f}%")
    
    datos_grafico = pd.DataFrame({
        'Concepto': ['Ingresos', 'Especialistas', 'Compras/Gastos', 'Utilidad'],
        'Monto': [ventas, nomina, gastos, utilidad]
    })
    fig = px.bar(datos_grafico, x='Concepto', y='Monto', color='Concepto', title="Flujo de Caja SQR")
    st.plotly_chart(fig, use_container_width=True)

# --- TAB 5: IMPUESTOS ---
with tab5:
    st.header("Estimación de Impuestos")
    iva_generado = st.session_state['proyectos']['IVA Generado'].sum() if not st.session_state['proyectos'].empty else 0
    st.metric("IVA Recaudado (A Pagar a DIAN)", f"${iva_generado:,.0f}")
    st.caption("Este es el IVA que has cobrado a tus clientes. Debes tenerlo disponible para el pago bimestral/cuatrimestral.")
