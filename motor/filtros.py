# -*- coding: utf-8 -*-
"""
motor/filtros.py — Buscador y filtros de la tabla de revisión.

El buscador encuentra un comprobante por RUC/DNI del cliente, razón social (sin importar tildes ni
mayúsculas), serie, número o serie-número ("E001-1099", "E001 1099", "1099", "000001099").
"""
from __future__ import annotations

import re

from .clasificador_ventas import Fila
from .conceptos import sin_tildes


def _norm(s: str) -> str:
    return ' '.join(sin_tildes(s or '').upper().split())


def _compacto(s: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', _norm(s))


def coincide_busqueda(f: Fila, texto: str) -> bool:
    t = _norm(texto)
    if not t:
        return True
    c = f.c
    doc = ' '.join(x for x in (f.cli_doc, c.doc_cliente if c else '') if x)
    nombre = ' '.join(x for x in (f.cli_nombre, c.nombre_cliente if c else '') if x)
    # número suelto: 1099 o 000001099 → número del comprobante; también parte de un RUC/DNI
    if t.isdigit():
        return int(t) == f.numero or t in doc
    # serie-número: "E001-1099", "E001 1099", "E0011099"
    m = re.fullmatch(r'([A-Z0-9]{4})[\s\-]*0*(\d+)', t)
    if m and m.group(1) == f.serie and int(m.group(2)) == f.numero:
        return True
    if _compacto(t) and _compacto(t) == f.serie:
        return True
    # razón social: todas las palabras buscadas deben aparecer (en cualquier orden)
    nom = _norm(nombre)
    if all(p in nom for p in t.split()):
        return True
    return _compacto(t) in _compacto(f.id) or t in _norm(doc)


def filtrar(filas: list[Fila], texto: str = '', condicion: str = '', moneda: str = '', tipos: list[str] | None = None) -> list[Fila]:
    """condicion: '' | 'CON' | 'CRE' · moneda: '' | 'S' | 'D' · tipos: ['01', '03', '07', '08'] o None (todos)."""
    res = []
    for f in filas:
        if condicion and (f.c is None or f.condicion != condicion):
            continue
        if moneda and f.moneda != moneda:
            continue
        if tipos and f.tipo not in tipos:
            continue
        if not coincide_busqueda(f, texto):
            continue
        res.append(f)
    return res
