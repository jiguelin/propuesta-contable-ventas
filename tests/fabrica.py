# -*- coding: utf-8 -*-
"""Genera XML UBL 2.1 sintéticos (datos ficticios) para los casos que no tienen muestra real:
exportación, notas de crédito, boletas sin DNI y comunicaciones de baja."""
from __future__ import annotations

NS = ('xmlns="urn:oasis:names:specification:ubl:schema:xsd:{raiz}-2" '
      'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
      'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
      'xmlns:sac="urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1"')


def _partes(ruc_emisor, cli_doc, cli_scheme, cli_nombre, pais):
    return f"""<cac:AccountingSupplierParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="6">{ruc_emisor}</cbc:ID></cac:PartyIdentification>
<cac:PartyLegalEntity><cbc:RegistrationName>EMPRESA DE PRUEBA S.A.C.</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party></cac:AccountingSupplierParty>
<cac:AccountingCustomerParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="{cli_scheme}">{cli_doc}</cbc:ID></cac:PartyIdentification>
<cac:PartyLegalEntity><cbc:RegistrationName>{cli_nombre}</cbc:RegistrationName><cac:RegistrationAddress><cac:Country><cbc:IdentificationCode>{pais}</cbc:IdentificationCode></cac:Country></cac:RegistrationAddress></cac:PartyLegalEntity></cac:Party></cac:AccountingCustomerParty>"""


