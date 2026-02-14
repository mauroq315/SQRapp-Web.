import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from google.oauth2 import service_account
import gspread
from datetime import datetime
import toml

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="SQRapp | Gerencia", layout="wide", page_icon="🚀")

# --- ESTILOS CSS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: #ffffff; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #ff4b4b; color: white; }
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

st.title("🚀 SQRapp | Gerencia")

# --- NUEVO ORDEN DE TABS ---
# 1. Proyectos (Origen del dinero) -> 2. Equipo (Destino del dinero) -> 3. Gastos -> 4. Resultados
tab1, tab2, tab3, tab4, tab5 = st.tabs(["💰 1. Proyectos (Ventas)", "👥 2. Squads (Equipo)", "💳 3. Gastos", "📊 Rentabilidad", "🏛 Impuestos"])

# --- TAB 1: PROYECTOS (VENTAS) ---
with tab1:
    st.header("Paso 1: Registrar Nuevo Proyecto")
    st.info("Registra aquí el contrato cerrado con el cliente. Esto creará el 'centro de costos' para asignar equipo.")
    
    with st.form("form_venta"):
        v_nombre = st.text_input("Nombre del Proyecto / Cliente (Ej: Edificio Alpha)")
        c1, c2 = st.columns(2)
        v_subtotal = c1.number_input("Valor del Contrato (Antes de IVA)", min_value=0.0)
        v_iva = c2.number_input("IVA (19%)", value=v_subtotal*0.19)
        v_total = v_subtotal + v_iva
        st.write(f"**Total Venta con IVA:** ${v_total:,.2f}")
        
        if st.form_submit_button("Crear Proyecto"):
            try:
                ws_proy = sheet_instance.worksheet("proyectos")
                ws_proy.append_row([v_nombre, v_subtotal, v_iva, v_total, 0])
                st.success(f"Proyecto '{v_nombre}' creado exitosamente. Ahora puedes asignarle equipo en la pestaña 2.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    st.markdown("---")
    st.subheader("Proyectos Activos")
    if not st.session_state['proyectos'].empty:
        st.dataframe(st.session_state['proyectos'])

# --- TAB 2: EQUIPO (SQUADS) ---
with tab2:
    st.header("Paso 2: Asignar Equipo al Proyecto")
    st.info("Asigna arquitectos o proveedores a los proyectos creados en el Paso 1.")
    
    # Obtener lista de proyectos actualizada
    lista_proyectos = []
    if not st.session_state['proyectos'].empty and 'Nombre Proyecto' in st.session_state['proyectos'].columns:
        lista_proyectos = st.session_state['proyectos']['Nombre Proyecto'].unique().tolist()
        lista_proyectos = [p for p in lista_proyectos if str(p).strip() != ""]
    
    if not lista_proyectos:
        st.warning("⚠️ No hay proyectos creados aún. Ve a la pestaña '1. Proyectos' primero.")
    
    with st.form("form_nomina"):
        col1, col2 = st.columns(2)
        nombre = col1.text_input("Nombre del Arquitecto / Squad")
        
        # El selectbox ahora depende de los proyectos existentes
        proyecto = col2.selectbox("Asignar al Proyecto:", options=lista_proyectos if lista_proyectos else ["General"])
        
        valor = st.number_input("Honorarios / Costo Total", min_value=0.0, step=1000.0)
        
        submitted = st.form_submit_button("Asignar al Squad")
        if submitted and nombre:
            try:
                ws_nom = sheet_instance.worksheet("nomina")
                ws_nom.append_row([nombre, proyecto, valor, 0, valor, "Pendiente"])
                st.success(f"{nombre} asignado al proyecto {proyecto}!")
                st.rerun()
            except Exception as e:
                st.error(f"Error guardando: {e}")

    st.markdown("---")
    st.header("Gestión de Pagos a Squads")
    
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
            st.info("Todos los squads están al día con sus pagos.")

# --- TAB 3: GASTOS ---
with tab3:
    st.header("Paso 3: Registrar Gastos")
    
    with st.form("form_gasto"):
        g_concepto = st.text_input("Concepto (Ej: Licencia Software, Viáticos)")
        
        # Ahora también puedes asignar gastos a proyectos específicos
        g_proyecto = st.selectbox("Proyecto Relacionado (Opcional)", ["General"] + lista_proyectos)
        
        g_cat = st.selectbox("Categoría", ["Operativo", "Administrativo", "Marketing", "Impuestos", "Otros"])
        g_monto = st.number_input("Monto", min_value=0.0)
        
        if st.form_submit_button("Guardar Gasto"):
            try:
                ws_gas = sheet_instance.worksheet("gastos")
                ws_gas.append_row([g_concepto, g_cat, g_proyecto, g_monto, str(datetime.now().date())])
                st.success("Gasto registrado")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

# --- TAB 4: RENTABILIDAD ---
with tab4:
    st.header("Estado de Resultados por Proyecto")
    
    # Calcular totales globales
    ventas = st.session_state['proyectos']['Subtotal Venta'].sum() if not st.session_state['proyectos'].empty else 0
    gastos = st.session_state['gastos']['Monto'].sum() if not st.session_state['gastos'].empty else 0
    nomina = st.session_state['nomina']['Valor Pactado'].sum() if not st.session_state['nomina'].empty else 0
    
    utilidad = ventas - gastos - nomina
    margen = (utilidad / ventas * 100) if ventas > 0 else 0
    
    # Métricas Globales
    st.subheader("Global de la Empresa")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Ventas", f"${ventas:,.0f}")
    c2.metric("Total Nómina Squads", f"${nomina:,.0f}")
    c3.metric("Total Gastos", f"${gastos:,.0f}")
    c4.metric("Utilidad Neta", f"${utilidad:,.0f}", delta=f"{margen:.1f}%")
    
    st.markdown("---")
    
    # Análisis Detallado (Aquí vendría la magia de filtrar por proyecto en el futuro)
    st.write("📊 *Próximamente: Filtro para ver rentabilidad de cada proyecto individualmente.*")
    
    datos_grafico = pd.DataFrame({
        'Concepto': ['Ingresos', 'Costos Squads', 'Gastos Op.', 'Utilidad'],
        'Monto': [ventas, nomina, gastos, utilidad]
    })
    fig = px.bar(datos_grafico, x='Concepto', y='Monto', color='Concepto', title="Flujo de Caja General")
    st.plotly_chart(fig, use_container_width=True)

# --- TAB 5: IMPUESTOS ---
with tab5:
    st.header("Estimación de Impuestos")
    iva_generado = st.session_state['proyectos']['IVA Generado'].sum() if not st.session_state['proyectos'].empty else 0
    st.metric("IVA Recaudado (A Pagar a DIAN)", f"${iva_generado:,.0f}")
    st.info("Recuerda: Este valor NO es tuyo, es del estado. No te lo gastes 😉")
