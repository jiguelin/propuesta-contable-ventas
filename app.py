# -*- coding: utf-8 -*-
"""
Propuesta Contable de Ventas — interfaz Streamlit.

    streamlit run app.py

Toda la lógica vive en `motor/`; este archivo solo dibuja la pantalla.
"""
from __future__ import annotations

import hmac
import os
from datetime import date

import pandas as pd
import streamlit as st

from motor import __version__
from motor.clasificador_ventas import ConfigEmpresa, actividad_desde_rubro
from motor.conceptos import CONCEPTOS, palabras_clave
from motor.db import url_local
from motor.edicion import aplicar_ediciones
from motor.proceso import aplicar_cambio
from motor.servicio_ventas import Servicio

st.set_page_config(page_title='Propuesta Contable de Ventas', page_icon='🧾', layout='wide')
st.markdown("""
<style>
  .block-container {padding-top: 1.4rem; max-width: 1500px;}
  h1 {font-weight: 700; letter-spacing: -0.02em;}
  [data-testid="stMetric"] {background: #f4f5f7; border-radius: 10px; padding: 8px 12px;}
  .nota {color: #6b7280; font-size: 0.85rem;}
</style>
""", unsafe_allow_html=True)

MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Setiembre', 'Octubre', 'Noviembre', 'Diciembre']
ACTIVIDADES = {'comercial': 'Comercial (compra y revende: mercaderías)', 'servicios': 'Servicios',
               'industrial': 'Industrial (fabrica: productos terminados)', 'mixta': 'Mixta (bienes y servicios)'}
MEDIOS_PAGO = {'': '(vacío)', '001': '001 Depósito en cuenta', '003': '003 Transferencia de fondos', '005': '005 Tarjeta de débito',
               '006': '006 Tarjeta de crédito', '008': '008 Efectivo sin utilizar medios de pago', '009': '009 Efectivo medios de pago',
               '999': '999 Otros medios de pago'}
ESTADOS = {'ok': 'Listo', 'revisar': 'Revisar', 'anulado': 'Anulado', 'hueco_anulado': 'Hueco → anulado',
           'faltante': 'XML faltante', 'excluido': 'Excluido'}


def _s(v) -> str:
    """Texto seguro desde una celda del editor (None / NaN → '')."""
    if v is None or (isinstance(v, float) and v != v):
        return ''
    return str(v).strip()


def secreto(nombre: str) -> str | None:
    try:
        v = st.secrets.get(nombre)
    except Exception:
        v = None
    return v or os.environ.get(nombre)


# ----------------------------------------------------------------------------- acceso
def acceso() -> bool:
    clave = secreto('APP_PASSWORD')
    if not clave:
        return True
    if st.session_state.get('acceso_ok'):
        return True
    st.title('🧾 Propuesta Contable de Ventas')
    with st.form('login'):
        pw = st.text_input('Clave de acceso', type='password')
        if st.form_submit_button('Entrar', type='primary'):
            if hmac.compare_digest(pw, clave):
                st.session_state.acceso_ok = True
                st.rerun()
            st.error('Clave incorrecta.')
    return False


if not acceso():
    st.stop()


@st.cache_resource
def servicio() -> tuple[Servicio, str]:
    """Devuelve (servicio, aviso). Si DATABASE_URL falla, sigue con la base temporal y avisa."""
    url = secreto('DATABASE_URL')
    clave_ia = secreto('ANTHROPIC_API_KEY')
    if url and ('usuario:clave@host' in url or '@host:' in url or url.strip().endswith('...')):
        url = None          # es el texto de ejemplo: se trata como si no hubiera base configurada
    if not url:
        return Servicio(db_url=url_local(), api_key=clave_ia), ''
    try:
        return Servicio(db_url=url, api_key=clave_ia), ''
    except Exception as ex:   # base caída, clave equivocada, etc.: la app no debe quedar inutilizable
        return Servicio(db_url=url_local(), api_key=clave_ia), (f'No se pudo conectar a DATABASE_URL ({ex.__class__.__name__}); '
                                                          'se usa la base temporal. Revise la dirección en Secrets.')


S, aviso_db = servicio()
ss = st.session_state
ss.setdefault('ruc', None)
ss.setdefault('lote', None)
ss.setdefault('ctx', None)
ss.setdefault('ver', 0)

