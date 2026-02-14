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
        creds = service_account.Credentials.from_service_account_info(secrets, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Error de credenciales: {e}")
        return None

def load_data():
    client = get_google_sheet_client()
    if not client:
        return None, None, None, None

    try:
        # Intentar abrir la hoja
        sheet = client.open("APP_SQR")
        
        # Función auxiliar para leer y limpiar
        def leer_hoja(nombre_hoja, columnas_esperadas):
            try:
                ws = sheet.worksheet(nombre_hoja)
                data = ws.get_all_records()
                df = pd.DataFrame(data)
                
                # Si está vacío, crear estructura vacía
                if df.empty:
                    return pd.DataFrame(columns=columnas_esperadas)
                
                # Asegurar que todas las columnas existan
                for col in columnas_esperadas:
                    if col not in df.columns:
                        df[col] = 0 if 'Valor' in col or 'Total' in col or 'IVA' in col else ""
                
                return df
            except gspread.exceptions.WorksheetNotFound:
                # Si no existe la pestaña, devolver dataframe vacío
                return pd.DataFrame(columns=columnas_esperadas)

        # Definir columnas esperadas para evitar KeyErrors
        cols_nomina = ['Trabajador', 'Proyecto Asignado', 'Valor Pactado', 'Pagado', 'Pendiente', 'Estado']
        cols_proyectos = ['Nombre Proyecto', 'Subtotal Venta', 'IVA Generado', 'Total Venta', 'Pagado por Cliente']
        cols_gastos = ['Concepto', 'Categoria', 'Proyecto', 'Monto', 'Fecha']
        
        df_nomina = leer_hoja("nomina", cols_nomina)
        df_proyectos = leer_hoja("proyectos", cols_proyectos)
        df_gastos = leer_hoja("gastos", cols_gastos)

        # --- LIMPIEZA DE DATOS (Anti-Crash) ---
        
        # 1. Limpieza Nómina
        if not df_nomina.empty:
            # Convertir a texto explícitamente para evitar error de concatenación
            df_nomina['Trabajador'] = df_nomina['Trabajador'].fillna('').astype(str)
            df_nomina['Proyecto Asignado'] = df_nomina['Proyecto Asignado'].fillna('').astype(str)
            
            # Convertir números
            cols_num_nom = ['Valor Pactado', 'Pagado', 'Pendiente']
            for col in cols_num_nom:
                if col in df_nomina.columns:
                    df_nomina[col] = pd.to_numeric(df_nomina[col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)

        # 2. Limpieza Proyectos
        if not df_proyectos.empty:
            cols_num_proy = ['Subtotal Venta', 'IVA Generado', 'Total Venta', 'Pagado por Cliente']
            for col in cols_num_proy:
                if col in df_proyectos.columns:
                    df_proyectos[col] = pd.to_numeric(df_proyectos[col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').fillna(0)

        # 3. Limpieza Gastos
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
    st.warning("⚠️ MODO OFFLINE: No se encontró 'credentials.json' o la hoja 'APP_SQR'.")
    st.stop()
else:
    # Guardar en session state
    st.session_state['nomina'] = df_n
    st.session_state['proyectos'] = df_p
    st.session_state['gastos'] = df_g

st.title("🚀 SQRapp | Gerencia")

# --- TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["👥 Equipo", "💰 Ventas", "💳 Gastos", "📊 Rentabilidad", "🏛 Impuestos"])

# --- TAB 1: EQUIPO (Nómina) ---
with tab1:
    st.header("Nuevo Contrato / Servicio")
    
    with st.form("form_nomina"):
        col1, col2 = st.columns(2)
        nombre = col1.text_input("Nombre Colaborador/Proveedor")
        
        # Obtener lista de proyectos única, manejando vacíos
        lista_proyectos = ["General"]
        if not st.session_state['proyectos'].empty and 'Nombre Proyecto' in st.session_state['proyectos'].columns:
            proyectos_existentes = st.session_state['proyectos']['Nombre Proyecto'].unique().tolist()
            # Filtrar vacíos y agregar
            proyectos_existentes = [p for p in proyectos_existentes if str(p).strip() != ""]
            lista_proyectos.extend(proyectos_existentes)
            
        proyecto = col2.selectbox("Proyecto Asignado", options=lista_proyectos)
        valor = st.number_input("Valor Total del Acuerdo", min_value=0.0, step=1000.0)
        
        submitted = st.form_submit_button("Registrar Contrato")
        if submitted and nombre:
            try:
                ws_nom = sheet_instance.worksheet("nomina")
                ws_nom.append_row([nombre, proyecto, valor, 0, valor, "Pendiente"])
                st.success("Contrato registrado!")
                st.rerun()
            except Exception as e:
                st.error(f"Error guardando: {e}")

    st.markdown("---")
    st.header("Registrar Pago / Anticipo")
    
    if not st.session_state['nomina'].empty:
        # CREAR LA REFERENCIA DE FORMA SEGURA
        df_display = st.session_state['nomina'].copy()
        
        # Esta es la línea que arregla tu error:
        df_display['Ref'] = df_display['Trabajador'].astype(str) + " - " + df_display['Proyecto Asignado'].astype(str)
        
        # Filtrar solo los que tienen saldo pendiente > 0
        pendientes = df_display[df_display['Pendiente'] > 0]
        
        if not pendientes.empty:
            opcion = st.selectbox("Seleccionar Contrato", pendientes['Ref'].tolist())
            pago = st.number_input("Monto a Pagar", min_value=0.0, step=1000.0)
            
            if st.button("Registrar Pago"):
                try:
                    idx = df_display[df_display['Ref'] == opcion].index[0]
                    # +2 porque google sheets empieza en 1 y tiene header
                    row_num = idx + 2 
                    
                    ws_nom = sheet_instance.worksheet("nomina")
                    
                    # Obtener valores actuales
                    pagado_actual = float(str(df_display.at[idx, 'Pagado']).replace(',',''))
                    valor_total = float(str(df_display.at[idx, 'Valor Pactado']).replace(',',''))
                    
                    nuevo_pagado = pagado_actual + pago
                    nuevo_pendiente = valor_total - nuevo_pagado
                    nuevo_estado = "Pagado" if nuevo_pendiente <= 0 else "Pendiente"
                    
                    # Actualizar celdas (Col D, E, F)
                    ws_nom.update_cell(row_num, 4, nuevo_pagado)
                    ws_nom.update_cell(row_num, 5, nuevo_pendiente)
                    ws_nom.update_cell(row_num, 6, nuevo_estado)
                    
                    st.success("Pago registrado!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error actualizando: {e}")
        else:
            st.info("No hay pagos pendientes.")
    else:
        st.info("No hay datos de nómina aún.")

# --- TAB 2: VENTAS ---
with tab2:
    st.header("Registrar Venta")
    with st.form("form_venta"):
        v_nombre = st.text_input("Nombre del Proyecto / Cliente")
        c1, c2 = st.columns(2)
        v_subtotal = c1.number_input("Subtotal", min_value=0.0)
        v_iva = c2.number_input("IVA (19%)", value=v_subtotal*0.19)
        v_total = v_subtotal + v_iva
        st.write(f"**Total Venta:** ${v_total:,.2f}")
        
        if st.form_submit_button("Guardar Venta"):
            try:
                ws_proy = sheet_instance.worksheet("proyectos")
                ws_proy.append_row([v_nombre, v_subtotal, v_iva, v_total, 0])
                st.success("Venta guardada")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    # Métricas
    if not st.session_state['proyectos'].empty:
        total_ventas = st.session_state['proyectos']['Total Venta'].sum()
        cobrado = st.session_state['proyectos']['Pagado por Cliente'].sum()
        por_cobrar = total_ventas - cobrado
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Ventas Totales", f"${total_ventas:,.0f}")
        c2.metric("Cobrado", f"${cobrado:,.0f}")
        c3.metric("Por Cobrar", f"${por_cobrar:,.0f}")

# --- TAB 3: GASTOS ---
with tab3:
    st.header("Registrar Gasto")
    with st.form("form_gasto"):
        g_concepto = st.text_input("Concepto")
        g_cat = st.selectbox("Categoría", ["Operativo", "Administrativo", "Marketing", "Impuestos", "Otros"])
        g_monto = st.number_input("Monto", min_value=0.0)
        
        if st.form_submit_button("Guardar Gasto"):
            try:
                ws_gas = sheet_instance.worksheet("gastos")
                ws_gas.append_row([g_concepto, g_cat, "General", g_monto, str(datetime.now().date())])
                st.success("Gasto guardado")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

# --- TAB 4: RENTABILIDAD ---
with tab4:
    st.header("Estado de Resultados")
    
    # Calcular totales seguros
    ventas = st.session_state['proyectos']['Subtotal Venta'].sum() if not st.session_state['proyectos'].empty else 0
    gastos = st.session_state['gastos']['Monto'].sum() if not st.session_state['gastos'].empty else 0
    nomina = st.session_state['nomina']['Valor Pactado'].sum() if not st.session_state['nomina'].empty else 0
    
    utilidad = ventas - gastos - nomina
    margen = (utilidad / ventas * 100) if ventas > 0 else 0
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Ingresos (Subtotal)", f"${ventas:,.0f}")
    col2.metric("Nómina/Costos", f"${nomina:,.0f}")
    col3.metric("Gastos Op.", f"${gastos:,.0f}")
    col4.metric("Utilidad Neta", f"${utilidad:,.0f}", delta=f"{margen:.1f}%")
    
    # Gráfico
    datos_grafico = pd.DataFrame({
        'Concepto': ['Ingresos', 'Nómina', 'Gastos', 'Utilidad'],
        'Monto': [ventas, nomina, gastos, utilidad]
    })
    fig = px.bar(datos_grafico, x='Concepto', y='Monto', color='Concepto', title="Flujo de Caja")
    st.plotly_chart(fig, use_container_width=True)

# --- TAB 5: IMPUESTOS ---
with tab5:
    st.header("Estimación de Impuestos")
    
    iva_generado = st.session_state['proyectos']['IVA Generado'].sum() if not st.session_state['proyectos'].empty else 0
    
    # Aquí podrías restar IVA descontable si lo tuvieras en gastos
    iva_pagar = iva_generado 
    
    st.metric("IVA a Pagar (Aprox)", f"${iva_pagar:,.0f}")
    st.info("Este cálculo es solo una estimación basada en las ventas registradas con IVA.")