def comprobante(serie_numero='F001-1', fecha='2026-09-10', tipo='01', moneda='PEN', ruc_emisor='20000000100',
                cli_doc='20100000001', cli_scheme='6', cli_nombre='CLIENTE NACIONAL UNO S.A.C.', pais='PE',
                items=(('SERVICIO DE ASESORIA CONTABLE', 100.0, 'ZZ'),), forma_pago='Contado', exportacion=False,
                operacion='0101', nc_ref=None, nc_motivo='07', raiz=None) -> bytes:
    """items = [(descripción, valor sin IGV, unidad)]. nc_ref = ('F001-1', '01', '2026-09-01') para notas."""
    raiz = raiz or ('CreditNote' if tipo == '07' else 'DebitNote' if tipo == '08' else 'Invoice')
    base = round(sum(v for _, v, _ in items), 2)
    tasa = 0 if exportacion else 18
    igv = round(base * tasa / 100, 2)
    total = round(base + igv, 2)
    esquema = ('9995', 'EXP', 'FRE', '40') if exportacion else ('1000', 'IGV', 'VAT', '10')
    linea_tag = {'Invoice': 'InvoiceLine', 'CreditNote': 'CreditNoteLine', 'DebitNote': 'DebitNoteLine'}[raiz]
    qty_tag = {'Invoice': 'InvoicedQuantity', 'CreditNote': 'CreditedQuantity', 'DebitNote': 'DebitedQuantity'}[raiz]
    lineas = ''
    for i, (desc, valor, unidad) in enumerate(items, start=1):
        ligv = round(valor * tasa / 100, 2)
        lineas += f"""<cac:{linea_tag}><cbc:ID>{i}</cbc:ID><cbc:{qty_tag} unitCode="{unidad}">1</cbc:{qty_tag}>
<cbc:LineExtensionAmount currencyID="{moneda}">{valor:.2f}</cbc:LineExtensionAmount>
<cac:TaxTotal><cbc:TaxAmount currencyID="{moneda}">{ligv:.2f}</cbc:TaxAmount><cac:TaxSubtotal><cbc:TaxableAmount currencyID="{moneda}">{valor:.2f}</cbc:TaxableAmount>
<cbc:TaxAmount currencyID="{moneda}">{ligv:.2f}</cbc:TaxAmount><cac:TaxCategory><cbc:Percent>{tasa}</cbc:Percent><cbc:TaxExemptionReasonCode>{esquema[3]}</cbc:TaxExemptionReasonCode>
<cac:TaxScheme><cbc:ID>{esquema[0]}</cbc:ID><cbc:Name>{esquema[1]}</cbc:Name><cbc:TaxTypeCode>{esquema[2]}</cbc:TaxTypeCode></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>
<cac:Item><cbc:Description>{desc}</cbc:Description></cac:Item><cac:Price><cbc:PriceAmount currencyID="{moneda}">{valor:.2f}</cbc:PriceAmount></cac:Price></cac:{linea_tag}>"""
    tipo_xml = f'<cbc:InvoiceTypeCode listID="{operacion}">{tipo}</cbc:InvoiceTypeCode>' if raiz == 'Invoice' else ''
    ref = ''
    if nc_ref:
        ref = (f'<cac:DiscrepancyResponse><cbc:ReferenceID>{nc_ref[0]}</cbc:ReferenceID><cbc:ResponseCode>{nc_motivo}</cbc:ResponseCode>'
               f'<cbc:Description>DEVOLUCION</cbc:Description></cac:DiscrepancyResponse>'
               f'<cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>{nc_ref[0]}</cbc:ID>'
               f'<cbc:DocumentTypeCode>{nc_ref[1]}</cbc:DocumentTypeCode>'
               + (f'<cbc:IssueDate>{nc_ref[2]}</cbc:IssueDate>' if len(nc_ref) > 2 and nc_ref[2] else '') +
               '</cac:InvoiceDocumentReference></cac:BillingReference>')
    pago = (f'<cac:PaymentTerms><cbc:ID>FormaPago</cbc:ID><cbc:PaymentMeansID>{forma_pago}</cbc:PaymentMeansID></cac:PaymentTerms>'
            if forma_pago and raiz == 'Invoice' else '')
    total_tag = 'LegalMonetaryTotal' if raiz != 'DebitNote' else 'RequestedMonetaryTotal'
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<{raiz} {NS.format(raiz=raiz)}>
<cbc:UBLVersionID>2.1</cbc:UBLVersionID><cbc:ID>{serie_numero}</cbc:ID><cbc:IssueDate>{fecha}</cbc:IssueDate>{tipo_xml}
<cbc:DocumentCurrencyCode>{moneda}</cbc:DocumentCurrencyCode>{ref}
{_partes(ruc_emisor, cli_doc, cli_scheme, cli_nombre, pais)}{pago}
<cac:TaxTotal><cbc:TaxAmount currencyID="{moneda}">{igv:.2f}</cbc:TaxAmount><cac:TaxSubtotal><cbc:TaxableAmount currencyID="{moneda}">{base:.2f}</cbc:TaxableAmount>
<cbc:TaxAmount currencyID="{moneda}">{igv:.2f}</cbc:TaxAmount><cac:TaxCategory><cac:TaxScheme><cbc:ID>{esquema[0]}</cbc:ID><cbc:Name>{esquema[1]}</cbc:Name>
<cbc:TaxTypeCode>{esquema[2]}</cbc:TaxTypeCode></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>
<cac:{total_tag}><cbc:LineExtensionAmount currencyID="{moneda}">{base:.2f}</cbc:LineExtensionAmount><cbc:PayableAmount currencyID="{moneda}">{total:.2f}</cbc:PayableAmount></cac:{total_tag}>
{lineas}
</{raiz}>"""
    return xml.encode('utf-8')


def baja(lineas, fecha_ref='2026-09-10', resumen=False) -> bytes:
    """lineas = [(tipo, serie, numero)]. resumen=True → SummaryDocuments con ConditionCode 3."""
    if resumen:
        cuerpo = ''.join(f'<sac:SummaryDocumentsLine><cbc:LineID>{i}</cbc:LineID><cbc:DocumentTypeCode>{t}</cbc:DocumentTypeCode>'
                         f'<cbc:ID>{s}-{n}</cbc:ID><cac:Status><cbc:ConditionCode>3</cbc:ConditionCode></cac:Status></sac:SummaryDocumentsLine>'
                         for i, (t, s, n) in enumerate(lineas, 1))
        raiz = 'SummaryDocuments'
    else:
        cuerpo = ''.join(f'<sac:VoidedDocumentsLine><cbc:LineID>{i}</cbc:LineID><cbc:DocumentTypeCode>{t}</cbc:DocumentTypeCode>'
                         f'<sac:DocumentSerialID>{s}</sac:DocumentSerialID><sac:DocumentNumberID>{n}</sac:DocumentNumberID>'
                         f'<sac:VoidReasonDescription>ERROR</sac:VoidReasonDescription></sac:VoidedDocumentsLine>'
                         for i, (t, s, n) in enumerate(lineas, 1))
        raiz = 'VoidedDocuments'
    ns = (f'xmlns="urn:sunat:names:specification:ubl:peru:schema:xsd:{raiz}-1" '
          'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
          'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
          'xmlns:sac="urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1"')
    return (f'<?xml version="1.0" encoding="UTF-8"?><{raiz} {ns}><cbc:ID>RA-20260910-1</cbc:ID>'
            f'<cbc:ReferenceDate>{fecha_ref}</cbc:ReferenceDate><cbc:IssueDate>{fecha_ref}</cbc:IssueDate>{cuerpo}</{raiz}>').encode('utf-8')
