# -*- coding: utf-8 -*-
"""
motor/excel_entrada.py — Lee el Excel de ventas del mes (propuesta SUNAT / registro de ventas en el formato
de 44 columnas A–AR de NewContaSis) y lo convierte en comprobantes, para cuando el descargador no trae
todos los XML (por ejemplo, solo facturas y ninguna boleta).

El Excel trae todos los comprobantes pero no el detalle de los ítems. Por eso el XML manda cuando existe y
el Excel solo completa los que faltan (ver lector.combinar).

Columnas usadas: A fecha · B venc. · C tipo · D serie · E número · F tipo doc. · G número doc. · H cliente ·
I exportación · J base · K exonerada · L inafecta · M ISC · N IGV · O otros · P total · Q TC ·
R–U documento modificado · V moneda · X venc. · Y condición · AN medio de pago · AQ ICBPER.
Las filas sin fecha en A o sin tipo/serie/número (títulos, cabeceras, totales) se ignoran.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .xml_parser import Comprobante

TIPOS = {'01': ('factura', 'FACTURA'), '03': ('boleta', 'BOLETA DE VENTA'), '07': ('nota_credito', 'NOTA DE CREDITO'),
         '08': ('nota_debito', 'NOTA DE DEBITO')}
D2 = Decimal('0.01')


def _dec(v, decimales: int = 2) -> Decimal:
    if v in (None, '', '-'):
        return Decimal(0)
    try:
        if isinstance(v, float):
            v = round(v, decimales)
        return Decimal(str(v).replace(',', '').strip()).quantize(Decimal(1).scaleb(-decimales))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _fecha(v) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) and 30000 < v < 80000:              # número de serie de Excel
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    s = str(v or '').strip()
    m = re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', s)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        except ValueError:
            return ''
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2}).*', s)
    return f'{m[1]}-{m[2]}-{m[3]}' if m else ''


def _txt(v) -> str:
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return ' '.join(str(v).split())


def _tasa(base: Decimal, igv: Decimal) -> Decimal:
    if base <= 0 or igv <= 0:
        return Decimal(0)
    r = igv / base * 100
    for t in (Decimal(18), Decimal(10)):
        if abs(r - t) <= Decimal('0.6'):
            return t
    return r.quantize(D2)


def es_excel(nombre: str, data: bytes) -> bool:
    return nombre.lower().endswith(('.xlsx', '.xlsm')) or (data[:4] == b'PK\x03\x04' and b'xl/' in data[:2000])


def leer_excel_ventas(data: bytes, nombre: str = 'ventas.xlsx') -> tuple[list[Comprobante], list[tuple[str, str, str, str]], list[str]]:
    """Devuelve (comprobantes, anulados, avisos). anulados = [(tipo, serie, numero, fecha)] para las filas con
    todos los importes en cero (así reporta SUNAT los comprobantes dados de baja)."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    comps: list[Comprobante] = []
    anulados: list[tuple[str, str, str, str]] = []
    avisos: list[str] = []
    for ws in wb.worksheets:
        for i, r in enumerate(ws.iter_rows(values_only=True), start=1):
            r = list(r) + [None] * (44 - len(r))
            fecha = _fecha(r[0])
            tipo = _txt(r[2]).zfill(2) if _txt(r[2]).isdigit() else ''
            serie = _txt(r[3]).upper()
            numero = _txt(r[4])
            if not (fecha and tipo and re.fullmatch(r'[A-Z0-9]{1,4}', serie) and numero.isdigit()):
                continue
            if tipo not in TIPOS:
                avisos.append(f'{nombre} fila {i}: tipo {tipo} {serie}-{numero} no va al registro de ventas')
                continue
            num = int(numero)
            imp = {k: abs(_dec(r[j])) for k, j in (('I', 8), ('J', 9), ('K', 10), ('L', 11), ('M', 12), ('N', 13),
                                                   ('O', 14), ('P', 15), ('AQ', 42))}
            if not any(imp.values()):
                anulados.append((tipo, serie, num, fecha))
                continue
            c = Comprobante(archivo=f'{nombre} (fila {i})', tipo_codigo=tipo, serie_numero=f'{serie}-{num}',
                            fecha_emision=fecha, periodo=fecha[:7], origen='excel')
            c.tipo_documento, c.tipo_nombre = TIPOS[tipo]
            c.fecha_vencimiento = _fecha(r[23]) or _fecha(r[1])
            c.tipo_doc_cliente_cod = _txt(r[5])
            c.doc_cliente = _txt(r[6])
            nom = _txt(r[7])
            c.nombre_cliente = '' if nom in ('-', '--', '0') else nom
            mon = _txt(r[21]).upper()
            q = _dec(r[16], 4)
            c.tc_excel = q                                   # TC con que el Excel expresó los importes
            dolares = mon in ('D', 'USD', '2', 'US$', 'DOLARES', 'DÓLARES') or (not mon and q > Decimal('1.5'))
            if dolares:
                c.moneda_codigo, c.moneda, c.simbolo = 'USD', 'dólares', 'US$'
                if q > Decimal('1.5'):
                    # la propuesta SUNAT trae los importes en soles (US$ × TC): se vuelven a dólares
                    imp = {k: (v / q).quantize(D2) for k, v in imp.items()}
                    dif = imp['P'] - sum(imp[k] for k in ('I', 'J', 'K', 'L', 'M', 'N', 'O', 'AQ'))
                    if dif and abs(dif) <= Decimal('0.05'):
                        imp['N' if imp['N'] else 'J'] += dif
                    c.avisos_lectura.append(f'En dólares según el Excel (TC {q}): importes llevados a US$ '
                                            f'(total US$ {imp["P"]:,.2f}). Verifique con el comprobante')
                else:
                    c.avisos_lectura.append('En dólares según el Excel: verifique los importes con el comprobante')
            c.exportacion, c.gravadas, c.exoneradas, c.inafectas = imp['I'], imp['J'], imp['K'], imp['L']
            c.isc, c.igv, c.otros_tributos, c.importe_total, c.icbper = imp['M'], imp['N'], imp['O'], imp['P'], imp['AQ']
            c.valor_venta = c.exportacion + c.gravadas + c.exoneradas + c.inafectas
            c.tasa_igv = _tasa(c.gravadas, c.igv)
            c.tiene_igv = c.igv > 0
            cond = _txt(r[24]).upper()
            c.forma_pago = 'Credito' if cond.startswith('CRE') else ('Contado' if cond.startswith('CON') else '')
            c.medio_pago = _txt(r[39])
            if tipo in ('07', '08'):
                rs, rn = _txt(r[19]).upper(), _txt(r[20])
                if rs and rn.isdigit():
                    c.doc_referencia = f'{rs}-{int(rn)}'
                c.doc_referencia_tipo = _txt(r[18]).zfill(2) if _txt(r[18]) else ''
                c.doc_referencia_fecha = _fecha(r[17])
            comps.append(c)
    wb.close()
    if not comps and not anulados:
        avisos.append(f'{nombre}: no se encontraron comprobantes (se espera el Excel de ventas de 44 columnas A–AR, '
                      'con la fecha en A, tipo en C, serie en D y número en E)')
    return comps, anulados, avisos
