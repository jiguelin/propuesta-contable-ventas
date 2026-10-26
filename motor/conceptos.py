# -*- coding: utf-8 -*-
"""
motor/conceptos.py — Catálogo de conceptos contables de ventas y su traducción al plan de cada empresa.

El motor nunca escribe un código fijo: primero decide un CONCEPTO (ej. SERVICIO_LOCAL) y después
lo traduce a la cuenta del plan de esa empresa con el "mapa de conceptos", que el usuario confirma
una sola vez. Así se respetan los dígitos y divisionarias de cada contador (70121, 7032101, …).

Ojo PCGE 2026: 70111 = mercaderías *exportación* (en el PCGE 2019 era venta local). Por eso las
sugerencias se validan contra el plan real, nunca se asumen.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .plan_cuentas import PlanCuentas


@dataclass(frozen=True)
class Concepto:
    clave: str
    nombre: str
    sugeridas: tuple[str, ...]           # códigos PCGE 2026 en orden de preferencia
    obligatorio: bool = True             # sin él no se puede generar
    familia: str = ''                    # prefijo de búsqueda alternativa


CONCEPTOS: dict[str, Concepto] = {c.clave: c for c in [
    Concepto('VENTA_MERCADERIA_LOCAL', 'Venta local de mercaderías (terceros)', ('70121', '7012'), True, '701'),
    Concepto('VENTA_MERCADERIA_LOCAL_REL', 'Venta local de mercaderías (vinculadas)', ('70122',), False, '701'),
    Concepto('VENTA_MERCADERIA_EXPORT', 'Exportación de mercaderías (terceros)', ('70111', '7011'), False, '701'),
    Concepto('VENTA_MERCADERIA_EXPORT_REL', 'Exportación de mercaderías (vinculadas)', ('70112',), False, '701'),
    Concepto('VENTA_PT_LOCAL', 'Venta local de productos terminados (terceros)', ('70221', '7022'), False, '702'),
    Concepto('VENTA_PT_LOCAL_REL', 'Venta local de productos terminados (vinculadas)', ('70222',), False, '702'),
    Concepto('VENTA_PT_EXPORT', 'Exportación de productos terminados (terceros)', ('70211', '7021'), False, '702'),
    Concepto('VENTA_PT_EXPORT_REL', 'Exportación de productos terminados (vinculadas)', ('70212',), False, '702'),
    Concepto('SERVICIO_LOCAL', 'Servicios prestados, local (terceros)', ('70321', '7032'), True, '703'),
    Concepto('SERVICIO_LOCAL_REL', 'Servicios prestados, local (vinculadas)', ('70322',), False, '703'),
    Concepto('SERVICIO_EXPORT', 'Exportación de servicios (terceros)', ('70311', '7031'), False, '703'),
    Concepto('SERVICIO_EXPORT_REL', 'Exportación de servicios (vinculadas)', ('70312',), False, '703'),
    Concepto('ALQUILER', 'Alquileres (otros ingresos)', ('754',), False, '754'),
    Concepto('VENTA_ACTIVO_FIJO', 'Venta de activo inmovilizado', ('75641', '7564', '756'), False, '756'),
    Concepto('OTROS_INGRESOS', 'Otros ingresos de gestión', ('7599', '759'), False, '759'),
    Concepto('ANTICIPO_RECIBIDO', 'Anticipos recibidos de clientes', ('1221', '122'), True, '122'),
    Concepto('CXC_PEN', 'Cuentas por cobrar en soles (crédito)', ('1212',), True, '121'),
    Concepto('CXC_USD', 'Cuentas por cobrar en dólares (crédito)', ('1212',), True, '121'),
    Concepto('CAJA_PEN', 'Caja en soles (contado)', ('1011', '101'), True, '101'),
    Concepto('CAJA_USD', 'Caja en dólares (contado)', ('1012', '101'), True, '101'),
    Concepto('ICBPER', 'Impuesto a las bolsas de plástico', ('40189',), False, '4018'),
    Concepto('OTROS_TRIBUTOS', 'Otros tributos y cargos', (), False, ''),
]}

CONCEPTOS_VENTA = [k for k in CONCEPTOS if k.startswith(('VENTA_', 'SERVICIO_', 'ALQUILER', 'OTROS_INGRESOS'))]


# ----------------------------------------------------------------------------- texto
_STOP = {
    'DE', 'DEL', 'LA', 'LAS', 'EL', 'LOS', 'Y', 'E', 'EN', 'POR', 'PARA', 'CON', 'A', 'AL', 'O', 'U',
    'TERCEROS', 'TERCERO', 'RELACIONADAS', 'RELACIONADA', 'LOCAL', 'LOCALES', 'VENTA', 'VENTAS',
    'SERVICIO', 'SERVICIOS', 'TERMINADOS', 'TERMINADAS', 'MERCADERIA', 'MERCADERIAS', 'EXPORTACION',
    'PRESTADOS', 'OTROS', 'OTRAS', 'MES', 'CUENTA', 'INGRESOS', 'GESTION',
}


def sin_tildes(s: str) -> str:
    return ''.join(ch for ch in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(ch))


def tokens(s: str) -> list[str]:
    """Palabras en mayúsculas, sin tildes ni signos."""
    return re.findall(r'[A-Z0-9]+', sin_tildes(s).upper())


def palabras_clave(descripcion: str) -> list[str]:
    """Palabras significativas de la descripción de una cuenta (para elegir subcuentas)."""
    vistas, res = set(), []
    for t in tokens(descripcion):
        if len(t) >= 3 and t not in _STOP and not t.isdigit() and t not in vistas:
            vistas.add(t)
            res.append(t)
    return res


def _dist1(a: str, b: str) -> bool:
    """True si a y b difieren en a lo más 1 letra (sustitución, inserción, borrado o trasposición)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        dif = [i for i in range(la) if a[i] != b[i]]
        if len(dif) == 1:
            return True
        return len(dif) == 2 and dif[1] == dif[0] + 1 and a[dif[0]] == b[dif[1]] and a[dif[1]] == b[dif[0]]
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def coincide(palabra: str, texto_tokens: list[str]) -> bool:
    """Coincidencia tolerante: igual, mismo inicio (CAPACITACION ~ CAPACITACIONES) o 1 letra de diferencia
    en palabras de 5+ letras (SERIVICIO ~ SERVICIO, LAORAL ~ LABORAL)."""
    for t in texto_tokens:
        if t == palabra:
            return True
        if len(palabra) >= 6 and len(t) >= 6 and (t.startswith(palabra[:-2]) or palabra.startswith(t[:-2])) and abs(len(t) - len(palabra)) <= 3:
            return True
        if len(palabra) >= 5 and len(t) >= 5 and _dist1(palabra, t):
            return True
    return False


