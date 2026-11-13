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

# Conceptos que van a UNA cuenta fija: no se elige subcuenta por los ítems. Deben ser cuenta que acepte
# movimiento en NewContaSis (columna ANALISIS del plan). La 1011 de ESSENTTA tiene subcuentas pero acepta
# movimiento, así que se usa la 1011. Solo si es de título (ANALISIS vacío) se elige una subcuenta.
CONCEPTOS_CUENTA_FIJA = ('ANTICIPO_RECIBIDO', 'CXC_PEN', 'CXC_USD', 'CAJA_PEN', 'CAJA_USD', 'ICBPER')
_PREFERIDAS = {                                   # palabras para proponer la subcuenta (el usuario confirma)
    'CAJA_PEN': ('EFECTIVO', 'GENERAL', 'PRINCIPAL', 'MN', 'SOLES', 'NACIONAL'),
    'CAJA_USD': ('ME', 'DOLARES', 'EXTRANJERA', 'EFECTIVO'),
    'CXC_PEN': ('EMITIDAS', 'CARTERA', 'MN', 'SOLES'),
    'CXC_USD': ('ME', 'DOLARES', 'EXTRANJERA', 'EMITIDAS'),
    'ANTICIPO_RECIBIDO': ('ANTICIPO', 'TERCEROS'),
}


def proponer_hoja(plan: PlanCuentas, concepto: str, hojas: list[str]) -> str:
    """Entre las subcuentas de una cuenta fija, la que mejor coincide con las palabras preferidas."""
    mejor, puntos = hojas[0] if hojas else '', -1
    for h in hojas:
        tt = set(re.findall(r'[A-Z]+', sin_tildes(plan.get(h).descripcion.upper()))) if plan.get(h) else set()
        p = sum(len(_PREFERIDAS.get(concepto, ())) - i for i, w in enumerate(_PREFERIDAS.get(concepto, ())) if w in tt)
        if p > puntos:
            mejor, puntos = h, p
    return mejor


def opciones_fijas(plan: PlanCuentas, concepto: str, cuenta: str = '') -> list:
    """Subcuentas entre las que hay que elegir para una cuenta fija, solo cuando la cuenta estándar del concepto
    (ej. 1011) o la elegida son de título en este plan: 1011 → [1011001, 1011002, 1011003]. Si la 1212 o la 1012
    ya son de movimiento, no hay nada que elegir → []."""
    for cod in (cuenta, *(CONCEPTOS[concepto].sugeridas[:1] if concepto in CONCEPTOS else ())):
        cu = plan.get(cod) if cod else None
        if cu is not None and not cu.acepta_movimiento:
            hojas = [h for h in plan.imputables() if h.codigo.startswith(cod) and h.codigo != cod]
            if len(hojas) > 1:
                return hojas
    if cuenta and plan.get(cuenta) is not None:
        for sug in (CONCEPTOS[concepto].sugeridas[:1] if concepto in CONCEPTOS else ()):
            padre = plan.get(sug)
            if cuenta.startswith(sug) and cuenta != sug and padre is not None and not padre.acepta_movimiento:
                return [h for h in plan.imputables() if h.codigo.startswith(sug) and h.codigo != sug]
    return []


def validar_mapa(plan: PlanCuentas, cuentas: dict[str, str]) -> list[str]:
    """Errores que impiden confirmar el mapa: cuentas que no existen, obligatorios vacíos y cuentas fijas de título."""
    errores = []
    for k, cod in cuentas.items():
        cod = (cod or '').strip()
        if not cod:
            if CONCEPTOS[k].obligatorio:
                errores.append(f'{k}: falta la cuenta (obligatorio)')
            continue
        cu = plan.get(cod)
        if cu is None:
            errores.append(f'{k}: la cuenta {cod} no existe en el plan')
        elif k in CONCEPTOS_CUENTA_FIJA and not cu.acepta_movimiento:
            subs = '; '.join(f'{h.codigo} {h.descripcion[:40]}' for h in opciones_fijas(plan, k, cod)[:8])
            errores.append(f'{k}: {cod} es una cuenta de título (no acepta movimiento en NewContaSis). '
                           f'Elija una subcuenta: {subs}')
    return errores


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
            elif clave in CONCEPTOS_CUENTA_FIJA and plan.get(d.cuenta).acepta_movimiento:
                d.es_grupo, d.hojas = False, []          # ej. 1011 con subcuentas pero de movimiento: se usa la 1011
            elif clave in CONCEPTOS_CUENTA_FIJA:
                grupo = d.cuenta
                ops = [h.codigo for h in opciones_fijas(plan, clave, grupo)] or d.hojas
                d.cuenta, d.es_grupo, d.hojas = proponer_hoja(plan, clave, ops), False, []
                d.nota = f'{grupo} es cuenta de título: se propone {d.cuenta}. Revise cuál corresponde'
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