# ----------------------------------------------------------------------------- barra lateral
with st.sidebar:
    st.markdown('### 🏢 Empresa')
    empresas = S.empresas()
    opciones = {f"{e['nombre']} · {e['ruc']}": e['ruc'] for e in empresas}
    etiquetas = ['➕ Nueva empresa'] + list(opciones)
    idx = 1 + list(opciones.values()).index(ss.ruc) if ss.ruc in opciones.values() else 0
    sel = st.selectbox('Empresa', etiquetas, index=idx, label_visibility='collapsed',
                       help='Escriba para buscar por nombre o RUC')
    if sel == '➕ Nueva empresa':
        with st.form('nueva'):
            ruc_n = st.text_input('RUC (11 dígitos)', max_chars=11)
            nom_n = st.text_input('Razón social')
            rub_n = st.text_area('Rubro (opcional)', placeholder='Ej.: compra y revende joyas; asesoría contable…', height=70)
            act_n = st.selectbox('Actividad', list(ACTIVIDADES), format_func=ACTIVIDADES.get,
                                 help='Si escribe el rubro, se propone sola. Puede cambiarla después.')
            if st.form_submit_button('Crear empresa', type='primary'):
                r = ruc_n.strip()
                if len(r) != 11 or not r.isdigit():
                    st.error('El RUC debe tener 11 dígitos.')
                elif not nom_n.strip():
                    st.error('Indique la razón social.')
                else:
                    S.crear_empresa(r, nom_n.strip().upper(), rub_n.strip(), actividad_desde_rubro(rub_n) or act_n)
                    ss.ruc, ss.lote = r, None
                    st.rerun()
    else:
        if opciones[sel] != ss.ruc:
            ss.ruc, ss.lote = opciones[sel], None
    if ss.ruc:
        e = S.db.empresa(ss.ruc) or {}
        _, mapa_ok = S.mapa(ss.ruc)
        n_ent = len(S.db.entidades(ss.ruc))
        st.markdown(f"{'✅' if e.get('plan_cargado') else '⬜'} Plan de cuentas  \n"
                    f"{'✅' if mapa_ok else '⬜'} Mapa de conceptos confirmado  \n"
                    f"{'✅' if n_ent else '⬜'} Entidades (opcional): {n_ent}")
    tcs = S.historial_tc()
    st.markdown('### 💱 Tipo de cambio')
    st.caption('Meses cargados: ' + (', '.join(tcs.meses_cargados()[-6:]) or 'ninguno'))
    st.divider()
    st.caption(f'v{__version__} · IA: {"activa" if S.api_key else "sin clave (solo reglas)"} · '
               f'Base: {"SQLite local (temporal)" if S.db.es_sqlite else "Postgres"}')

st.title('🧾 Propuesta Contable de Ventas')
st.caption('XML de ventas del mes → Excel listo para importar en NewContaSis (Registro de Ventas), con cuentas, '
           'tipo de cambio, anulados y anticipos.')
if aviso_db:
    st.error(aviso_db, icon='⚠️')
elif S.db.es_sqlite:
    st.info('Sin DATABASE_URL: los datos se guardan en una base temporal. En Streamlit Cloud se borran al reiniciar la app.', icon='ℹ️')

if not ss.ruc:
    st.info('Elija o cree una empresa en la barra lateral.')
    st.stop()

cfg: ConfigEmpresa = S.config(ss.ruc)
t_emp, t_plan, t_tc, t_ventas = st.tabs(['① Empresa', '② Plan y conceptos', '③ Tipo de cambio', '④ Ventas del mes'])

