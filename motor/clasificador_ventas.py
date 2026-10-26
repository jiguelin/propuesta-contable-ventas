# -*- coding: utf-8 -*-
"""
motor/clasificador_ventas.py — Decide, para cada comprobante de venta: cliente (F/G/H), condición,
anticipo, exportación, concepto, cuenta base (AB), cuenta total (AD), TC, glosa y semáforo.

Orden de decisión de la cuenta base (sección 7.1 de la especificación):
  1. regla de la empresa   2. memoria (cliente + texto)   3. señales del XML
  4. actividad / rubro de la empresa   5. IA (opcional, solo entre cuentas reales)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from .conceptos import CONCEPTOS, Destino, coincide, elegir_subcuenta, tokens
from .correlativos import id_normal
from .entidades import RegistroExterior
from .plan_cuentas import PlanCuentas
from .texto import limpiar
from .tipo_cambio import HistorialTC
from .xml_parser import Comprobante

TIPOS_VENTA = {'01', '03', '07', '08'}
NOMBRE_TIPO = {'01': 'FACTURA', '03': 'BOLETA', '07': 'NOTA DE CREDITO', '08': 'NOTA DE DEBITO'}

# Palabras que indican servicio (castellano e inglés; los XML del portal SUNAT ponen NIU también en servicios)
KW_SERVICIO = (
    'SERVICIO', 'SERVICIOS', 'ASESORIA', 'CONSULTORIA', 'MANTENIMIENTO', 'REPARACION', 'INSTALACION',
    'ALQUILER', 'ARRENDAMIENTO', 'TRANSPORTE', 'FLETE', 'HONORARIOS', 'CAPACITACION', 'CURSO', 'TALLER',
    'HOSTING', 'EVALUACION', 'INSPECCION', 'DESMONTAJE', 'MONTAJE', 'ARENADO',
    'SUPERVISION', 'AUDITORIA', 'CONTABLE', 'LABORAL', 'TRAMITE', 'SOPORTE TECNICO', 'COMISION',
    'PUBLICIDAD', 'MARKETING', 'ASISTENCIA TECNICA', 'MANO DE OBRA',
    'SERVICE', 'MAINTENANCE', 'REPAIR', 'RENTAL', 'CONSULTING', 'TRAINING', 'FEE', 'HORAS',
)
# Palabras ambiguas que NO se usan (también describen productos): DISEÑO, LIMPIEZA, CONFECCION, OBRA, GESTION.
UNIDADES_SERVICIO = {'ZZ', 'HUR', 'DAY', 'MON', 'ANN', 'SERVICIO', 'HORA', 'DÍA', 'MES', 'AÑO'}
UNIDADES_BIEN = {'KGM', 'GRM', 'LTR', 'MTR', 'MTK', 'MTQ', 'GLL', 'BX', 'PK', 'CS', 'DZN', 'SET', 'BG', 'BO',
                 'CT', 'PR', 'RO', 'TNE', 'MIL', 'KILOGRAMO', 'GRAMO', 'LITRO', 'METRO', 'CAJA', 'PAQUETE',
                 'DOCENA', 'JUEGO', 'BOLSA', 'BOTELLA', 'PAR', 'ROLLO', 'TONELADA', 'MILLAR', 'GALÓN'}
# Catálogo 54: códigos de detracción de servicios
DETRACCION_SERVICIOS = {'012', '019', '020', '021', '022', '024', '025', '026', '027', '030', '037'}
KW_ANTICIPO = ('ANTICIPO', 'ADELANTO', 'A CUENTA')
GENERICOS_DEFECTO = ['', '-', '0', '00000000', '0000000', '99999999', '11111111', '00000000000', '12345678']
# NC: motivos de devolución/descuento (cat. 09)
NC_DEVOLUCION = {'06', '07'}
NC_DESCUENTO = {'04', '05', '09'}


# ----------------------------------------------------------------------------- configuración
@dataclass
class ConfigEmpresa:
    ruc: str
    nombre: str = ''
    rubro: str = ''                         # texto libre opcional: "compra y revende joyas"
    actividad: str = 'comercial'            # comercial | servicios | industrial | mixta
    actividad_origen: str = ''              # 'usuario' si la eligió la persona (el giro del plan no la cambia)
    modo_contado: str = 'directo'           # directo (AD = caja) | provision (AD = 1212)
    tc_tipo: str = 'venta'                  # venta | compra
    usd_en_soles: bool = True
    nc_negativo: bool = True
    anticipo_pasivo: bool = True            # anticipo → 1221; final → asiento Debe 1221 / Haber 70
    medio_pago_defecto: str = ''
    cond_boleta: str = 'CON'
    cond_factura: str = 'CRE'
    codigo_anulado: str = '00000000000'
    nombre_anulado: str = 'ANULADO'
    codigo_boleta: str = '03'
    nombre_boleta: str = 'BOLETA'
    genericos: list[str] = field(default_factory=lambda: list(GENERICOS_DEFECTO))
    vinculados: list[str] = field(default_factory=list)
    alerta_boleta_sin_dni: float = 700.0

    @classmethod
    def desde_dict(cls, d: dict) -> 'ConfigEmpresa':
        campos = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in campos})

    def a_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


def actividad_desde_rubro(rubro: str) -> str | None:
    """Sugiere la actividad a partir del texto libre del rubro (o del giro del plan)."""
    t = ' '.join(tokens(rubro))
    if not t:
        return None
    serv = any(k in t for k in ('SERVICIO', 'CONSULTOR', 'ASESOR', 'TRANSPORTE', 'CONSTRUCC', 'MANTENIM', 'EDUCAC', 'SALUD'))
    ind = any(k in t for k in ('FABRIC', 'PRODUC', 'CONFECCION', 'MANUFACT', 'ELABORA', 'INDUSTRI'))
    com = any(k in t for k in ('VENDE', 'VENTA', 'COMERCI', 'REVENDE', 'DISTRIBU', 'IMPORTA', 'BODEGA', 'TIENDA', 'COMPRA'))
    marcadas = [n for n, v in (('servicios', serv), ('industrial', ind), ('comercial', com)) if v]
    if len(marcadas) == 1:
        return marcadas[0]
    if len(marcadas) > 1:
        return 'mixta'
    return None


# ----------------------------------------------------------------------------- resultado
@dataclass
class Fila:
    """Una fila de la propuesta (un comprobante, un anulado o un hueco)."""
    clave: str                       # TIPO|SERIE|NUMERO
    tipo: str
    serie: str
    numero: int
    fecha: str                       # ISO
    c: Comprobante | None = None
    estado: str = 'ok'               # ok | revisar | excluido | anulado | hueco_anulado | faltante
    motivo: str = ''
    avisos: list[str] = field(default_factory=list)
    concepto: str = ''
    cuenta_base: str = ''
    cuenta_total: str = ''
    cuenta_otros: str = ''
    cuenta_icbper: str = ''
    condicion: str = 'CON'
    confianza: int = 0
    origen: str = ''                 # regla | memoria | xml | actividad | ia | editado | referencia
    explicacion: str = ''
    es_anticipo: bool = False
    anticipo_marca: str = ''         # referencia | texto | manual
    es_exportacion: bool = False
    mixta: bool = False
    cli_tipo: str = ''               # F
    cli_doc: str = ''                # G
    cli_nombre: str = ''             # H
    cli_origen: str = ''
    tc: float | None = None
    tc_exacto: bool = True
    glosa: str = ''
    medio_pago: str = ''
    fecha_venc: str = ''
    ref_fecha: str = ''
    ref_tipo: str = ''
    ref_serie: str = ''
    ref_numero: str = ''
    base_manual: bool = False
    total_manual: bool = False
    confirmado: bool = False         # los huecos propuestos como anulados se exportan solo si se confirman

    @property
    def id(self) -> str:
        return f'{self.serie}-{self.numero}'

    @property
    def semaforo(self) -> str:
        if self.estado in ('anulado', 'hueco_anulado', 'faltante', 'excluido'):
            return '⚪'
        if self.confianza >= 95:
            return '🟢'
        return '🟡' if self.confianza >= 75 else '🔴'

    @property
    def moneda(self) -> str:
        return 'D' if self.c is not None and self.c.moneda_codigo == 'USD' else 'S'

    @property
    def exportable(self) -> bool:
        return self.estado in ('ok', 'anulado') or (self.estado == 'hueco_anulado' and self.confirmado)


def clave(tipo: str, serie: str, numero: int) -> str:
    return f'{tipo}|{serie}|{numero}'


def separar(serie_numero: str) -> tuple[str, int | None]:
    if '-' not in (serie_numero or ''):
        return (serie_numero or '').upper(), None
    s, n = serie_numero.split('-', 1)
    n = n.strip()
    return s.strip().upper(), int(n) if n.isdigit() else None


# ----------------------------------------------------------------------------- contexto
@dataclass
class Contexto:
    cfg: ConfigEmpresa
    plan: PlanCuentas
    mapa: dict[str, Destino]
    tc: HistorialTC
    exterior: RegistroExterior
    reglas: list[dict] = field(default_factory=list)          # {palabras: [..], ruc_cliente, cuenta}
    memoria: dict[tuple[str, str], str] = field(default_factory=dict)   # (ruc_cliente, firma) → cuenta
    palabras_cuenta: dict[str, list[str]] = field(default_factory=dict)
    buscar_original: Callable[[str, str, int], dict | None] | None = None   # NC/ND y anticipos de meses previos
    ia: Callable | None = None

    def cuenta_de(self, concepto: str) -> Destino | None:
        d = self.mapa.get(concepto)
        return d if d and d.ok else None


def firma(c: Comprobante) -> str:
    """Firma del texto de los ítems para la memoria: palabras significativas ordenadas (máx. 6)."""
    vistas = []
    for l in c.lineas:
        for t in tokens(l.descripcion):
            if len(t) >= 4 and not t.isdigit() and t not in vistas and t not in ('POR', 'MES', 'PARA'):
                vistas.append(t)
    return ' '.join(sorted(vistas[:6]))


# ----------------------------------------------------------------------------- piezas
def es_generico(doc: str, genericos: list[str]) -> bool:
    d = (doc or '').strip()
    return d in genericos or (d and set(d) <= {'0'}) or d in ('-', '--')


def es_exportacion(c: Comprobante) -> bool:
    if (c.tipo_operacion or '').startswith('02'):
        return True
    if c.exportacion > 0 or any(l.afectacion == '40' for l in c.lineas):
        return True
    return bool(c.pais_cliente) and c.pais_cliente != 'PE' and c.tipo_doc_cliente_cod not in ('6', '1')


def cliente(c: Comprobante, ctx: Contexto, exportacion: bool, f: Fila):
    cfg = ctx.cfg
    cod = (c.tipo_doc_cliente_cod or '').strip()
    doc = (c.doc_cliente or '').strip()
    nombre = limpiar(c.nombre_cliente, True, 60)
    if exportacion and cod not in ('6',):
        codigo, origen = ctx.exterior.codigo_para(c.nombre_cliente, doc if not es_generico(doc, cfg.genericos) else '', c.pais_cliente)
        f.cli_tipo, f.cli_doc, f.cli_nombre, f.cli_origen = '0', codigo, nombre or 'CLIENTE DEL EXTERIOR', f'exterior ({origen})'
        if origen == 'nuevo':
            f.avisos.append(f'Cliente del exterior nuevo: código {codigo}')
        return
    if c.tipo_codigo == '03' or (c.tipo_codigo in ('07', '08') and (c.doc_referencia or '')[:1].upper() == 'B'):
        valido = not es_generico(doc, cfg.genericos) and (
            (cod == '1' and re.fullmatch(r'\d{8}', doc)) or (cod in ('4', '7', 'A', '6') and bool(doc)))
        if not valido:
            f.cli_tipo, f.cli_doc, f.cli_nombre, f.cli_origen = '0', cfg.codigo_boleta, cfg.nombre_boleta, 'boleta sin documento'
            if c.importe_total > Decimal(str(cfg.alerta_boleta_sin_dni)):
                f.avisos.append(f'Boleta mayor a S/ {cfg.alerta_boleta_sin_dni:,.0f} sin DNI (SUNAT exige identificar al comprador)')
            return
    if cod == '6' or (not cod and len(doc) == 11):
        if not re.fullmatch(r'\d{11}', doc):
            f.estado, f.motivo = 'revisar', f'RUC del cliente inválido: "{doc}"'
        f.cli_tipo, f.cli_doc, f.cli_nombre, f.cli_origen = '6', doc[:11], nombre, 'xml'
        return
    if cod in ('1', '4', '7', 'A'):
        f.cli_tipo, f.cli_doc, f.cli_nombre, f.cli_origen = cod, doc[:11], nombre, 'xml'
        if cod == '1' and not re.fullmatch(r'\d{8}', doc):
            f.avisos.append(f'DNI con formato raro: "{doc}"')
        return
    if es_generico(doc, cfg.genericos):
        f.cli_tipo, f.cli_doc, f.cli_nombre, f.cli_origen = '0', cfg.codigo_boleta, cfg.nombre_boleta, 'sin documento'
        if c.tipo_codigo == '01':
            f.estado, f.motivo = 'revisar', 'Factura sin RUC del cliente'
        return
    f.cli_tipo, f.cli_doc, f.cli_nombre, f.cli_origen = '0', doc[:11], nombre, 'xml'


def condicion(c: Comprobante, cfg: ConfigEmpresa) -> tuple[str, str]:
    if c.tipo_codigo in ('07', '08'):
        return 'CRE', 'nota: crédito por defecto'
    fp = (c.forma_pago or '').upper()
    if 'CRED' in fp or c.cuotas:
        return 'CRE', 'forma de pago del XML'
    if 'CONT' in fp:
        return 'CON', 'forma de pago del XML'
    d = cfg.cond_boleta if c.tipo_codigo == '03' else cfg.cond_factura
    return d, 'sin forma de pago en el XML: valor por defecto de la empresa'


def clase_linea(desc: str, unidad: str, unidad_cod: str = '') -> tuple[str | None, str]:
    """('SERVICIO' | 'BIEN' | None, motivo)."""
    tt = tokens(desc)
    hits = [k for k in KW_SERVICIO if all(coincide(w, tt) for w in k.split())]
    if hits:
        return 'SERVICIO', f'palabra {hits[0]}'
    u = (unidad or '').upper()
    if u in UNIDADES_SERVICIO:
        return 'SERVICIO', f'unidad {u}'
    if u in UNIDADES_BIEN:
        return 'BIEN', f'unidad {u}'
    return None, ''


def tipo_venta(c: Comprobante, ctx: Contexto) -> tuple[str, int, str, bool]:
    """Devuelve (SERVICIO|MERCADERIA|PT, confianza, motivo, mixta)."""
    act = ctx.cfg.actividad or actividad_desde_rubro(ctx.cfg.rubro) or 'comercial'
    bien = 'PT' if act == 'industrial' else 'MERCADERIA'
    serv_doc = c.detraccion_codigo in DETRACCION_SERVICIOS
    importes: dict[str, Decimal] = {}
    motivos: dict[str, str] = {}
    desconocidas = Decimal(0)
    for l in c.lineas:
        k, m = clase_linea(l.descripcion, l.unidad)
        if k is None and serv_doc:
            k, m = 'SERVICIO', f'detracción {c.detraccion_codigo} (servicio)'
        if k is None:
            desconocidas += abs(l.valor_venta)
            continue
        k = bien if k == 'BIEN' else k
        importes[k] = importes.get(k, Decimal(0)) + abs(l.valor_venta)
        motivos.setdefault(k, m)
    if not c.lineas:
        if serv_doc:
            return 'SERVICIO', 90, 'detracción de servicio, sin ítems', False
        return ('SERVICIO' if act == 'servicios' else bien), 75, 'sin ítems: según la actividad', False
    por_act = 'SERVICIO' if act == 'servicios' else bien
    if desconocidas:
        importes[por_act] = importes.get(por_act, Decimal(0)) + desconocidas
        motivos.setdefault(por_act, f'actividad de la empresa ({act})')
    clase = max(importes, key=importes.get)
    mixta = len(importes) > 1
    if act == 'mixta':
        conf = 92 if motivos.get(clase, '').startswith(('palabra', 'unidad', 'detrac')) else 80
    elif clase == por_act:
        conf = 95
    else:
        conf = 88      # p. ej. una empresa de servicios que vende un bien
    return clase, conf, motivos.get(clase, ''), mixta


def concepto_de(clase: str, exportacion: bool, vinculado: bool) -> str:
    base = {'SERVICIO': 'SERVICIO', 'MERCADERIA': 'VENTA_MERCADERIA', 'PT': 'VENTA_PT'}[clase]
    return f"{base}_{'EXPORT' if exportacion else 'LOCAL'}{'_REL' if vinculado else ''}"


def cuenta_para_concepto(concepto: str, c: Comprobante | None, ctx: Contexto, clase: str = '') -> tuple[str, int, str, str]:
    """Traduce el concepto a una cuenta hoja del plan. (cuenta, confianza, motivo, concepto_usado)."""
    d = ctx.cuenta_de(concepto)
    usado = concepto
    if not d and concepto.endswith('_REL'):
        usado = concepto[:-4]
        d = ctx.cuenta_de(usado)
    if not d:
        return '', 0, f'concepto {concepto} sin cuenta en el mapa de la empresa', usado
    if not d.es_grupo:
        return d.cuenta, 100, '', usado
    textos = []
    if c is not None:
        for l in c.lineas:
            k, _ = clase_linea(l.descripcion, l.unidad)
            if not clase or k is None or (k == 'SERVICIO') == (clase == 'SERVICIO'):
                textos.append((l.descripcion, float(abs(l.valor_venta))))
    cuenta, conf, mot = elegir_subcuenta(ctx.plan, d.hojas, textos, ctx.palabras_cuenta)
    return cuenta, conf, mot, usado


def regla_aplicable(c: Comprobante, ctx: Contexto) -> dict | None:
    tt = tokens(' '.join(l.descripcion for l in c.lineas))
    for r in sorted(ctx.reglas, key=lambda r: r.get('prioridad', 100)):
        if not r.get('activa', True):
            continue
        if r.get('ruc_cliente') and r['ruc_cliente'] != c.doc_cliente:
            continue
        palabras = [p for p in r.get('palabras', []) if p]
        if palabras and not all(any(coincide(t, tt) for t in tokens(p)) for p in palabras):
            continue
        if not palabras and not r.get('ruc_cliente'):
            continue
        if ctx.plan.get(r.get('cuenta', '')):
            return r
    return None


def glosa_de(f: Fila) -> str:
    if f.estado in ('anulado', 'hueco_anulado'):
        return 'ANULADO'
    if f.es_anticipo:
        base = 'ANTICIPO DE CLIENTE'
    elif f.tipo == '07':
        base = 'NOTA DE CREDITO'
    elif f.tipo == '08':
        base = 'NOTA DE DEBITO'
    elif f.concepto.startswith('SERVICIO'):
        base = 'PRESTACION DE SERVICIOS'
    elif f.concepto.startswith('VENTA_PT'):
        base = 'VENTA DE PRODUCTOS TERMINADOS'
    elif f.concepto.startswith('VENTA_MERC'):
        base = 'VENTA DE MERCADERIAS'
    else:
        base = 'VENTA'
    if f.es_exportacion and f.tipo not in ('07', '08'):
        base = base.replace('VENTA DE', 'EXPORTACION DE').replace('PRESTACION DE SERVICIOS', 'EXPORTACION DE SERVICIOS')
    if f.tipo in ('01', '03') and not f.es_anticipo:
        base += ' AL CONTADO' if f.condicion == 'CON' else ' AL CREDITO'
    return limpiar(base, True, 60)


def cuenta_total(f: Fila, ctx: Contexto) -> str:
    usd = f.moneda == 'D'
    if f.condicion == 'CON' and ctx.cfg.modo_contado == 'directo':
        d = ctx.cuenta_de('CAJA_USD' if usd else 'CAJA_PEN')
    else:
        d = ctx.cuenta_de('CXC_USD' if usd else 'CXC_PEN')
    return d.cuenta if d and not d.es_grupo else (d.hojas[0] if d and d.hojas else '')


# ----------------------------------------------------------------------------- principal
def clasificar(c: Comprobante, ctx: Contexto, anticipos_ref: dict[str, str], lote_por_id: dict[str, 'Fila']) -> Fila:
    serie, num = separar(c.serie_numero)
    f = Fila(clave(c.tipo_codigo, serie, num or 0), c.tipo_codigo, serie, num or 0, c.fecha_emision, c=c)
    f.fecha_venc = c.fecha_vencimiento or c.fecha_emision
    exp = es_exportacion(c)
    f.es_exportacion = exp
    cliente(c, ctx, exp, f)
    f.condicion, mot_cond = condicion(c, ctx.cfg)
    vinculado = c.doc_cliente in ctx.cfg.vinculados

    # --- NC / ND: documento modificado ---
    original = None
    if c.tipo_codigo in ('07', '08'):
        rs, rn = separar(c.doc_referencia)
        f.ref_serie, f.ref_numero = rs, str(rn or '')
        f.ref_tipo = c.doc_referencia_tipo or ('03' if rs[:1] == 'B' else '01')
        original = lote_por_id.get(id_normal(c.doc_referencia))
        orig_dict = None
        if original is None and ctx.buscar_original and rn:
            orig_dict = ctx.buscar_original(f.ref_tipo, rs, rn)
        f.ref_fecha = c.doc_referencia_fecha or (original.fecha if original else (orig_dict or {}).get('fecha', ''))
        if not c.doc_referencia:
            f.estado, f.motivo = 'revisar', 'Nota sin documento modificado'
        elif not f.ref_fecha:
            f.estado, f.motivo = 'revisar', f'Falta la fecha de {c.doc_referencia} (no está en el XML ni en lotes anteriores)'
        if original is not None:
            f.cuenta_base, f.confianza, f.origen, f.concepto = original.cuenta_base, 95, 'referencia', original.concepto
            f.explicacion = f'misma cuenta que {c.doc_referencia}'
        elif orig_dict and orig_dict.get('cuenta_base'):
            f.cuenta_base, f.confianza, f.origen, f.concepto = orig_dict['cuenta_base'], 95, 'referencia', orig_dict.get('concepto', '')
            f.explicacion = f'misma cuenta que {c.doc_referencia} (lote anterior)'
        if (c.tipo_operacion or '') == '13' or (c.motivo_referencia and c.importe_total == 0):
            f.avisos.append('NC de ajuste de cuotas o importe cero: revise')

    # --- anticipo ---
    sid = id_normal(c.serie_numero)
    texto = ' '.join(tokens(' '.join(l.descripcion for l in c.lineas)))
    if c.tipo_codigo in ('01', '03'):
        if sid in anticipos_ref:
            f.es_anticipo, f.anticipo_marca = True, 'referencia'
            f.avisos.append(f'Anticipo: lo descuenta {anticipos_ref[sid]}')
        elif any(k in texto for k in KW_ANTICIPO) and not c.anticipos_aplicados:
            f.es_anticipo, f.anticipo_marca = True, 'texto'
    if c.anticipos_aplicados:
        f.avisos.append('Descuenta anticipos: ' + ', '.join(f"{a['id']} ({a['monto']})" for a in c.anticipos_aplicados))

    # --- cuenta base ---
    if not f.cuenta_base:
        if f.es_anticipo and ctx.cfg.anticipo_pasivo:
            d = ctx.cuenta_de('ANTICIPO_RECIBIDO')
            f.concepto = 'ANTICIPO_RECIBIDO'
            f.cuenta_base = d.cuenta if d and not d.es_grupo else (d.hojas[0] if d and d.hojas else '')
            f.confianza = 95 if f.anticipo_marca == 'referencia' else 88
            f.origen, f.explicacion = 'referencia', 'anticipo recibido (se aplica después con Debe 1221 / Haber 70)'
        else:
            regla = regla_aplicable(c, ctx)
            clase_v, conf_v, mot_v, mixta = tipo_venta(c, ctx)
            f.mixta = mixta
            f.concepto = concepto_de(clase_v, exp, vinculado)
            mem = ctx.memoria.get((c.doc_cliente, firma(c))) or ctx.memoria.get(('*', firma(c)))
            if regla:
                f.cuenta_base, f.confianza, f.origen = regla['cuenta'], 99, 'regla'
                f.explicacion = f"regla: {regla.get('nombre') or ', '.join(regla.get('palabras', []))}"
            elif mem and ctx.plan.get(mem):
                f.cuenta_base, f.confianza, f.origen = mem, 97, 'memoria'
                f.explicacion = 'misma cuenta que la corrección anterior para este cliente y concepto'
            else:
                cuenta, conf_c, mot_c, usado = cuenta_para_concepto(f.concepto, c, ctx, clase_v)
                if usado != f.concepto:
                    f.avisos.append(f'{f.concepto} sin cuenta propia; se usa {usado}')
                    f.concepto = usado
                f.cuenta_base = cuenta
                f.confianza = min(conf_v, conf_c) if cuenta else 0
                f.origen = 'xml' if mot_v.startswith(('palabra', 'unidad', 'detrac')) else 'actividad'
                f.explicacion = '; '.join(x for x in (f'{CONCEPTOS[usado].nombre} por {mot_v}' if usado in CONCEPTOS else mot_v, mot_c) if x)
                if mixta:
                    f.avisos.append('Comprobante mixto (bienes y servicios): se usa la cuenta del mayor importe')
            if ctx.ia and f.confianza < 90 and f.origen not in ('regla', 'memoria'):
                try:
                    r = ctx.ia(f, ctx)
                except Exception as ex:            # la IA nunca rompe el proceso
                    r = None
                    f.avisos.append(f'IA no disponible: {ex.__class__.__name__}')
                if r:
                    f.cuenta_base, f.confianza, f.explicacion = r[0], max(f.confianza, r[1]), f'IA: {r[2]}'
                    f.origen = 'ia'
    if not f.cuenta_base:
        f.estado, f.motivo = 'revisar', f.motivo or 'Sin cuenta de ventas: complete el mapa de conceptos'
    elif not ctx.plan.get(f.cuenta_base):
        f.estado, f.motivo = 'revisar', f'La cuenta {f.cuenta_base} no existe en el plan'

    # --- cuenta total, otros, TC, glosa, medio de pago ---
    f.cuenta_total = cuenta_total(f, ctx)
    if not f.cuenta_total:
        f.estado, f.motivo = 'revisar', f.motivo or 'Sin cuenta de caja / por cobrar en el mapa'
    if (c.otros_tributos + c.otros_cargos) != 0:
        d = ctx.cuenta_de('OTROS_TRIBUTOS')
        f.cuenta_otros = d.cuenta if d else ''
        if not f.cuenta_otros:
            f.avisos.append('Tiene otros tributos/cargos y no hay cuenta OTROS_TRIBUTOS en el mapa')
    if c.icbper:
        d = ctx.cuenta_de('ICBPER')
        f.cuenta_icbper = d.cuenta if d else ''
    tc, exacto = ctx.tc.valor(c.fecha_emision, ctx.cfg.tc_tipo)
    f.tc, f.tc_exacto = tc, exacto
    if tc is None:
        f.estado, f.motivo = 'revisar', f.motivo or f'Sin tipo de cambio para {c.fecha_emision}'
    elif not exacto:
        f.avisos.append('TC del día inmediato anterior (sin publicación ese día)')
    if f.condicion == 'CON':
        f.medio_pago = c.medio_pago if re.fullmatch(r'\d{3}', c.medio_pago or '') else ctx.cfg.medio_pago_defecto
    if c.tiene_detraccion:
        f.avisos.append(f'Detracción {c.detraccion_codigo} {c.detraccion_porcentaje}% (solo informativa)')
    if c.tiene_percepcion:
        f.avisos.append('Trae percepción: revise (no se registra)')
    if c.gratuitas:
        f.avisos.append(f'Operación gratuita por {c.gratuitas}: no se registra base')
    if 0 < f.confianza < 75 and f.estado == 'ok':
        f.estado, f.motivo = 'revisar', 'Confianza baja: confirme la cuenta'
    f.glosa = glosa_de(f)
    return f
