# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from motor.clasificador_ventas import ConfigEmpresa, Contexto  # noqa: E402
from motor.conceptos import sugerir_mapa  # noqa: E402
from motor.entidades import RegistroExterior, leer_entidades_excel  # noqa: E402
from motor.lector import leer_archivos  # noqa: E402
from motor.plan_cuentas import leer_plan_excel  # noqa: E402
from motor.proceso import procesar  # noqa: E402
from motor.tipo_cambio import HistorialTC, leer_pdf_sunat  # noqa: E402

DATOS = RAIZ / 'tests' / 'datos'
JOYERIA, MINERA, ESTUDIO = '20000000101', '20000000102', '20000000100'


@pytest.fixture(scope='session')
def plan():
    return leer_plan_excel(DATOS / 'plan_prueba.xlsx')


@pytest.fixture(scope='session')
def mapa(plan):
    return sugerir_mapa(plan)


@pytest.fixture(scope='session')
def tc():
    return HistorialTC(leer_pdf_sunat((DATOS / 'tipocambio_2026-09.pdf').read_bytes()))


@pytest.fixture(scope='session')
def entidades():
    return leer_entidades_excel(DATOS / 'entidades_prueba.xlsx')


def archivos(carpeta: str) -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted((DATOS / carpeta).glob('*.xml'))]


@pytest.fixture
def correr(plan, mapa, tc, entidades):
    """correr(ruc, actividad, archivos, periodo, **config) → (lote, ctx)"""
    def _correr(ruc, actividad, arch, periodo='2026-09', registros_exterior=None, buscar_original=None, tc_extra=(), **kw):
        cfg = ConfigEmpresa(ruc=ruc, nombre='PRUEBA', actividad=actividad, codigo_anulado='0000',
                            codigo_boleta='03', nombre_boleta='BOLETA DE VENTA', **kw)
        historial = HistorialTC(list(tc._d.values()) + list(tc_extra))
        ctx = Contexto(cfg, plan, mapa, historial, RegistroExterior(registros_exterior or [], entidades),
                       buscar_original=buscar_original)
        return procesar(ctx, periodo, leer_archivos(arch)), ctx
    return _correr
