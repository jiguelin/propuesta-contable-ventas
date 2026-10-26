# -*- coding: utf-8 -*-
"""
motor/proceso.py — Procesa un lote (una empresa + un mes) sin tocar la base de datos.

  lectura (XML/ZIP) → filtros (empresa, periodo, duplicados) → clasificación → bajas (RA/RC)
  → huecos de correlativos → anticipos aplicados (asiento complementario) → validaciones
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .clasificador_ventas import (TIPOS_VENTA, ConfigEmpresa, Contexto, Fila, centro_costos, clasificar, clave,
                                  cuenta_total, glosa_de, separar)
from .conceptos import CONCEPTOS
from .correlativos import buscar_huecos, id_normal
from .lector import Lectura

D2 = Decimal('0.01')


@dataclass
class Lote:
    ruc: str
    periodo: str                                          # AAAA-MM
    filas: list[Fila] = field(default_factory=list)
    no_procesados: list[tuple[str, str]] = field(default_factory=list)
    anticipos: list[dict] = field(default_factory=list)   # asientos complementarios Debe 1221 / Haber 70
    entidades_nuevas: list[dict] = field(default_factory=list)
    bloqueos: list[str] = field(default_factory=list)

    def por_estado(self, *estados: str) -> list[Fila]:
        return [f for f in self.filas if f.estado in estados]

    @property
    def exportables(self) -> list[Fila]:
        return [f for f in self.filas if f.exportable]


def _fila_anulada(f: Fila, ctx: Contexto, motivo: str, estado: str = 'anulado'):
    cfg = ctx.cfg
    f.estado, f.motivo = estado, motivo
    f.cli_tipo, f.cli_doc, f.cli_nombre, f.cli_origen = '0', cfg.codigo_anulado, cfg.nombre_anulado, 'anulado'
    f.condicion = 'CRE'
    f.es_anticipo = False
    if not f.cuenta_base:
        venta = 'SERVICIO_LOCAL' if cfg.actividad == 'servicios' else ('VENTA_PT_LOCAL' if cfg.actividad == 'industrial' else 'VENTA_MERCADERIA_LOCAL')
        d = ctx.cuenta_de(venta) or ctx.cuenta_de('VENTA_MERCADERIA_LOCAL') or ctx.cuenta_de('SERVICIO_LOCAL')
        f.cuenta_base = (d.hojas[0] if d.es_grupo else d.cuenta) if d else ''
        f.concepto = venta
    f.cuenta_total = cuenta_total(f, ctx)
    if f.tc is None:
        f.tc, f.tc_exacto = ctx.tc.valor(f.fecha, cfg.tc_tipo)
    f.medio_pago, f.glosa, f.confianza = '', 'ANULADO', 100
    f.fecha_venc = f.fecha
    f.centro_costos = centro_costos(f, ctx)


def cuadre(f: Fila) -> Decimal:
    """Diferencia P − (I+J+K+L+M+N+O+AQ) en la moneda del comprobante."""
    c = f.c
    suma = c.exportacion + c.gravadas + c.exoneradas + c.inafectas + c.isc + c.igv + c.otros_tributos + c.otros_cargos + c.icbper
    return (c.importe_total - suma).quantize(D2)


def procesar(ctx: Contexto, periodo: str, lectura: Lectura, ultimo_previo: dict | None = None) -> Lote:
    cfg: ConfigEmpresa = ctx.cfg
    lote = Lote(cfg.ruc, periodo, no_procesados=list(lectura.no_procesados))

    # ------------------------------------------------------------ 1. filtros
    validos, vistos = [], {}
    for c in lectura.comprobantes:
        serie, num = separar(c.serie_numero)
        f = Fila(clave(c.tipo_codigo, serie, num or 0), c.tipo_codigo, serie, num or 0, c.fecha_emision, c=c)
        if c.error:
            f.estado, f.motivo = 'excluido', c.error
        elif c.ruc_emisor != cfg.ruc:
            f.estado, f.motivo = 'excluido', f'El emisor {c.ruc_emisor} no es la empresa (¿es una compra o de otra empresa?)'
        elif c.tipo_codigo not in TIPOS_VENTA:
            f.estado, f.motivo = 'excluido', f'Tipo de comprobante {c.tipo_codigo} no va al registro de ventas'
        elif (c.fecha_emision or '')[:7] != periodo:
            f.estado, f.motivo = 'excluido', f'Fuera de periodo ({(c.fecha_emision or "")[:7]})'
        elif num is None:
            f.estado, f.motivo = 'excluido', f'Serie-número ilegible: {c.serie_numero}'
        elif f.clave in vistos:
            f.estado, f.motivo = 'excluido', f'Duplicado (ya se leyó en {vistos[f.clave]})'
        if f.estado == 'excluido':
            f.cli_nombre = c.nombre_cliente[:60]
            lote.filas.append(f)
            continue
        vistos[f.clave] = c.archivo or c.serie_numero
        validos.append(c)

    # ------------------------------------------------------------ 2. anticipos referenciados en el lote
    anticipos_ref: dict[str, str] = {}
    for c in validos:
        for a in c.anticipos_aplicados:
            anticipos_ref.setdefault(id_normal(a['id']), c.serie_numero)

    # ------------------------------------------------------------ 3. clasificación (NC/ND al final para heredar la cuenta)
    por_id: dict[str, Fila] = {}
    for c in sorted(validos, key=lambda x: (x.tipo_codigo in ('07', '08'), x.fecha_emision, x.serie_numero)):
        f = clasificar(c, ctx, anticipos_ref, por_id)
        por_id[id_normal(c.serie_numero)] = f
        dif = cuadre(f)
        if abs(dif) > Decimal('0.01') and f.estado == 'ok':
            f.estado, f.motivo = 'revisar', f'No cuadra: total − suma de importes = {dif}'
        lote.filas.append(f)

    # ------------------------------------------------------------ 4. bajas (Comunicación de Baja / Resumen diario)
    for b in lectura.bajas:
        k = clave(b.tipo, b.serie, b.numero)
        existente = next((f for f in lote.filas if f.clave == k and f.estado != 'excluido'), None)
        if existente:
            _fila_anulada(existente, ctx, f'Anulado según {b.fuente}')
        elif (b.fecha or '')[:7] == periodo:
            f = Fila(k, b.tipo, b.serie, b.numero, b.fecha)
            _fila_anulada(f, ctx, f'Anulado según {b.fuente} (sin XML del comprobante)')
            lote.filas.append(f)

    # ------------------------------------------------------------ 5. huecos de correlativos
    docs = [(f.tipo, f.serie, f.numero, f.fecha) for f in lote.filas if f.estado != 'excluido']
    for h in buscar_huecos(docs, anticipos_ref, ultimo_previo):
        f = Fila(clave(h.tipo, h.serie, h.numero), h.tipo, h.serie, h.numero, h.fecha)
        if h.clase == 'faltante':
            f.estado, f.motivo = 'faltante', h.motivo
            f.glosa = 'XML FALTANTE'
        else:
            _fila_anulada(f, ctx, h.motivo, estado='hueco_anulado')
        lote.filas.append(f)

    # ------------------------------------------------------------ 6. anticipos aplicados → asiento complementario
    calcular_anticipos(lote, ctx)

    # ------------------------------------------------------------ 7. bloqueos
    faltan = [k for k, con in CONCEPTOS.items() if con.obligatorio and not ctx.cuenta_de(k)]
    if not ctx.plan.cuentas:
        lote.bloqueos.append('La empresa no tiene plan de cuentas cargado.')
    if faltan:
        lote.bloqueos.append('Mapa de conceptos incompleto: ' + ', '.join(faltan))
    sin_tc = sorted({f.fecha for f in lote.filas if f.estado in ('ok', 'revisar', 'anulado', 'hueco_anulado') and f.tc is None})
    if sin_tc:
        lote.bloqueos.append('Falta el tipo de cambio de: ' + ', '.join(sin_tc[:10]) + (' …' if len(sin_tc) > 10 else '') +
                             '. Suba el PDF de SUNAT del mes.')
    lote.entidades_nuevas = list(ctx.exterior.nuevos)
    lote.filas.sort(key=lambda f: ({'ok': 0, 'revisar': 0, 'anulado': 0, 'hueco_anulado': 0, 'faltante': 1, 'excluido': 2}[f.estado],
                                   f.fecha or '', f.tipo, f.serie, f.numero))
    return lote


def calcular_anticipos(lote: Lote, ctx: Contexto):
    """Por cada comprobante final que descuenta anticipos: Debe 1221 / Haber 70 por la base del anticipo."""
    lote.anticipos = []
    por_id = {f.id: f for f in lote.filas if f.c is not None and f.estado != 'excluido'}
    d_ant = ctx.cuenta_de('ANTICIPO_RECIBIDO')
    cta_ant = (d_ant.hojas[0] if d_ant and d_ant.es_grupo else (d_ant.cuenta if d_ant else ''))
    for f in lote.filas:
        c = f.c
        if c is None or f.estado not in ('ok', 'revisar') or not c.anticipos_aplicados:
            continue
        tasa = c.tasa_igv if c.igv else Decimal(0)
        k = Decimal(str(f.tc)) if (f.moneda == 'D' and ctx.cfg.usd_en_soles and f.tc) else Decimal(1)
        for a in c.anticipos_aplicados:
            aid = id_normal(a['id'])
            monto = Decimal(a['monto'])
            base = (monto / (1 + tasa / 100)).quantize(D2, ROUND_HALF_UP) if tasa else monto
            orig = por_id.get(aid)
            registrado = ''
            if orig is not None:
                registrado = orig.cuenta_base
                origen = 'mismo lote'
            elif ctx.buscar_original:
                s, n = separar(aid)
                o = ctx.buscar_original('03' if s.startswith('B') else '01', s, n or 0) if n else None
                registrado = (o or {}).get('cuenta_base', '')
                origen = f"lote {(o or {}).get('periodo', '')}" if o else 'sin registro en la app'
            else:
                origen = 'sin registro en la app'
            if registrado and cta_ant and not registrado.startswith(cta_ant[:3]):
                accion = f'El anticipo se registró en {registrado}: no corresponde el asiento (ya está en ventas)'
                proponer = False
            else:
                accion = 'Registrar asiento complementario' + ('' if registrado else ' (verifique que el anticipo se registró en 1221)')
                proponer = True
            lote.anticipos.append({
                'final': f.id, 'fecha': f.fecha, 'anticipo': aid, 'monto_anticipo': monto, 'base': base,
                'base_soles': (base * k).quantize(D2, ROUND_HALF_UP), 'moneda': f.moneda,
                'debe': cta_ant, 'haber': f.cuenta_base, 'origen': origen, 'proponer': proponer, 'accion': accion,
            })


def aplicar_cambio(f: Fila, ctx: Contexto, **cambios):
    """Edición desde la pantalla: actualiza la fila y recalcula lo que depende de ella."""
    if 'cuenta_base' in cambios and cambios['cuenta_base'] != f.cuenta_base:
        f.cuenta_base, f.base_manual, f.origen = cambios['cuenta_base'], True, 'editado'
        f.confianza = 100
        motivo_base = f.motivo.startswith(('Confianza', 'Sin cuenta de ventas', 'La cuenta')) and not f.motivo.startswith('La cuenta total')
        if f.estado == 'revisar' and ctx.plan.get(f.cuenta_base) and motivo_base:
            f.estado, f.motivo = 'ok', ''
        elif f.estado == 'ok' and not ctx.plan.get(f.cuenta_base):
            f.estado, f.motivo = 'revisar', f'La cuenta {f.cuenta_base or "(vacía)"} no existe en el plan'
    if 'condicion' in cambios and cambios['condicion'] in ('CON', 'CRE') and cambios['condicion'] != f.condicion:
        f.condicion = cambios['condicion']
        if not f.total_manual:
            f.cuenta_total = cuenta_total(f, ctx)
        f.glosa = glosa_de(f, ctx.cfg.glosa_detalle)
    if 'cuenta_total' in cambios and cambios['cuenta_total'] != f.cuenta_total:
        f.cuenta_total, f.total_manual = cambios['cuenta_total'], True
        if f.estado == 'ok' and not ctx.plan.get(f.cuenta_total):
            f.estado, f.motivo = 'revisar', f'La cuenta total {f.cuenta_total or "(vacía)"} no existe en el plan'
        elif f.estado == 'revisar' and f.motivo.startswith(('La cuenta total', 'Sin cuenta de caja')) and ctx.plan.get(f.cuenta_total):
            f.estado, f.motivo = 'ok', ''
    if 'es_anticipo' in cambios and bool(cambios['es_anticipo']) != f.es_anticipo and f.tipo in ('01', '03'):
        f.es_anticipo = bool(cambios['es_anticipo'])
        f.anticipo_marca = 'manual' if f.es_anticipo else ''
        if not f.base_manual and ctx.cfg.anticipo_pasivo:
            if f.es_anticipo:
                d = ctx.cuenta_de('ANTICIPO_RECIBIDO')
                f.cuenta_base = (d.hojas[0] if d.es_grupo else d.cuenta) if d else f.cuenta_base
                f.concepto = 'ANTICIPO_RECIBIDO'
            elif f.c is not None:
                from .clasificador_ventas import clasificar as _cl
                g = _cl(f.c, ctx, {}, {})
                f.cuenta_base, f.concepto = g.cuenta_base, g.concepto
        f.glosa = glosa_de(f, ctx.cfg.glosa_detalle)
    if 'centro_costos' in cambios:
        f.centro_costos = (cambios['centro_costos'] or '')[:9]
    if 'glosa' in cambios:
        f.glosa = (cambios['glosa'] or '')[:60]
    if 'confirmado' in cambios:
        f.confirmado = bool(cambios['confirmado'])
    if 'estado' in cambios and f.estado in ('hueco_anulado', 'faltante') and cambios['estado'] in ('hueco_anulado', 'faltante'):
        if cambios['estado'] == 'hueco_anulado' and f.estado == 'faltante':
            _fila_anulada(f, ctx, 'Marcado como anulado por el usuario', 'hueco_anulado')
        elif cambios['estado'] == 'faltante':
            f.estado, f.motivo, f.glosa = 'faltante', 'Marcado por el usuario: falta el XML', 'XML FALTANTE'
    if ('cuenta_base' in cambios or 'es_anticipo' in cambios) and 'centro_costos' not in cambios:
        f.centro_costos = centro_costos(f, ctx)
