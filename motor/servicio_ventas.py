# -*- coding: utf-8 -*-
"""
motor/servicio_ventas.py — Orquestador que usan app.py y cli.py: une la base de datos con el motor.
"""
from __future__ import annotations

from dataclasses import asdict

from .clasificador_ventas import ConfigEmpresa, Contexto, actividad_desde_rubro, firma
from .conceptos import CONCEPTOS, Destino, sugerir_mapa
from .centros_costos import leer_centros_excel, sugerir as sugerir_centros
from .db import BaseDatos
from .entidades import Entidad, Entidades, RegistroExterior, leer_entidades_excel
from .excel_ventas import generar_importacion, generar_reporte, generar_revision
from .ia import crear_ia
from .lector import leer_archivos
from .plan_cuentas import Cuenta, PlanCuentas, leer_plan_excel
from .proceso import Lote, calcular_anticipos, procesar
from .tipo_cambio import TC, HistorialTC, leer_pdf_sunat, leer_tabla


class Servicio:
    def __init__(self, db_url: str | None = None, api_key: str | None = None):
        self.db = BaseDatos(db_url)
        self.api_key = api_key

    # ------------------------------------------------------------ empresas
    def empresas(self) -> list[dict]:
        return self.db.empresas()

    def config(self, ruc: str) -> ConfigEmpresa:
        e = self.db.empresa(ruc)
        if not e:
            return ConfigEmpresa(ruc=ruc)
        cfg = ConfigEmpresa.desde_dict({**e['config'], 'ruc': ruc, 'nombre': e['nombre']})
        return cfg

    def guardar_config(self, cfg: ConfigEmpresa):
        self.db.guardar_empresa(cfg.ruc, cfg.nombre, cfg.a_dict())

    def crear_empresa(self, ruc: str, nombre: str, rubro: str = '', actividad: str = '') -> ConfigEmpresa:
        cfg = ConfigEmpresa(ruc=ruc, nombre=nombre, rubro=rubro,
                            actividad=actividad or actividad_desde_rubro(rubro) or 'comercial',
                            actividad_origen='usuario' if (actividad or actividad_desde_rubro(rubro)) else '')
        self.guardar_config(cfg)
        return cfg

    # ------------------------------------------------------------ plan y mapa
    def cargar_plan(self, ruc: str, data: bytes) -> tuple[PlanCuentas, list[str]]:
        """Guarda el plan y propone el mapa de conceptos (sin confirmar). Devuelve (plan, avisos)."""
        plan = leer_plan_excel(data)
        avisos = []
        if not plan.cuentas:
            raise ValueError('No se encontraron cuentas en el archivo.')
        if plan.ruc and plan.ruc != ruc:
            avisos.append(f'El plan dice RUC {plan.ruc} y la empresa es {ruc}. Verifique que sea el plan correcto.')
        self.db.guardar_plan(ruc, [asdict(c) for c in plan.cuentas])
        cfg = self.config(ruc)
        if plan.giro:
            sug = actividad_desde_rubro(plan.giro)
            if not cfg.rubro:
                cfg.rubro = plan.giro
            if sug and cfg.actividad_origen != 'usuario':
                cfg.actividad = sug
                avisos.append(f'Actividad propuesta según el giro del plan ("{plan.giro}"): {sug}. Puede cambiarla.')
            elif sug and sug != cfg.actividad:
                avisos.append(f'El giro del plan ("{plan.giro}") sugiere actividad {sug}; se mantiene la elegida: {cfg.actividad}.')
            self.guardar_config(cfg)
        sugerido = sugerir_mapa(plan)
        previo = self.db.mapa(ruc)
        cuentas = {}
        for k, d in sugerido.items():
            ant = previo.get(k, {}).get('cuenta', '')
            cuentas[k] = ant if ant and plan.get(ant) else d.cuenta     # se respeta lo confirmado antes si sigue existiendo
        self.db.guardar_mapa(ruc, cuentas, confirmado=False)
        return plan, avisos

    def plan(self, ruc: str) -> PlanCuentas:
        e = self.db.empresa(ruc) or {}
        p = PlanCuentas([Cuenta(r['codigo'], r['descripcion'], tipo=r.get('tipo', '')) for r in self.db.plan(ruc)],
                        empresa=e.get('nombre', ''), ruc=ruc)
        p.reindex()
        return p

    def mapa(self, ruc: str, plan: PlanCuentas | None = None) -> tuple[dict[str, Destino], bool]:
        """(mapa de conceptos traducido al plan, confirmado)."""
        plan = plan or self.plan(ruc)
        filas = self.db.mapa(ruc)
        res: dict[str, Destino] = {}
        for k in CONCEPTOS:
            cod = filas.get(k, {}).get('cuenta', '')
            cu = plan.get(cod) if cod else None
            d = Destino(k)
            if cu:
                d.cuenta, d.es_grupo = cod, not cu.es_hoja
                d.hojas = [h.codigo for h in plan.hojas_bajo(cod)] if d.es_grupo else []
            res[k] = d
        confirmado = bool(filas) and all(r.get('confirmado') for r in filas.values())
        return res, confirmado

    def guardar_mapa(self, ruc: str, cuentas: dict[str, str]):
        self.db.guardar_mapa(ruc, cuentas, confirmado=True)

    # ------------------------------------------------------------ entidades
    def cargar_entidades(self, ruc: str, data: bytes) -> tuple[Entidades, list[str]]:
        ent = leer_entidades_excel(data)
        avisos = []
        if ent.ruc and ent.ruc != ruc:
            avisos.append(f'El Excel de entidades dice RUC {ent.ruc} y la empresa es {ruc}.')
        self.db.guardar_entidades(ruc, [asdict(e) for e in ent.items])
        cfg = self.config(ruc)
        an, bo = ent.codigo_anulado(), ent.codigo_boleta()
        if an:
            cfg.codigo_anulado, cfg.nombre_anulado = an
            avisos.append(f'Código de anulado tomado de las entidades: {an[0]} ({an[1]})')
        else:
            avisos.append('No hay una entidad llamada ANULADO: se usará 00000000000. Créela en NewContaSis.')
        if bo:
            cfg.codigo_boleta, cfg.nombre_boleta = bo
            avisos.append(f'Código de boleta sin documento tomado de las entidades: {bo[0]} ({bo[1]})')
        self.guardar_config(cfg)
        return ent, avisos

    def entidades(self, ruc: str) -> Entidades:
        return Entidades([Entidad(r['codigo'], r['nombre'], r.get('direccion', '')) for r in self.db.entidades(ruc)])

    # ------------------------------------------------------------ centros de costos (opcional)
    def cargar_centros(self, ruc: str, data: bytes) -> tuple[list[dict], list[str]]:
        items, ruc_xl = leer_centros_excel(data)
        if not items:
            raise ValueError('No se encontraron centros de costos en el archivo.')
        avisos = []
        if ruc_xl and ruc_xl != ruc:
            avisos.append(f'El Excel dice RUC {ruc_xl} y la empresa es {ruc}.')
        self.db.guardar_centros(ruc, items)
        cfg = self.config(ruc)
        sug = sugerir_centros(items)
        if sug['servicios'] and not cfg.cc_servicios:
            cfg.cc_servicios = sug['servicios']
        if sug['mercaderia'] and not cfg.cc_mercaderia:
            cfg.cc_mercaderia = sug['mercaderia']
        cfg.usar_centro_costos = True
        self.guardar_config(cfg)
        avisos.append(f'Propuesta: servicios {cfg.cc_servicios or "(ninguno)"} · mercaderías {cfg.cc_mercaderia or "(ninguno)"}. Puede cambiarlos.')
        return items, avisos

    # ------------------------------------------------------------ tipo de cambio
    def cargar_tc(self, nombre: str, data: bytes) -> list[TC]:
        items = leer_pdf_sunat(data) if nombre.lower().endswith('.pdf') else leer_tabla(data)
        self.db.guardar_tc([{'fecha': t.fecha.isoformat(), 'compra': t.compra, 'venta': t.venta, 'fuente': t.fuente} for t in items])
        return items

    def historial_tc(self) -> HistorialTC:
        from datetime import date
        return HistorialTC([TC(date.fromisoformat(r['fecha']), r['compra'], r['venta'], r.get('fuente') or '')
                            for r in self.db.tipos_cambio()])

    # ------------------------------------------------------------ proceso
    def contexto(self, ruc: str, usar_ia: bool = True) -> Contexto:
        cfg = self.config(ruc)
        plan = self.plan(ruc)
        mapa, _ = self.mapa(ruc, plan)
        ent = self.entidades(ruc)
        return Contexto(cfg=cfg, plan=plan, mapa=mapa, tc=self.historial_tc(),
                        exterior=RegistroExterior(self.db.exterior(ruc), ent),
                        reglas=self.db.reglas(ruc), memoria=self.db.memoria(ruc), palabras_cuenta=self.db.palabras(ruc),
                        buscar_original=lambda t, s, n: self.db.comprobante(ruc, t, s, n),
                        ia=crear_ia(self.api_key) if (usar_ia and self.api_key) else None)

    def procesar(self, ruc: str, periodo: str, archivos: list[tuple[str, bytes]], usar_ia: bool = True) -> tuple[Lote, Contexto]:
        ctx = self.contexto(ruc, usar_ia)
        _, confirmado = self.mapa(ruc, ctx.plan)
        lote = procesar(ctx, periodo, leer_archivos(archivos), self.db.ultimos_numeros(ruc, periodo))
        if not confirmado:
            lote.bloqueos.insert(0, 'El mapa de conceptos no está confirmado (pantalla "Plan y conceptos").')
        previos = self.db.ya_exportados(ruc, [(f.tipo, f.serie, f.numero) for f in lote.filas if f.c is not None])
        for f in lote.filas:
            if (f.tipo, f.serie, f.numero) in previos and f.estado != 'excluido':
                f.avisos.append(f'Ya se exportó en un lote anterior ({previos[(f.tipo, f.serie, f.numero)][:16]})')
        return lote, ctx

    def recalcular_anticipos(self, lote: Lote, ctx: Contexto):
        calcular_anticipos(lote, ctx)

    # ------------------------------------------------------------ salida
    def archivos_salida(self, lote: Lote, ctx: Contexto) -> dict[str, bytes]:
        base = f'{ctx.cfg.ruc}_{lote.periodo.replace("-", "")}'
        return {
            f'NEWCONTASIS_VENTAS_{base}.xlsx': generar_importacion(lote.filas, ctx.cfg),
            f'REVISION_VENTAS_{base}.xlsx': generar_revision(lote, ctx.cfg, ctx.plan),
            f'REPORTE_VENTAS_{base}.txt': generar_reporte(lote, ctx.cfg).encode('utf-8'),
        }

    def registrar_descarga(self, lote: Lote, ctx: Contexto, usuario: str = '') -> int:
        """Guarda lo exportado (para NC/ND, anticipos, duplicados y correlativos del mes siguiente),
        aprende las correcciones y fija los códigos de clientes del exterior."""
        filas = []
        for f in lote.exportables:
            filas.append({'tipo': f.tipo, 'serie': f.serie, 'numero': f.numero, 'periodo': lote.periodo, 'fecha': f.fecha,
                          'cliente_doc': f.c.doc_cliente if f.c else '', 'total': float(f.c.importe_total) if f.c else 0.0,
                          'moneda': f.moneda, 'condicion': f.condicion, 'concepto': f.concepto, 'cuenta_base': f.cuenta_base,
                          'cuenta_total': f.cuenta_total, 'es_anticipo': f.es_anticipo, 'estado': f.estado})
            if f.base_manual and f.c is not None and not f.es_anticipo and f.tipo in ('01', '03'):
                self.db.aprender(ctx.cfg.ruc, f.c.doc_cliente, firma(f.c), f.cuenta_base)
        resumen = {'n_xml': sum(1 for f in lote.filas if f.c is not None), 'n_ok': len(lote.exportables),
                   'n_excluidos': len(lote.por_estado('excluido')), 'n_revisar': len(lote.por_estado('revisar'))}
        self.db.guardar_exterior(ctx.cfg.ruc, lote.entidades_nuevas)
        return self.db.guardar_lote(ctx.cfg.ruc, lote.periodo, usuario, resumen, filas)
