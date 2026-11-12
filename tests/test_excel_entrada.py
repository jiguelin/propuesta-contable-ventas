# -*- coding: utf-8 -*-
"""Excel de ventas del mes (44 columnas A–AR) junto con los XML, o solo: el XML manda y nada se duplica."""
import io
import zipfile
from datetime import datetime

import openpyxl
from decimal import Decimal

from conftest import ESTUDIO
from fabrica import comprobante


def excel_ventas(filas) -> bytes:
    """filas = [(fecha, tipo, serie, numero, tipo_doc, doc, nombre, base, igv, total, condicion)]
    Igual que la propuesta descargada: tres filas vacías arriba y datos desde la fila 4."""
    wb = openpyxl.Workbook()
    ws = wb.active
    for i, (fe, ti, se, nu, td, doc, nom, base, igv, tot, cond) in enumerate(filas, start=4):
        fila = [None] * 44
        fila[0] = datetime.fromisoformat(fe)
        fila[2], fila[3], fila[4], fila[5], fila[6], fila[7] = ti, se, str(nu), td, doc, nom
        fila[8], fila[9], fila[10], fila[11], fila[12], fila[13], fila[14], fila[15], fila[16] = '0', base, 0, 0, 0, igv, 0, tot, 1
        fila[24], fila[42] = cond, '0'
        for j, v in enumerate(fila, start=1):
            ws.cell(i, j, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


EXCEL = [
    ('2026-09-03', '01', 'F001', 1, '6', '20100000001', 'CLIENTE NACIONAL UNO S.A.C.', 100.0, 18.0, 118.0, None),
    ('2026-09-03', '01', 'F001', 2, '6', '20100000001', 'CLIENTE NACIONAL UNO S.A.C.', 50.0, 9.0, 59.0, None),
    ('2026-09-04', '03', 'B001', 10, '1', '00000001', '-', 20.0, 3.6, 23.6, None),
    ('2026-09-04', '03', 'B001', 11, '1', '45678912', '-', 30.0, 5.4, 35.4, None),
    ('2026-09-05', '03', 'B001', 12, '1', '00000001', '-', 0, 0, 0, None),               # anulada
    ('2026-09-05', '03', 'B001', 13, '1', '00000001', '-', 40.0, 7.2, 47.2, None),
]


def test_xml_y_excel_sin_duplicar(correr):
    xml = comprobante('F001-1', fecha='2026-09-03', items=(('SERVICIO DE ASESORIA CONTABLE SETIEMBRE', 100.0, 'ZZ'),))
    lote, _ = correr(ESTUDIO, 'servicios', [('f1.xml', xml), ('ventas.xlsx', excel_ventas(EXCEL))])
    ids = [f.id for f in lote.filas if f.estado != 'excluido']
    assert len(ids) == len(set(ids)) == 6                       # 5 con importes + 1 anulada, ninguna repetida
    f1 = next(f for f in lote.filas if f.id == 'F001-1')
    assert f1.c.origen == 'xml' and 'ASESORIA' in f1.glosa     # el XML manda: glosa con el detalle
    f2 = next(f for f in lote.filas if f.id == 'F001-2')
    assert f2.c.origen == 'excel' and f2.condicion == 'CON' and f2.cuenta_total == '1011'   # como su factura con XML
    assert any('Del Excel' in a for a in f2.avisos)
    assert lote.resumen and '1 ya estaban en XML' in lote.resumen[0] and '4 se completan' in lote.resumen[0]


def test_solo_excel_boletas(correr):
    lote, _ = correr(ESTUDIO, 'servicios', [('ventas.xlsx', excel_ventas(EXCEL))])
    por = {f.id: f for f in lote.filas}
    b10, b11, b12 = por['B001-10'], por['B001-11'], por['B001-12']
    assert (b10.cli_tipo, b10.cli_doc) == ('0', '03')            # 00000001 = cliente varios → código de boleta
    assert (b11.cli_tipo, b11.cli_doc, b11.cli_nombre) == ('1', '45678912', 'CLIENTE 45678912')
    assert b12.estado == 'anulado'                              # importes en cero en el Excel
    assert b10.condicion == 'CON' and b10.cuenta_base and b10.c.tasa_igv == 18
    assert not any('cuadra' in (f.motivo or '') for f in lote.filas)


def test_excel_distinto_al_xml_avisa(correr):
    xml = comprobante('F001-2', fecha='2026-09-03', items=(('SERVICIO DE ASESORIA', 60.0, 'ZZ'),))
    lote, _ = correr(ESTUDIO, 'servicios', [('ventas.xlsx', excel_ventas(EXCEL[:2])), ('f2.xml', xml)])
    f2 = next(f for f in lote.filas if f.id == 'F001-2')
    assert f2.c.origen == 'xml' and any('El Excel indica total S/ 59.00' in a for a in f2.avisos)


def test_excel_dentro_de_zip(correr):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('ventas.xlsx', excel_ventas(EXCEL[:3]))
    lote, _ = correr(ESTUDIO, 'servicios', [('todo.zip', buf.getvalue())])
    assert {f.id for f in lote.filas} == {'F001-1', 'F001-2', 'B001-10'}


def test_dolares_pasaporte_y_nc_sin_referencia(correr):
    """Casos de la propuesta SUNAT: comprobante en dólares con importes en soles y TC en Q, pasaporte de 15
    dígitos sin nombre, y nota de crédito sin documento modificado (se completa en la tabla)."""
    from motor.edicion import aplicar_ediciones
    filas = [
        ('2026-09-10', '03', 'B001', 20, '4', '009503325', '-', 122.88, 22.12, 145.0, None),
        ('2026-09-11', '03', 'B001', 21, '7', '162406220851646', '-', 61.02, 10.98, 72.0, None),
        ('2026-09-12', '07', 'BC01', 5, '4', '009503325', '-', -122.88, -22.12, -145.0, None),
    ]
    data = excel_ventas(filas)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.active
    for j, v in enumerate([datetime(2026, 9, 15), None, '01', 'F001', '30', '6', '20100000001', 'CLIENTE NACIONAL UNO S.A.C.',
                           '0', 552.64, 0, 0, 0, 99.48, 0, 652.12, 3.454], start=1):
        ws.cell(10, j, v)
    buf = io.BytesIO(); wb.save(buf)
    lote, ctx = correr(ESTUDIO, 'servicios', [('ventas.xlsx', buf.getvalue())])
    por = {f.id: f for f in lote.filas}
    usd = por['F001-30']
    assert usd.moneda == 'D' and usd.c.importe_total == Decimal('188.80') and usd.c.gravadas + usd.c.igv == Decimal('188.80')
    assert (por['B001-21'].cli_tipo, por['B001-21'].cli_doc) == ('7', '162406220851646')   # sin cortar a 11
    nc = por['BC01-5']
    assert nc.estado == 'revisar' and nc.motivo == 'Nota sin documento modificado' and nc.c.importe_total == Decimal('145.00')
    aplicar_ediciones([{'clave': nc.clave, 'Doc. modificado': 'b001-20'}], {f.clave: f for f in lote.filas}, ctx)
    assert (nc.estado, nc.ref_tipo, nc.ref_serie, nc.ref_numero, nc.ref_fecha) == ('ok', '03', 'B001', '20', '2026-09-10')
