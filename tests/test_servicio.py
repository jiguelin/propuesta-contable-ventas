# -*- coding: utf-8 -*-
"""Flujo completo con base de datos (SQLite temporal): empresa → plan → mapa → TC → lote → descarga → mes siguiente."""
import pytest

from conftest import DATOS, ESTUDIO, JOYERIA, archivos
from fabrica import comprobante
from motor.proceso import aplicar_cambio
from motor.servicio_ventas import Servicio


@pytest.fixture
def servicio(tmp_path):
    s = Servicio(db_url=f'sqlite:///{tmp_path / "t.db"}')
    s.cargar_tc('tc.pdf', (DATOS / 'tipocambio_2026-09.pdf').read_bytes())
    return s


def preparar(s, ruc, actividad):
    s.crear_empresa(ruc, 'EMPRESA PRUEBA', '', actividad)
    plan, avisos = s.cargar_plan(ruc, (DATOS / 'plan_prueba.xlsx').read_bytes())
    mapa, confirmado = s.mapa(ruc)
    assert not confirmado
    s.guardar_mapa(ruc, {k: d.cuenta for k, d in mapa.items()})
    return avisos


def test_flujo_completo_y_actividad_del_usuario(servicio):
    avisos = preparar(servicio, JOYERIA, 'comercial')
    assert servicio.config(JOYERIA).actividad == 'comercial'      # el giro del plan no la cambia
    assert any('se mantiene' in a for a in avisos) or any('RUC' in a for a in avisos)
    _, ent_avisos = servicio.cargar_entidades(JOYERIA, (DATOS / 'entidades_prueba.xlsx').read_bytes())
    cfg = servicio.config(JOYERIA)
    assert (cfg.codigo_anulado, cfg.codigo_boleta, cfg.nombre_boleta) == ('0000', '03', 'BOLETA DE VENTA')
    lote, ctx = servicio.procesar(JOYERIA, '2026-09', archivos('joyeria_set'), usar_ia=False)
    assert not lote.bloqueos
    salida = servicio.archivos_salida(lote, ctx)
    assert len(salida) == 3 and all(salida.values())
    lote_id = servicio.registrar_descarga(lote, ctx)
    assert lote_id and servicio.db.comprobante(JOYERIA, '03', 'BOP1', 4501)['cuenta_base'] == '70121'
    assert servicio.db.ultimos_numeros(JOYERIA, '2026-10')[('03', 'BOP1')] == 4501


def test_mapa_sin_confirmar_bloquea(servicio):
    servicio.crear_empresa(ESTUDIO, 'EMPRESA PRUEBA', '', 'servicios')
    servicio.cargar_plan(ESTUDIO, (DATOS / 'plan_prueba.xlsx').read_bytes())
    lote, _ = servicio.procesar(ESTUDIO, '2026-09', [('f.xml', comprobante('F001-1'))], usar_ia=False)
    assert any('no está confirmado' in b for b in lote.bloqueos)


def test_memoria_de_correcciones(servicio):                     # caso 15
    preparar(servicio, ESTUDIO, 'servicios')
    xml = comprobante('F001-10', items=(('SERVICIO DE PLANILLAS Y LABORAL', 300.0, 'ZZ'),))
    lote, ctx = servicio.procesar(ESTUDIO, '2026-09', [('a.xml', xml)], usar_ia=False)
    f = lote.filas[0]
    assert f.cuenta_base == '7032101' and f.confianza < 95      # no coincide con ninguna subcuenta
    aplicar_cambio(f, ctx, cuenta_base='7032103')
    servicio.registrar_descarga(lote, ctx)
    xml2 = comprobante('F001-11', fecha='2026-09-25', items=(('SERVICIO DE PLANILLAS Y LABORAL', 450.0, 'ZZ'),))
    lote2, _ = servicio.procesar(ESTUDIO, '2026-09', [('b.xml', xml2)], usar_ia=False)
    g = lote2.filas[0]
    assert (g.cuenta_base, g.origen) == ('7032103', 'memoria')


def test_hueco_al_inicio_del_mes_y_nc_de_mes_anterior(servicio):
    preparar(servicio, ESTUDIO, 'servicios')
    ago = [('a.xml', comprobante('F001-20', fecha='2026-08-28', items=(('SERVICIO DE CAPACITACION', 200.0, 'ZZ'),)))]
    servicio.cargar_tc('tc.pdf', (DATOS / 'tipocambio_2026-09.pdf').read_bytes())
    lote, ctx = servicio.procesar(ESTUDIO, '2026-08', ago, usar_ia=False)
    for f in lote.filas:                                        # agosto no tiene TC en la prueba: se registra igual
        f.estado = 'ok'
    servicio.registrar_descarga(lote, ctx)
    sep = [('b.xml', comprobante('F001-23', fecha='2026-09-03')),
           ('nc.xml', comprobante('FC01-1', fecha='2026-09-05', tipo='07', nc_ref=('F001-20', '01', ''),
                                  items=(('SERVICIO DE CAPACITACION', 50.0, 'ZZ'),)))]
    lote2, _ = servicio.procesar(ESTUDIO, '2026-09', sep, usar_ia=False)
    huecos = sorted(f.numero for f in lote2.por_estado('hueco_anulado'))
    assert huecos == [21, 22]                                   # sabe que agosto terminó en F001-20
    nc = next(f for f in lote2.filas if f.id == 'FC01-1')
    assert nc.ref_fecha == '2026-08-28' and nc.cuenta_base == '7032103' and nc.estado == 'ok'


def test_migracion_config_antigua():
    from motor.clasificador_ventas import ConfigEmpresa
    vieja = ConfigEmpresa.desde_dict({'ruc': '20000000101', 'usd_en_soles': True, 'nc_negativo': True})
    assert not vieja.usd_en_soles and not vieja.nc_negativo
    nueva = ConfigEmpresa.desde_dict({'ruc': '20000000101', 'usd_en_soles': True, 'version_config': 2})
    assert nueva.usd_en_soles                                       # elección explícita posterior se respeta