# ============================================================================= ① EMPRESA
with t_emp:
    with st.form('cfg'):
        c1, c2 = st.columns(2)
        nombre = c1.text_input('Razón social', cfg.nombre)
        c1.text_input('RUC', cfg.ruc, disabled=True)
        rubro = c1.text_area('Rubro (opcional)', cfg.rubro, height=80,
                             help='Texto libre: a qué se dedica. Ayuda a proponer la actividad y le da contexto a la IA.')
        actividad = c1.selectbox('Actividad', list(ACTIVIDADES), index=list(ACTIVIDADES).index(cfg.actividad),
                                 format_func=ACTIVIDADES.get)
        modo = c2.radio('Ventas al contado', ['directo', 'provision'], index=0 if cfg.modo_contado == 'directo' else 1,
                        format_func={'directo': 'Directo a caja (AD = 1011 soles / 1012 dólares)',
                                     'provision': 'Provisión + cobro (AD = 1212; NewContaSis genera el cobro)'}.get)
        tc_tipo = c2.radio('Tipo de cambio', ['venta', 'compra'], index=0 if cfg.tc_tipo == 'venta' else 1, horizontal=True)
        usd = c2.checkbox('Convertir dólares a soles (normalmente no)', cfg.usd_en_soles,
                          help='Por defecto los comprobantes en dólares van en dólares, con V = D y el TC en Q.')
        nc_neg = c2.checkbox('Notas de crédito en negativo (normalmente no)', cfg.nc_negativo,
                             help='Por defecto van en positivo: el tipo 07 indica que es nota de crédito.')
        ant = c2.checkbox('Anticipos a 1221 y asiento Debe 1221 / Haber 70 en la factura final', cfg.anticipo_pasivo)
        c3, c4, c5 = st.columns(3)
        cond_b = c3.selectbox('Boleta sin forma de pago', ['CON', 'CRE'], index=0 if cfg.cond_boleta == 'CON' else 1)
        cond_f = c3.selectbox('Factura sin forma de pago', ['CRE', 'CON'], index=0 if cfg.cond_factura == 'CRE' else 1)
        mp = c3.selectbox('Medio de pago por defecto (contado)', list(MEDIOS_PAGO), format_func=MEDIOS_PAGO.get,
                          index=list(MEDIOS_PAGO).index(cfg.medio_pago_defecto) if cfg.medio_pago_defecto in MEDIOS_PAGO else 0)
        ents = {e['codigo']: e['nombre'] for e in S.db.entidades(ss.ruc)}
        if ents:
            ops_ent = sorted(set(ents) | {cfg.codigo_anulado, cfg.codigo_boleta})
            etq = lambda c: f"{c} – {ents.get(c, '')}".strip(' –')
            cod_an = c4.selectbox('Entidad para ANULADOS (opcional)', ops_ent, index=ops_ent.index(cfg.codigo_anulado), format_func=etq,
                                  help='Elija la entidad que la empresa usa para anular. Si no elige, se usa 00000000000.')
            nom_an = ents.get(cod_an, cfg.nombre_anulado)
            cod_bo = c5.selectbox('Entidad para BOLETA sin documento', ops_ent, index=ops_ent.index(cfg.codigo_boleta), format_func=etq)
            nom_bo = ents.get(cod_bo, cfg.nombre_boleta)
        else:
            cod_an = c4.text_input('Código de entidad para ANULADOS (opcional)', cfg.codigo_anulado, max_chars=11,
                                   help='Si lo deja vacío se usa 00000000000. Cargue las entidades para elegirla de una lista.')
            nom_an = c4.text_input('Nombre de entidad ANULADO', cfg.nombre_anulado)
            cod_bo = c5.text_input('Código de BOLETA sin documento', cfg.codigo_boleta, max_chars=11)
            nom_bo = c5.text_input('Nombre de BOLETA sin documento', cfg.nombre_boleta)
        vinc = st.text_area('RUC de empresas vinculadas (uno por línea, opcional)', '\n'.join(cfg.vinculados), height=70)
        if st.form_submit_button('Guardar configuración', type='primary'):
            if actividad != cfg.actividad:
                cfg.actividad_origen = 'usuario'
            cfg.nombre, cfg.rubro, cfg.actividad, cfg.modo_contado = nombre.strip().upper(), rubro.strip(), actividad, modo
            cfg.tc_tipo, cfg.usd_en_soles, cfg.nc_negativo, cfg.anticipo_pasivo = tc_tipo, usd, nc_neg, ant
            cfg.cond_boleta, cfg.cond_factura, cfg.medio_pago_defecto = cond_b, cond_f, mp
            cfg.codigo_anulado, cfg.nombre_anulado = cod_an.strip() or '00000000000', nom_an.strip() or 'ANULADO'
            cfg.codigo_boleta, cfg.nombre_boleta = cod_bo.strip() or '03', nom_bo.strip() or 'BOLETA'
            cfg.vinculados = [v.strip() for v in vinc.split() if v.strip().isdigit()]
            S.guardar_config(cfg)
            ss.lote = None
            st.success('Configuración guardada.')
            st.rerun()

    st.markdown('#### Entidades de NewContaSis (opcional)')
    st.caption('Excel "CLIENTES - PROVEEDORES (TODOS)". Sirve para tomar los códigos de ANULADO y BOLETA, y para reconocer '
               'clientes del exterior ya creados. Los nuevos reciben 10000000001, 10000000002…')
    ent_f = st.file_uploader('Excel de entidades', type=['xlsx', 'xls'], key=f'ent_{ss.ruc}')
    if ent_f and st.button('Cargar entidades'):
        try:
            ent, avisos = S.cargar_entidades(ss.ruc, ent_f.getvalue())
            st.success(f'{len(ent)} entidades cargadas.')
            for a in avisos:
                st.info(a)
        except Exception as ex:
            st.error(f'No se pudo leer: {ex}')
    ext = S.db.exterior(ss.ruc)
    if ext:
        with st.expander(f'Clientes del exterior con código asignado por la app ({len(ext)})'):
            st.dataframe(pd.DataFrame(ext)[['codigo', 'nombre', 'pais', 'creado_en']], hide_index=True, use_container_width=True)

    st.markdown('#### Reglas de la empresa (opcional)')
    st.caption('Cuando los ítems contengan estas palabras (o el cliente sea ese RUC), usar esta cuenta. Tienen prioridad sobre todo.')
    regs = S.db.reglas(ss.ruc)
    if regs:
        st.dataframe(pd.DataFrame([{'id': r['id'], 'palabras': ', '.join(r['palabras']), 'RUC cliente': r['ruc_cliente'],
                                    'cuenta': r['cuenta']} for r in regs]), hide_index=True, use_container_width=True)
    with st.form('regla'):
        r1, r2, r3, r4 = st.columns([3, 2, 2, 1])
        pal = r1.text_input('Palabras (separadas por coma)', placeholder='SERVICIO LABORAL')
        rc = r2.text_input('RUC del cliente (opcional)')
        cta = r3.text_input('Cuenta')
        if r4.form_submit_button('Agregar'):
            if S.plan(ss.ruc).get(cta.strip()):
                S.db.guardar_regla(ss.ruc, pal, [p.strip() for p in pal.split(',') if p.strip()], cta.strip(), rc.strip())
                st.rerun()
            else:
                st.error('Esa cuenta no existe en el plan.')
    if regs:
        borrar = st.selectbox('Borrar regla', [''] + [str(r['id']) for r in regs])
        if borrar and st.button('Borrar'):
            S.db.borrar_regla(int(borrar))
            st.rerun()