# ----------------------------------------------------------------------------- mapa
@dataclass
class Destino:
    """Resultado de traducir un concepto: una cuenta hoja o un grupo de subcuentas."""
    concepto: str
    cuenta: str = ''                 # código en el plan (hoja o grupo)
    es_grupo: bool = False
    hojas: list[str] = field(default_factory=list)
    nota: str = ''

    @property
    def ok(self) -> bool:
        return bool(self.cuenta)


def resolver_codigo(plan: PlanCuentas, codigo: str) -> tuple[str, bool, str]:
    """Busca `codigo` en el plan; si no existe sube de nivel (75641 → 7564). Devuelve (código, es_grupo, nota)."""
    c = codigo
    while len(c) >= 3:
        cu = plan.get(c)
        if cu:
            nota = '' if c == codigo else f'{codigo} no existe; se usa {c}'
            return c, not cu.es_hoja, nota
        c = c[:-1]
    return '', False, f'{codigo} no existe en el plan'


def sugerir_mapa(plan: PlanCuentas) -> dict[str, Destino]:
    """Propone la cuenta de cada concepto según el plan de la empresa (el usuario confirma)."""
    mapa: dict[str, Destino] = {}
    for clave, con in CONCEPTOS.items():
        d = Destino(clave)
        for sug in con.sugeridas:
            cod, grupo, nota = resolver_codigo(plan, sug)
            if cod:
                d.cuenta, d.es_grupo, d.nota = cod, grupo, nota
                break
        if d.cuenta and d.es_grupo:
            d.hojas = [h.codigo for h in plan.hojas_bajo(d.cuenta)]
            if len(d.hojas) == 1:          # 122 con una sola subcuenta 1221 → se usa directo
                d.cuenta, d.es_grupo, d.hojas = d.hojas[0], False, []
        if clave == 'CXC_USD' and d.cuenta:
            # si existe una subcuenta en moneda extranjera de la 1212, preferirla
            me = [h for h in plan.hojas_bajo('121') if re.search(r'\bM\.?E\.?\b|EXTRANJERA|DOLAR', h.descripcion.upper())]
            if me:
                d.cuenta, d.es_grupo, d.nota = me[0].codigo, False, 'subcuenta en moneda extranjera'
        mapa[clave] = d
    return mapa


def elegir_subcuenta(plan: PlanCuentas, hojas: list[str], textos: list[tuple[str, float]],
                     palabras_extra: dict[str, list[str]] | None = None) -> tuple[str, int, str]:
    """Elige la subcuenta de un grupo comparando las descripciones de los ítems con las palabras clave
    de cada subcuenta. `textos` = [(descripción del ítem, importe)]. Devuelve (cuenta, confianza, motivo)."""
    palabras_extra = palabras_extra or {}
    puntaje: dict[str, float] = {}
    motivo: dict[str, str] = {}
    for desc, importe in textos:
        tt = tokens(desc)
        mejor, mejor_n, mejor_p = None, 0, ''
        for h in hojas:
            cu = plan.get(h)
            claves = palabras_extra.get(h) or palabras_clave(cu.descripcion if cu else '')
            hits = [p for p in claves if coincide(p, tt)]
            if len(hits) > mejor_n:
                mejor, mejor_n, mejor_p = h, len(hits), ', '.join(hits[:3])
        if mejor:
            puntaje[mejor] = puntaje.get(mejor, 0) + float(importe or 0) + 0.01
            motivo.setdefault(mejor, mejor_p)
    if puntaje:
        cuenta = max(puntaje, key=puntaje.get)
        conf = 95 if len(puntaje) == 1 else 85
        return cuenta, conf, f'palabras de la subcuenta: {motivo[cuenta]}'
    return hojas[0], 70, 'ningún ítem coincide con las subcuentas; se usa la primera (revise)'
