# -*- coding: utf-8 -*-
"""
motor/lector.py — Abre lo que sube el usuario (XML sueltos o ZIP, también ZIP dentro de ZIP) y separa:

  * comprobantes UBL (Invoice / CreditNote / DebitNote)            → xml_parser.Comprobante
  * bajas: VoidedDocuments (Comunicación de Baja, RA) y
           SummaryDocuments con ConditionCode 3 (boletas anuladas, RC) → lista de anulados
  * Excel de ventas del mes (44 columnas A–AR)                      → completa los comprobantes sin XML
  * todo lo demás (CDR ApplicationResponse, PDF, retenciones…)       → "no procesados", con el motivo

XML y Excel se pueden subir juntos o por separado: el XML manda (tiene el detalle para la glosa y la
forma de pago); del Excel solo entran los comprobantes que no llegaron en XML. Nunca se duplican.
"""
from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from dataclasses import dataclass, field

from lxml import etree

from .excel_entrada import es_excel, leer_excel_ventas
from .xml_parser import NS, Comprobante, _PARSER, corregir_codificacion, parse_xml

NS_SAC = NS['sac']


@dataclass
class Baja:
    tipo: str          # 01, 03, 07, 08
    serie: str
    numero: int
    fecha: str         # fecha de emisión de los comprobantes dados de baja (ReferenceDate)
    fuente: str        # 'RA' o 'RC' + nombre de archivo

    @property
    def id(self) -> str:
        return f'{self.serie}-{self.numero}'


@dataclass
class Lectura:
    comprobantes: list[Comprobante] = field(default_factory=list)
    bajas: list[Baja] = field(default_factory=list)
    no_procesados: list[tuple[str, str]] = field(default_factory=list)   # (archivo, motivo)
    resumen: list[str] = field(default_factory=list)                     # qué aportó cada fuente


def _expandir(nombre: str, data: bytes, prof: int = 0):
    """Genera (nombre, bytes) de cada archivo; abre ZIP recursivamente (máx. 3 niveles)."""
    if nombre.lower().endswith(('.xlsx', '.xlsm')):
        yield nombre, data
        return
    if nombre.lower().endswith('.zip') or data[:4] == b'PK\x03\x04':
        if prof > 3:
            yield nombre, b''
            return
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.is_dir() or info.filename.startswith('__MACOSX'):
                        continue
                    yield from _expandir(info.filename.split('/')[-1], z.read(info), prof + 1)
        except zipfile.BadZipFile:
            yield nombre, data
        return
    yield nombre, data


def _txt(node, xp):
    r = node.xpath(xp, namespaces=NS)
    if not r:
        return ''
    v = r[0] if isinstance(r[0], str) else (r[0].text or '')
    return ' '.join(v.split())


def _leer_bajas(root, nombre: str) -> list[Baja]:
    raiz = etree.QName(root).localname
    fecha = _txt(root, './cbc:ReferenceDate')
    res: list[Baja] = []
    if raiz == 'VoidedDocuments':
        for ln in root.xpath('./sac:VoidedDocumentsLine', namespaces=NS):
            serie, num = _txt(ln, './sac:DocumentSerialID'), _txt(ln, './sac:DocumentNumberID')
            if serie and num.isdigit():
                res.append(Baja(_txt(ln, './cbc:DocumentTypeCode'), serie.upper(), int(num), fecha, f'RA {nombre}'))
    elif raiz == 'SummaryDocuments':
        for ln in root.xpath('./sac:SummaryDocumentsLine', namespaces=NS):
            if _txt(ln, './cac:Status/cbc:ConditionCode') != '3':
                continue
            sid = _txt(ln, './cbc:ID')
            if '-' in sid:
                serie, num = sid.split('-', 1)
                if num.strip().isdigit():
                    res.append(Baja(_txt(ln, './cbc:DocumentTypeCode'), serie.strip().upper(), int(num), fecha, f'RC {nombre}'))
    return res


def _clave(tipo: str, serie_numero: str) -> tuple[str, str, int] | None:
    if '-' not in (serie_numero or ''):
        return None
    serie, num = serie_numero.rsplit('-', 1)
    num = num.strip().lstrip('0') or '0'
    return (tipo, serie.strip().upper(), int(num)) if num.isdigit() else None


