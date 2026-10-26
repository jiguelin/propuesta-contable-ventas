# -*- coding: utf-8 -*-
"""
motor/lector.py — Abre lo que sube el usuario (XML sueltos o ZIP, también ZIP dentro de ZIP) y separa:

  * comprobantes UBL (Invoice / CreditNote / DebitNote)            → xml_parser.Comprobante
  * bajas: VoidedDocuments (Comunicación de Baja, RA) y
           SummaryDocuments con ConditionCode 3 (boletas anuladas, RC) → lista de anulados
  * todo lo demás (CDR ApplicationResponse, PDF, retenciones…)       → "no procesados", con el motivo
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

from lxml import etree

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


def _expandir(nombre: str, data: bytes, prof: int = 0):
    """Genera (nombre, bytes) de cada archivo; abre ZIP recursivamente (máx. 3 niveles)."""
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


def leer_archivos(archivos: list[tuple[str, bytes]]) -> Lectura:
    out = Lectura()
    for nombre0, data0 in archivos:
        for nombre, data in _expandir(nombre0, data0):
            if not data:
                out.no_procesados.append((nombre, 'ZIP demasiado anidado o vacío'))
                continue
            low = nombre.lower()
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
    return out