# ============================================================================= ② PLAN Y CONCEPTOS
with t_plan:
    plan_f = st.file_uploader('Plan de cuentas (Excel exportado de NewContaSis)', type=['xlsx', 'xls'], key=f'plan_{ss.ruc}')
    if plan_f and st.button('Cargar plan', type='primary'):
        try:
            p, avisos = S.cargar_plan(ss.ruc, plan_f.getvalue())
            st.success(f'{len(p.cuentas)} cuentas cargadas ({len(p.hojas())} imputables). Revise y confirme el mapa de conceptos.')
            for a in avisos:
                st.warning(a)
            ss.lote = None
        except Exception as ex:
            st.error(f'No se pudo leer el plan: {ex}')
    plan = S.plan(ss.ruc)
    if not plan.cuentas:
        st.info('Cargue el plan de cuentas de la empresa para continuar.')
    else:
        mapa, confirmado = S.mapa(ss.ruc, plan)
        st.markdown('#### Mapa de conceptos ' + ('✅ confirmado' if confirmado else '⚠️ sin confirmar'))
        st.caption('Cada concepto se traduce a una cuenta del plan de esta empresa. Si la cuenta tiene subcuentas '
                   '(ej. 70321 → 7032101, 7032102…), la app elige la subcuenta por las palabras de los ítems.')
        df = pd.DataFrame([{'Concepto': k, 'Descripción': CONCEPTOS[k].nombre,
                            'Obligatorio': '✔' if CONCEPTOS[k].obligatorio else '',
                            'Cuenta': d.cuenta,
                            'Cuenta en el plan': (plan.get(d.cuenta).descripcion[:60] if d.cuenta and plan.get(d.cuenta) else ''),
                            'Subcuentas': ', '.join(d.hojas[:6]) + (' …' if len(d.hojas) > 6 else '')} for k, d in mapa.items()])
        ed = st.data_editor(df, hide_index=True, use_container_width=True, disabled=['Concepto', 'Descripción', 'Obligatorio',
                                                                                     'Cuenta en el plan', 'Subcuentas'],
                            key=f'mapa_{ss.ruc}_{ss.ver}')
        if st.button('Confirmar mapa', type='primary'):
            malas = [f"{r['Concepto']}: {_s(r['Cuenta'])}" for _, r in ed.iterrows() if _s(r['Cuenta']) and not plan.get(_s(r['Cuenta']))]
            faltan = [r['Concepto'] for _, r in ed.iterrows() if CONCEPTOS[r['Concepto']].obligatorio and not _s(r['Cuenta'])]
            if malas:
                st.error('Cuentas que no existen en el plan: ' + '; '.join(malas))
            elif faltan:
                st.error('Faltan conceptos obligatorios: ' + ', '.join(faltan))
            else:
                S.guardar_mapa(ss.ruc, {r['Concepto']: _s(r['Cuenta']) for _, r in ed.iterrows()})
                ss.ver += 1
                ss.lote = None
                st.success('Mapa confirmado.')
                st.rerun()

        grupos = {d.cuenta: d.hojas for d in mapa.values() if d.es_grupo}
        if grupos:
            st.markdown('#### Palabras clave de subcuentas')
            st.caption('La app compara estas palabras con la descripción de los ítems. Por defecto salen de la descripción '
                       'de la subcuenta; puede añadir más (ej. LABORAL para la subcuenta de asesoría).')
            pal_guard = S.db.palabras(ss.ruc)
            filas = []
            for g, hojas in grupos.items():
                for h in hojas:
                    filas.append({'Grupo': g, 'Subcuenta': h, 'Descripción': plan.get(h).descripcion[:60],
                                  'Palabras clave': ', '.join(pal_guard.get(h) or palabras_clave(plan.get(h).descripcion))})
            ed2 = st.data_editor(pd.DataFrame(filas), hide_index=True, use_container_width=True,
                                 disabled=['Grupo', 'Subcuenta', 'Descripción'], key=f'pal_{ss.ruc}_{ss.ver}')
            if st.button('Guardar palabras clave'):
                for _, r in ed2.iterrows():
                    S.db.guardar_palabras(ss.ruc, r['Subcuenta'], [p.strip().upper() for p in _s(r['Palabras clave']).split(',') if p.strip()])
                ss.lote = None
                st.success('Palabras guardadas.')

