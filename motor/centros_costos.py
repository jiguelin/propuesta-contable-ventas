# -*- coding: utf-8 -*-
"""
motor/centros_costos.py — Excel de centros de costos exportado de NewContaSis (opcional por empresa).

Formato observado (CONTADEUS):  filas 2-4 empresa/giro/RUC · "CENTROS DE COSTOS" · cabecera COD | DESCRIPCION | DEBE | HABER
  01   CC 01 SERVICIOS          (agrupador)
  0104 VENTA SERVICIOS          → ventas de servicios
  0105 VENTA PRODUCTOS          → ventas de mercaderías / productos
"""
from __future__ import annotations

import io
import re

import openpyxl

from .conceptos import tokens


def _limpio(v) -> str:
    return ' '.join(str(v).split()) if v is not None else ''


def leer_centros_excel(origen) -> tuple[list[dict], str]:
    """Devuelve ([{'codigo', 'descripcion', 'es_hoja'}], ruc del encabezado)."""
    if isinstance(origen, (bytes, bytearray)):
        origen = io.BytesIO(origen)
    ws = openpyxl.load_workbook(origen, read_only=True, data_only=True).active
    filas = list(ws.iter_rows(values_only=True))
    ruc = ''
    for r in filas[:8]:
        for v in r:
            m = re.search(r'R\.?U\.?C\.?\s*:?\s*(\d{11})', _limpio(v))
            if m:
                ruc = m.group(1)
    idx = c_cod = c_desc = None
    for i, r in enumerate(filas[:40]):
        vals = [_limpio(v).upper() for v in r]
        if any(v in ('COD', 'CODIGO', 'CÓDIGO') for v in vals) and any(v.startswith('DESCRIP') for v in vals):
            idx = i
            c_cod = next(j for j, v in enumerate(vals) if v in ('COD', 'CODIGO', 'CÓDIGO'))
            c_desc = next(j for j, v in enumerate(vals) if v.startswith('DESCRIP'))
            break
    items = []
    if idx is None:      # formato simple: código | descripción
        for r in filas:
            vals = [_limpio(v) for v in r if _limpio(v)]
            if len(vals) >= 2 and re.fullmatch(r'[0-9A-Z]{1,9}', vals[0]):
                items.append({'codigo': vals[0], 'descripcion': vals[1]})
    else:
        for r in filas[idx + 1:]:
            cod = _limpio(r[c_cod]) if c_cod < len(r) else ''
            if not cod or cod.upper().startswith('GENERADO') or len(cod) > 9:
                continue
            items.append({'codigo': cod, 'descripcion': _limpio(r[c_desc]) if c_desc < len(r) else ''})
    codigos = sorted(i['codigo'] for i in items)
    padres = {a for a, b in zip(codigos, codigos[1:]) if b.startswith(a)}
    for i in items:
        i['es_hoja'] = i['codigo'] not in padres
    return items, ruc


def sugerir(items: list[dict]) -> dict[str, str]:
    """Propone {'servicios': código, 'mercaderia': código} según las descripciones."""
    res = {'servicios': '', 'mercaderia': ''}
    for i in items:
        if not i.get('es_hoja', True):
            continue
        t = ' '.join(tokens(i['descripcion']))
        if 'VENTA' not in t and 'INGRESO' not in t:
            continue
        if 'SERVICIO' in t and not res['servicios']:
            res['servicios'] = i['codigo']
        elif any(k in t for k in ('PRODUCTO', 'MERCADER', 'BIENES')) and not res['mercaderia']:
            res['mercaderia'] = i['codigo']
    return res
