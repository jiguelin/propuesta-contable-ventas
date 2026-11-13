# -*- coding: utf-8 -*-
"""La pantalla completa se dibuja sin errores (todas las pestañas), con una empresa configurada y una
propuesta con notas de crédito y filas del Excel. Detecta errores como variables usadas fuera de lugar."""
from pathlib import Path

import pytest

from conftest import DATOS, ESTUDIO
from fabrica import comprobante
from test_excel_entrada import EXCEL, excel_ventas

APP = Path(__file__).resolve().parent.parent / 'app.py'


@pytest.fixture
def app_con_empresa(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    from motor.servicio_ventas import Servicio
    url = f'sqlite:///{tmp_path / "app.db"}'
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.delenv('APP_PASSWORD', raising=False)
    s = Servicio(db_url=url)
    s.cargar_tc('tc.pdf', (DATOS / 'tipocambio_2026-09.pdf').read_bytes())
    s.crear_empresa(ESTUDIO, 'EMPRESA PRUEBA', '', 'servicios')
    s.cargar_plan(ESTUDIO, (DATOS / 'plan_prueba.xlsx').read_bytes())
    mapa, _ = s.mapa(ESTUDIO)
    s.guardar_mapa(ESTUDIO, {k: d.cuenta for k, d in mapa.items()})
    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state['ruc'] = ESTUDIO
    return at, s


def test_pantalla_sin_errores(app_con_empresa):
    at, s = app_con_empresa
    at.run()
    assert not at.exception, at.exception


def test_pantalla_con_propuesta_xml_excel_y_nc(app_con_empresa):
    from motor import __version__
    at, s = app_con_empresa
    nc = comprobante('FC01-1', fecha='2026-09-05', tipo='07', nc_ref=('F001-1', '01', '2026-09-03'),
                     items=(('SERVICIO DE ASESORIA', 50.0, 'ZZ'),))
    xml = comprobante('F001-1', fecha='2026-09-03', items=(('SERVICIO DE ASESORIA CONTABLE', 100.0, 'ZZ'),))
    lote, ctx = s.procesar(ESTUDIO, '2026-09', [('f.xml', xml), ('nc.xml', nc), ('v.xlsx', excel_ventas(EXCEL))], usar_ia=False)
    assert any(f.tipo == '07' for f in lote.filas) and any(f.c is not None and f.c.origen == 'excel' for f in lote.filas)
    lote.version = __version__
    at.session_state['lote'], at.session_state['ctx'] = lote, ctx
    at.run()
    assert not at.exception, at.exception
    assert any('se completan desde el Excel' in a.value for a in at.info)