# ============================================================================= ③ TIPO DE CAMBIO
with t_tc:
    st.caption('PDF "SUNAT – Tipo de Cambio" del mes (uno o varios). Es común a todas las empresas y se acumula. '
               'Si un día no tiene publicación se usa el del día inmediato anterior.')
    tcf = st.file_uploader('PDF de SUNAT (o Excel/CSV fecha | compra | venta)', type=['pdf', 'xlsx', 'csv'],
                           accept_multiple_files=True, key='tc_up')
    if tcf and st.button('Cargar tipo de cambio', type='primary'):
        for f in tcf:
            try:
                items = S.cargar_tc(f.name, f.getvalue())
                st.success(f'{f.name}: {len(items)} días ({items[0].fecha:%m/%Y}).')
            except Exception as ex:
                st.error(f'{f.name}: {ex}')
        ss.lote = None
    tcs = S.historial_tc()
    if len(tcs):
        meses = tcs.meses_cargados()
        mes_v = st.selectbox('Ver mes', meses[::-1])
        datos = [{'Fecha': d.strftime('%d/%m/%Y'), 'Compra': tcs.buscar(d)[0].compra, 'Venta': tcs.buscar(d)[0].venta}
                 for d in tcs.fechas() if d.strftime('%Y-%m') == mes_v]
        st.dataframe(pd.DataFrame(datos), hide_index=True, use_container_width=False)

