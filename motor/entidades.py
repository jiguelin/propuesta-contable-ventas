# -*- coding: utf-8 -*-
"""
motor/entidades.py — Excel de entidades de NewContaSis y códigos de clientes.

Formato observado ("CLIENTES - PROVEEDORES (TODOS)"):
  filas 2-4: razón social, giro, "R.U.C.: 20609565625"
  fila 8   : R.U.C. | NOMBRE O RAZON SOCIAL | TELEFONO | DIRECCION
  última   : "Generado automáticamente por SISTEMA EXPERTO CONTABLE …" (se ignora)

Usos:
  * código de anulado (entidad llamada ANULADO: "0000" en CONTADEUS) y de boleta sin documento
    (entidad BOLETA / BOLETA DE VENTA: "03");
  * clientes del exterior: si ya existen se usa su código (#0000000009, LOGICCRAFTE, …);
    si son nuevos reciben el siguiente de la serie 10000000001, 10000000002, …
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import openpyxl

from .conceptos import sin_tildes

SERIE_EXTERIOR = re.compile(r'^10000\d{6}$')       # 10000000001 … 10000999999
PRIMER_EXTERIOR = 10000000001

_SUFIJOS = {'LLC', 'INC', 'LTD', 'LIMITED', 'CORP', 'CORPORATION', 'CO', 'COMPANY', 'PTE', 'GMBH', 'SL', 'SA',
            'SAC', 'SRL', 'EIRL', 'SAA', 'SPA', 'BV', 'AG', 'PLC', 'LLP', 'THE', 'CIA', 'DE', 'CV', 'LTDA'}


def normalizar_nombre(nombre: str) -> str:
    """'Ian Marko, LLC.' → 'IAN MARKO'. Sin tildes, signos ni sufijos societarios."""
    s = sin_tildes(nombre or '').upper()
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s)
    palabras = [p for p in s.split() if p not in _SUFIJOS]
    return ' '.join(palabras)


@dataclass
class Entidad:
    codigo: str
    nombre: str
    direccion: str = ''

    @property
    def nombre_norm(self) -> str:
        return normalizar_nombre(self.nombre)


@dataclass
class Entidades:
    items: list[Entidad] = field(default_factory=list)
    empresa: str = ''
    ruc: str = ''

    def __post_init__(self):
        self._por_codigo = {e.codigo: e for e in self.items}
        self._por_nombre: dict[str, Entidad] = {}
        for e in self.items:
            self._por_nombre.setdefault(e.nombre_norm, e)

    def __len__(self):
        return len(self.items)

    def get(self, codigo: str) -> Entidad | None:
        return self._por_codigo.get((codigo or '').strip())

    def por_nombre(self, nombre: str) -> Entidad | None:
        n = normalizar_nombre(nombre)
        return self._por_nombre.get(n) if n else None

    def codigo_generico(self, *nombres: str) -> tuple[str, str] | None:
        """Primera entidad cuyo nombre normalizado es alguno de `nombres` → (código, nombre)."""
        buscados = {normalizar_nombre(n) for n in nombres}
        for e in self.items:
            if e.nombre_norm in buscados:
                return e.codigo, e.nombre
        return None

    def codigo_anulado(self) -> tuple[str, str] | None:
        return self.codigo_generico('ANULADO', 'ANULADOS', 'ANULADA')

    def codigo_boleta(self) -> tuple[str, str] | None:
        return self.codigo_generico('BOLETA DE VENTA', 'BOLETA', 'BOLETAS', 'BOLETAS DE VENTA')

    def codigos(self) -> set[str]:
        return set(self._por_codigo)


def _limpio(v) -> str:
    return ' '.join(str(v).split()) if v is not None else ''


def leer_entidades_excel(origen) -> Entidades:
    if isinstance(origen, (bytes, bytearray)):
        origen = io.BytesIO(origen)
    wb = openpyxl.load_workbook(origen, read_only=True, data_only=True)
    ws = wb.active
    filas = list(ws.iter_rows(values_only=True))
    empresa, ruc = '', ''
    for r in filas[:8]:
        for v in r:
            s = _limpio(v)
            m = re.search(r'R\.?U\.?C\.?\s*:?\s*(\d{11})', s)
            if m:
                ruc = m.group(1)
            elif s and not empresa and len(s) > 3:
                empresa = s
    idx, c_cod, c_nom, c_dir = None, None, None, None
    for i, r in enumerate(filas[:40]):
        vals = [_limpio(v).upper() for v in r]
        if any(v.startswith('NOMBRE') or 'RAZON SOCIAL' in v for v in vals) and any(v.replace('.', '') in ('RUC', 'CODIGO') for v in vals):
            idx = i
            c_cod = next(j for j, v in enumerate(vals) if v.replace('.', '') in ('RUC', 'CODIGO'))
            c_nom = next(j for j, v in enumerate(vals) if v.startswith('NOMBRE') or 'RAZON SOCIAL' in v)
            c_dir = next((j for j, v in enumerate(vals) if v.startswith('DIRECCION')), None)
            break
    if idx is None:
        raise ValueError('No se encontró la cabecera "R.U.C. | NOMBRE O RAZON SOCIAL" del Excel de entidades.')
    items = []
    for r in filas[idx + 1:]:
        cod = _limpio(r[c_cod]) if c_cod < len(r) else ''
        if not cod or cod.upper().startswith('GENERADO'):
            continue
        nom = _limpio(r[c_nom]) if c_nom < len(r) else ''
        dire = _limpio(r[c_dir]) if c_dir is not None and c_dir < len(r) else ''
        items.append(Entidad(cod, nom, dire))
    return Entidades(items, empresa, ruc)


def siguiente_codigo_exterior(usados: set[str]) -> str:
    """Siguiente código libre de la serie 10000000001, 10000000002, … (mayor usado + 1)."""
    nums = [int(c) for c in usados if SERIE_EXTERIOR.match(c or '')]
    n = max(nums) + 1 if nums else PRIMER_EXTERIOR
    while str(n) in usados:
        n += 1
    return str(n)


class RegistroExterior:
    """Clientes del exterior de una empresa: nombre/ID extranjero → código. Nunca reutiliza un código."""

    def __init__(self, registros: list[dict] | None = None, entidades: Entidades | None = None):
        self.por_nombre: dict[str, str] = {}
        self.por_id: dict[str, str] = {}
        self.nombres: dict[str, str] = {}
        self.entidades = entidades or Entidades()
        self.nuevos: list[dict] = []
        for r in registros or []:
            self._indexar(r['codigo'], r.get('nombre', ''), r.get('id_extranjero', ''))

    def _indexar(self, codigo: str, nombre: str, id_ext: str):
        n = normalizar_nombre(nombre)
        if n:
            self.por_nombre.setdefault(n, codigo)
        if id_ext:
            self.por_id.setdefault(id_ext.strip().upper(), codigo)
        self.nombres.setdefault(codigo, nombre)

    def usados(self) -> set[str]:
        return self.entidades.codigos() | set(self.nombres)

    def codigo_para(self, nombre: str, id_ext: str = '', pais: str = '') -> tuple[str, str]:
        """Devuelve (código, origen) con origen ∈ {'entidades', 'registro', 'nuevo'}."""
        key_id = (id_ext or '').strip().upper()
        if key_id and key_id not in ('-', '0') and key_id in self.por_id:
            return self.por_id[key_id], 'registro'
        n = normalizar_nombre(nombre)
        if n and n in self.por_nombre:
            return self.por_nombre[n], 'registro'
        e = self.entidades.por_nombre(nombre)
        if e:
            self._indexar(e.codigo, nombre, key_id)
            return e.codigo, 'entidades'
        cod = siguiente_codigo_exterior(self.usados())
        self._indexar(cod, nombre, key_id)
        self.nuevos.append({'codigo': cod, 'nombre': nombre, 'id_extranjero': key_id, 'pais': pais})
        return cod, 'nuevo'
