# -*- coding: utf-8 -*-
"""
motor/edicion.py — Aplica lo que la persona cambió en la tabla editable de la pantalla
(condición, cuenta de ventas AB, cuenta del total AD, anticipo, glosa) sobre las filas del lote.
Separado de app.py para poder probarlo sin Streamlit.
"""
from __future__ import annotations

from .clasificador_ventas import Fila
from .proceso import aplicar_cambio


def texto(v) -> str:
    """Texto seguro desde una celda (None / NaN → '')."""
    if v is None or (isinstance(v, float) and v != v):
        return ''
    return str(v).strip()


def marca(v) -> bool:
    """Casilla del editor (bool, numpy.bool_, None o NaN) → bool."""
    if v is None or (isinstance(v, float) and v != v):
        return False
    return bool(v)


def aplicar_ediciones(registros: list[dict], por_clave: dict[str, Fila], ctx) -> tuple[bool, list[Fila]]:
    """registros = filas del editor como dict (clave, ✓, Cond, AB cuenta, AD cuenta, Anticipo, Glosa).
    Devuelve (hubo_cambios, filas_seleccionadas)."""
    cambio, sel = False, []
    for r in registros:
        f = por_clave.get(r.get('clave'))
        if f is None:
            continue
        if marca(r.get('✓')):
            sel.append(f)
        cambios = {}
        cond = texto(r.get('Cond'))
        if cond in ('CON', 'CRE') and cond != f.condicion:
            cambios['condicion'] = cond
        if 'AB cuenta' in r and texto(r['AB cuenta']) != f.cuenta_base:
            cambios['cuenta_base'] = texto(r['AB cuenta'])
        if 'AD cuenta' in r and texto(r['AD cuenta']) != f.cuenta_total:
            cambios['cuenta_total'] = texto(r['AD cuenta'])
        if 'Anticipo' in r and marca(r['Anticipo']) != f.es_anticipo:
            cambios['es_anticipo'] = marca(r['Anticipo'])
        if 'CC' in r and texto(r['CC']) != f.centro_costos:
            cambios['centro_costos'] = texto(r['CC'])
        if 'Glosa' in r and texto(r['Glosa']) != f.glosa:
            cambios['glosa'] = texto(r['Glosa'])
        if cambios:
            aplicar_cambio(f, ctx, **cambios)
            cambio = True
    return cambio, sel