def combinar(out: Lectura, del_excel: list[Comprobante], anulados_excel: list[Baja]):
    """Agrega al lote los comprobantes del Excel que no vinieron en XML; si vinieron en los dos, se usa el XML
    y se avisa si el total no coincide."""
    por_clave = {}
    for c in out.comprobantes:
        k = _clave(c.tipo_codigo, c.serie_numero)
        if k and k not in por_clave:
            por_clave[k] = c
    n_xml = sum(1 for c in out.comprobantes if not c.error)
    agregados = repetidos = 0
    vistos_excel = set()
    for c in del_excel:
        k = _clave(c.tipo_codigo, c.serie_numero)
        if k in vistos_excel:
            continue                                   # mismo comprobante en dos Excel
        vistos_excel.add(k)
        x = por_clave.get(k)
        if x is not None:
            repetidos += 1
            if abs(x.importe_total - c.importe_total) > Decimal('0.05'):
                x.avisos_lectura.append(f'El Excel indica total {c.importe_total:,.2f} y el XML {x.importe_total:,.2f}: se usa el XML')
            continue
        c.avisos_lectura.insert(0, 'Del Excel (sin XML): sin detalle de ítems, glosa general')
        out.comprobantes.append(c)
        agregados += 1
    claves_baja = {(b.tipo, b.serie, b.numero) for b in out.bajas}
    for b in anulados_excel:
        if (b.tipo, b.serie, b.numero) not in claves_baja:
            out.bajas.append(b)
            claves_baja.add((b.tipo, b.serie, b.numero))
    if del_excel or anulados_excel:
        out.resumen.append(f'XML leídos: {n_xml}. Excel: {len(vistos_excel)} comprobantes, {repetidos} ya estaban en XML '
                           f'(se usa el XML) y {agregados} se completan desde el Excel'
                           + (f'; {len(anulados_excel)} con importes en cero (anulados)' if anulados_excel else '') + '.')


def leer_archivos(archivos: list[tuple[str, bytes]]) -> Lectura:
    out = Lectura()
    del_excel: list[Comprobante] = []
    anulados_excel: list[Baja] = []
    for nombre0, data0 in archivos:
        for nombre, data in _expandir(nombre0, data0):
            if not data:
                out.no_procesados.append((nombre, 'ZIP demasiado anidado o vacío'))
                continue
            low = nombre.lower()
            if es_excel(nombre, data):
                try:
                    comps, anul, avisos = leer_excel_ventas(data, nombre)
                except Exception as ex:
                    out.no_procesados.append((nombre, f'Excel ilegible: {ex.__class__.__name__}'))
                    continue
                del_excel.extend(comps)
                anulados_excel.extend(Baja(t, s, n, f, f'Excel {nombre} (importes en cero)') for t, s, n, f in anul)
                out.no_procesados.extend((nombre, a) for a in avisos)
                continue
            if not (low.endswith('.xml') or data.lstrip()[:5] == b'<?xml' or data.lstrip()[:1] == b'<'):
                out.no_procesados.append((nombre, 'no es XML'))
                continue
            try:
                root = etree.fromstring(corregir_codificacion(data), _PARSER)
            except Exception as ex:
                out.no_procesados.append((nombre, f'XML ilegible: {ex}'))
                continue
            if root is None:
                out.no_procesados.append((nombre, 'XML vacío'))
                continue
            raiz = etree.QName(root).localname
            if raiz in ('Invoice', 'CreditNote', 'DebitNote'):
                out.comprobantes.append(parse_xml(data, nombre))
            elif raiz in ('VoidedDocuments', 'SummaryDocuments'):
                bajas = _leer_bajas(root, nombre)
                if bajas:
                    out.bajas.extend(bajas)
                elif raiz == 'SummaryDocuments':
                    out.no_procesados.append((nombre, 'Resumen diario sin anulados (informativo)'))
            elif raiz == 'ApplicationResponse':
                out.no_procesados.append((nombre, 'CDR (constancia de SUNAT), no es comprobante'))
            else:
                out.no_procesados.append((nombre, f'No es comprobante de venta (raíz {raiz})'))
    combinar(out, del_excel, anulados_excel)
    return out
