# -*- coding: utf-8 -*-
"""
motor/correlativos.py — Huecos en la numeración de cada serie.

Por cada (tipo, serie) se ordenan los números del mes y se listan los que faltan. Cada hueco se propone
como ANULADO, salvo que el número aparezca como anticipo en otro XML (PrepaidPayment): entonces es un
comprobante emitido cuyo XML no se subió ("XML faltante"), nunca un anulado.

Ejemplo real (ESSENTTA, setiembre 2026, serie BOP1): faltan 4480, 4482-4484, 4488-4490, 4494 y 4497;
4494 es anticipo de BOP1-4495 → faltante; los otros 8 → anulado propuesto.
"""
from __future__ import annotations

from dataclasses import dataclass


def id_normal(serie_numero: str) -> str:
    """'BOP1-00004494' → 'BOP1-4494' (para comparar referencias)."""
    if '-' not in (serie_numero or ''):
        return (serie_numero or '').strip().upper()
    s, n = serie_numero.split('-', 1)
    n = n.strip()
    return f'{s.strip().upper()}-{int(n)}' if n.isdigit() else f'{s.strip().upper()}-{n}'


@dataclass
class Hueco:
    tipo: str
    serie: str
    numero: int
    fecha: str            # fecha propuesta: la del comprobante anterior de la serie
    clase: str            # 'anulado' | 'faltante'
    motivo: str

    @property
    def id(self) -> str:
        return f'{self.serie}-{self.numero}'


def buscar_huecos(docs: list[tuple[str, str, int, str]], referenciados: dict[str, str],
                  ultimo_previo: dict[tuple[str, str], int] | None = None) -> list[Hueco]:
    """docs = [(tipo, serie, número, fecha ISO)] del mes (sin duplicados ni excluidos).
    referenciados = {'BOP1-4494': 'BOP1-4495'} anticipo → comprobante que lo descuenta.
    ultimo_previo = {(tipo, serie): último número del mes anterior} para revisar el inicio de la serie."""
    ultimo_previo = ultimo_previo or {}
    por_serie: dict[tuple[str, str], dict[int, str]] = {}
    for tipo, serie, num, fecha in docs:
        por_serie.setdefault((tipo, serie), {})[num] = fecha
    huecos: list[Hueco] = []
    for (tipo, serie), nums in sorted(por_serie.items()):
        orden = sorted(nums)
        desde = orden[0]
        previo = ultimo_previo.get((tipo, serie))
        if previo is not None and previo < desde:
            desde = previo + 1
        fecha_ant = nums[orden[0]]
        for n in range(desde, orden[-1] + 1):
            if n in nums:
                fecha_ant = nums[n]
                continue
            sid = f'{serie}-{n}'
            if sid in referenciados:
                huecos.append(Hueco(tipo, serie, n, fecha_ant, 'faltante',
                                    f'Es anticipo descontado en {referenciados[sid]}: falta su XML (no es anulado)'))
            else:
                huecos.append(Hueco(tipo, serie, n, fecha_ant, 'anulado',
                                    'Número faltante en la serie: se propone como anulado'))
    return huecos