# ============================================================================= ④ VENTAS DEL MES
with t_ventas:
    hoy = date.today()
    pm = date(hoy.year, hoy.month, 1)
    def_anio, def_mes = (pm.year, pm.month - 1) if pm.month > 1 else (pm.year - 1, 12)
    c1, c2, c3 = st.columns([1, 1, 2])
    anio = c1.selectbox('Año', list(range(hoy.year - 3, hoy.year + 1))[::-1], index=0 if def_anio == hoy.year else 1)
    mes = c2.selectbox('Mes', list(range(1, 13)), index=def_mes - 1, format_func=lambda m: MESES[m - 1])
    usar_ia = c3.toggle('Usar IA para los casos dudosos', value=bool(S.api_key), disabled=not S.api_key,
                        help='Solo se consulta cuando las reglas no llegan al 90 %. Requiere ANTHROPIC_API_KEY en Secrets.')
    periodo = f'{anio}-{mes:02d}'
    st.caption('Solo entran los comprobantes emitidos en el mes elegido; los de otros meses salen en Excluidos.')
    xmls = st.file_uploader('XML de ventas (sueltos o ZIP). También comunicaciones de baja y resúmenes diarios.',
                            type=['xml', 'zip'], accept_multiple_files=True, key=f'xml_{ss.ruc}')
    if st.button('GENERAR PROPUESTA', type='primary', disabled=not xmls, use_container_width=True):
        with st.spinner('Leyendo y clasificando…'):
            lote, ctx = S.procesar(ss.ruc, periodo, [(f.name, f.getvalue()) for f in xmls], usar_ia)
        ss.lote, ss.ctx, ss.ver = lote, ctx, ss.ver + 1
        st.rerun()

    lote, ctx = ss.lote, ss.ctx
    if lote is None or lote.ruc != ss.ruc:
        st.stop()

    for b in lote.bloqueos:
        st.error(b, icon='⛔')
    exp = lote.exportables
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric('Para importar', len(exp))
    m2.metric('Revisar', len(lote.por_estado('revisar')))
    m3.metric('Huecos → anulado', len(lote.por_estado('hueco_anulado')))
    m4.metric('XML faltantes', len(lote.por_estado('faltante')))
    m5.metric('Excluidos', len(lote.por_estado('excluido')))
    m6.metric('Total S/', f"{sum(float(f.c.importe_total) * (f.tc if f.moneda == 'D' and f.tc else 1) for f in exp if f.c is not None):,.0f}")

    plan = ctx.plan
    hojas = [c.codigo for c in plan.hojas()]
    op_base = sorted({c for c in hojas if c[:2] in ('70', '75', '12', '16', '45', '46', '49')} | {f.cuenta_base for f in lote.filas if f.cuenta_base})
    op_total = sorted({c for c in hojas if c[:2] in ('10', '12', '13', '14', '16', '17')} | {f.cuenta_total for f in lote.filas if f.cuenta_total})

    def tabla(filas, editable=True, key=''):
        if not filas:
            st.caption('Sin filas.')
            return
        df = pd.DataFrame([{
            'clave': f.clave, '✓': False, '': f.semaforo, 'Estado': ESTADOS[f.estado], 'Fecha': f.fecha, 'Tipo': f.tipo,
            'Serie-Número': f.id, 'Cliente': f'{f.cli_doc} {f.cli_nombre}'.strip(), 'Mon': f.moneda,
            'Total': float(f.c.importe_total) if f.c is not None else 0.0, 'TC': f.tc,
            'Cond': f.condicion, 'AB cuenta': f.cuenta_base,
            'AB descripción': (plan.get(f.cuenta_base).descripcion[:40] if plan.get(f.cuenta_base) else ''),
            'AD cuenta': f.cuenta_total, 'Anticipo': f.es_anticipo, 'Glosa': f.glosa,
            'Por qué': f.explicacion or f.motivo, 'Avisos': ' · '.join(f.avisos)} for f in filas])
        cfgcols = {
            'clave': None,
            '✓': st.column_config.CheckboxColumn('✓', help='Seleccionar para cambios en bloque', width='small'),
            'Total': st.column_config.NumberColumn(format='%.2f'),
            'TC': st.column_config.NumberColumn(format='%.3f'),
            'Cond': st.column_config.SelectboxColumn(options=['CON', 'CRE'], width='small'),
            'AB cuenta': st.column_config.SelectboxColumn(options=op_base, help='Cuenta de ventas (elemento 7) o 1221 si es anticipo'),
            'AD cuenta': st.column_config.SelectboxColumn(options=op_total, help='Cuenta del total: 1011/1012 contado, 1212 crédito'),
            'Anticipo': st.column_config.CheckboxColumn(help='Marque si es un comprobante de anticipo (va a 1221)'),
            'Glosa': st.column_config.TextColumn(max_chars=60),
        }
        bloqueadas = [c for c in df.columns if c not in ('✓', 'Cond', 'AB cuenta', 'AD cuenta', 'Anticipo', 'Glosa')]
        ed = st.data_editor(df, hide_index=True, use_container_width=True, column_config=cfgcols,
                            disabled=bloqueadas if editable else True, key=f'ed_{key}_{ss.ver}', height=min(38 * len(df) + 40, 560))
        cambio, sel = aplicar_ediciones(ed.to_dict('records'), {f.clave: f for f in lote.filas}, ctx)
        if cambio:
            S.recalcular_anticipos(lote, ctx)
            ss.ver += 1
            st.rerun()
        return sel

    tabs = st.tabs([f'Propuesta ({len(lote.por_estado("ok"))})', f'Revisar ({len(lote.por_estado("revisar"))})',
                    f'Anulados y faltantes ({len(lote.por_estado("anulado", "hueco_anulado", "faltante"))})',
                    f'Anticipos ({len(lote.anticipos)})', f'Excluidos ({len(lote.por_estado("excluido")) + len(lote.no_procesados)})',
                    f'Clientes del exterior nuevos ({len(lote.entidades_nuevas)})'])
    with tabs[0]:
        sel = tabla(lote.por_estado('ok'), key='ok') or []
    with tabs[1]:
        st.caption('Estas filas no se exportan hasta corregirlas (cuenta, TC, RUC, cuadre…).')
        sel_r = tabla(lote.por_estado('revisar'), key='rev') or []
        sel += sel_r

    # ----- cambios en bloque
    with st.expander('✏️ Cambios en bloque (cuenta de ventas AB o cuenta del total AD)', expanded=False):
        b1, b2, b3, b4 = st.columns([1.2, 1.2, 2, 1])
        destino = b1.selectbox('Aplicar a', ['Seleccionadas (✓)', 'Todas', 'Solo contado', 'Solo crédito'])
        campo = b2.selectbox('Columna', ['AD cuenta total', 'AB cuenta de ventas'])
        opciones_b = op_total if campo.startswith('AD') else op_base
        etiqueta = lambda c: f"{c} – {plan.get(c).descripcion[:50] if plan.get(c) else ''}"
        cuenta_b = b3.selectbox('Cuenta', opciones_b, format_func=etiqueta,
                                index=opciones_b.index('1212') if campo.startswith('AD') and '1212' in opciones_b else 0)
        if b4.button('Aplicar', use_container_width=True):
            filas = [f for f in lote.filas if f.estado in ('ok', 'revisar') and f.c is not None]
            filas = {'Seleccionadas (✓)': sel, 'Todas': filas, 'Solo contado': [f for f in filas if f.condicion == 'CON'],
                     'Solo crédito': [f for f in filas if f.condicion == 'CRE']}[destino]
            for f in filas:
                aplicar_cambio(f, ctx, **({'cuenta_total': cuenta_b} if campo.startswith('AD') else {'cuenta_base': cuenta_b}))
            S.recalcular_anticipos(lote, ctx)
            ss.ver += 1
            st.rerun()
        if st.button('↺ Restaurar AD según condición (1011/1012 contado · 1212 crédito)'):
            from motor.clasificador_ventas import cuenta_total
            for f in lote.filas:
                if f.c is not None and f.estado in ('ok', 'revisar'):
                    f.total_manual = False
                    f.cuenta_total = cuenta_total(f, ctx)
            ss.ver += 1
            st.rerun()

    with tabs[2]:
        st.caption('Huecos en la numeración: se proponen como anulados con la entidad '
                   f'{ctx.cfg.codigo_anulado} ({ctx.cfg.nombre_anulado}). Solo se exportan los confirmados. '
                   'Si en realidad falta el XML, cambie el estado a "XML faltante".')
        filas_an = lote.por_estado('anulado', 'hueco_anulado', 'faltante')
        if filas_an:
            dfa = pd.DataFrame([{'clave': f.clave, 'Confirmar': f.confirmado if f.estado == 'hueco_anulado' else f.estado == 'anulado',
                                 'Estado': f.estado, 'Fecha': f.fecha, 'Tipo': f.tipo, 'Serie-Número': f.id,
                                 'Entidad': f'{f.cli_doc} {f.cli_nombre}'.strip(), 'Motivo': f.motivo} for f in filas_an])
            eda = st.data_editor(dfa, hide_index=True, use_container_width=True, key=f'an_{ss.ver}',
                                 column_config={'clave': None,
                                                'Estado': st.column_config.SelectboxColumn(options=['hueco_anulado', 'faltante', 'anulado'])},
                                 disabled=['Fecha', 'Tipo', 'Serie-Número', 'Entidad', 'Motivo'])
            por_clave = {f.clave: f for f in lote.filas}
            cambio = False
            for _, r in eda.iterrows():
                f = por_clave[r['clave']]
                if f.estado != 'anulado' and r['Estado'] != f.estado and r['Estado'] in ('hueco_anulado', 'faltante'):
                    aplicar_cambio(f, ctx, estado=r['Estado'])
                    cambio = True
                if f.estado == 'hueco_anulado' and bool(r['Confirmar']) != f.confirmado:
                    aplicar_cambio(f, ctx, confirmado=bool(r['Confirmar']))
                    cambio = True
            if cambio:
                ss.ver += 1
                st.rerun()
            if st.button('Confirmar todos los anulados propuestos'):
                for f in lote.por_estado('hueco_anulado'):
                    f.confirmado = True
                ss.ver += 1
                st.rerun()
    with tabs[3]:
        st.caption('La factura/boleta final se importa con su base neta. Por cada anticipo aplicado registre a mano '
                   'el asiento complementario Debe 1221 / Haber 70.')
        if lote.anticipos:
            st.dataframe(pd.DataFrame([{'Final': a['final'], 'Fecha': a['fecha'], 'Anticipo': a['anticipo'],
                                        'Monto anticipo': float(a['monto_anticipo']), 'Base a registrar': float(a['base_soles']),
                                        'Moneda': a['moneda'],
                                        'Debe': a['debe'] if a['proponer'] else '', 'Haber': a['haber'] if a['proponer'] else '',
                                        'Anticipo registrado en': a['origen'], 'Acción': a['accion']} for a in lote.anticipos]),
                         hide_index=True, use_container_width=True)
    with tabs[4]:
        filas_ex = [{'Archivo / comprobante': f.c.archivo if f.c else f.id, 'Fecha': f.fecha, 'Motivo': f.motivo}
                    for f in lote.por_estado('excluido')] + [{'Archivo / comprobante': n, 'Fecha': '', 'Motivo': m} for n, m in lote.no_procesados]
        if filas_ex:
            st.dataframe(pd.DataFrame(filas_ex), hide_index=True, use_container_width=True)
    with tabs[5]:
        if lote.entidades_nuevas:
            st.dataframe(pd.DataFrame(lote.entidades_nuevas), hide_index=True, use_container_width=True)

    # ----- descarga
    st.divider()
    if ctx.cfg.modo_contado == 'directo' and any(f.condicion == 'CON' and f.c is not None for f in exp):
        st.warning('Hay ventas al contado con la caja en AD. Al importar en NewContaSis marque **"Generar solo – Asientos de Venta"** '
                   'para que no se genere un segundo asiento de cobro.', icon='⚠️')
    sin_conf = len([f for f in lote.por_estado('hueco_anulado') if not f.confirmado])
    if sin_conf:
        st.info(f'{sin_conf} huecos propuestos como anulados sin confirmar: no irán en el Excel.')
    salida = S.archivos_salida(lote, ctx)
    d1, d2, d3 = st.columns(3)
    nombres = list(salida)

    def registrar():
        S.registrar_descarga(lote, ctx)
        st.session_state['registrado'] = lote.periodo

    d1.download_button('⬇️ Excel para NewContaSis', salida[nombres[0]], nombres[0], type='primary',
                       disabled=bool(lote.bloqueos) or not exp, use_container_width=True, on_click=registrar,
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    d2.download_button('⬇️ Excel de revisión', salida[nombres[1]], nombres[1], use_container_width=True,
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    d3.download_button('⬇️ Reporte', salida[nombres[2]], nombres[2], use_container_width=True, mime='text/plain')
    if lote.bloqueos:
        st.caption('El Excel para NewContaSis se habilita cuando no hay bloqueos.')
    if ss.get('registrado') == lote.periodo:
        st.success('Lote registrado: la app recordará estas cuentas, los códigos de clientes del exterior y la numeración.')
