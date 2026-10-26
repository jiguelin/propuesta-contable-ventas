# -*- coding: utf-8 -*-
"""
motor/db.py — Base de datos propia de la app de ventas (no se conecta a la de compras).

Postgres (Supabase o Neon) en producción: DATABASE_URL = "postgresql+psycopg2://usuario:clave@host/base".
Sin DATABASE_URL usa SQLite local en datos/ventas.db (sirve para pruebas; en Streamlit Cloud se borra
al reiniciar la app).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (Boolean, Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete,
                        func, insert, select, update)

meta = MetaData()

empresas = Table('empresas', meta,
                 Column('ruc', String(11), primary_key=True),
                 Column('nombre', String(200), nullable=False),
                 Column('config', Text, default='{}'),
                 Column('plan_cargado', String(30), default=''),
                 Column('actualizado', String(30)))
plan_cuentas = Table('plan_cuentas', meta,
                     Column('empresa_ruc', String(11), primary_key=True),
                     Column('codigo', String(12), primary_key=True),
                     Column('descripcion', String(300)),
                     Column('tipo', String(40), default=''))
mapa_conceptos = Table('v_mapa_conceptos', meta,
                       Column('empresa_ruc', String(11), primary_key=True),
                       Column('concepto', String(40), primary_key=True),
                       Column('cuenta', String(12), default=''),
                       Column('confirmado', Boolean, default=False),
                       Column('confirmado_en', String(30), default=''))
palabras_cuenta = Table('v_palabras_cuenta', meta,
                        Column('empresa_ruc', String(11), primary_key=True),
                        Column('cuenta', String(12), primary_key=True),
                        Column('palabras', Text, default=''))
entidades = Table('v_entidades', meta,
                  Column('empresa_ruc', String(11), primary_key=True),
                  Column('codigo', String(20), primary_key=True),
                  Column('nombre', String(200)),
                  Column('direccion', String(300), default=''))
clientes_exterior = Table('v_clientes_exterior', meta,
                          Column('empresa_ruc', String(11), primary_key=True),
                          Column('codigo', String(20), primary_key=True),
                          Column('nombre', String(200)),
                          Column('id_extranjero', String(40), default=''),
                          Column('pais', String(4), default=''),
                          Column('origen', String(20), default=''),
                          Column('creado_en', String(30)))
centros_costos = Table('v_centros_costos', meta,
                       Column('empresa_ruc', String(11), primary_key=True),
                       Column('codigo', String(9), primary_key=True),
                       Column('descripcion', String(200), default=''),
                       Column('es_hoja', Boolean, default=True))
tipo_cambio = Table('tipo_cambio', meta,
                    Column('fecha', String(10), primary_key=True),
                    Column('compra', Float), Column('venta', Float),
                    Column('fuente', String(40)), Column('cargado', String(30)))
reglas = Table('reglas', meta,
               Column('id', Integer, primary_key=True, autoincrement=True),
               Column('empresa_ruc', String(11), default='*'),
               Column('nombre', String(120), default=''),
               Column('palabras', Text, default=''),
               Column('ruc_cliente', String(11), default=''),
               Column('cuenta', String(12)),
               Column('prioridad', Integer, default=100),
               Column('activa', Boolean, default=True))
memoria = Table('v_memoria', meta,
                Column('empresa_ruc', String(11), primary_key=True),
                Column('ruc_cliente', String(20), primary_key=True),
                Column('firma', String(200), primary_key=True),
                Column('cuenta', String(12)),
                Column('veces', Integer, default=1),
                Column('ultima', String(30)))
lotes = Table('v_lotes', meta,
              Column('id', Integer, primary_key=True, autoincrement=True),
              Column('empresa_ruc', String(11)), Column('periodo', String(7)), Column('usuario', String(80), default=''),
              Column('creado_en', String(30)), Column('n_xml', Integer), Column('n_ok', Integer),
              Column('n_excluidos', Integer), Column('n_revisar', Integer), Column('resumen', Text, default=''))
comprobantes = Table('v_comprobantes', meta,
                     Column('empresa_ruc', String(11), primary_key=True),
                     Column('tipo', String(2), primary_key=True),
                     Column('serie', String(6), primary_key=True),
                     Column('numero', Integer, primary_key=True),
                     Column('periodo', String(7)), Column('fecha', String(10)), Column('cliente_doc', String(20)),
                     Column('total', Float), Column('moneda', String(1)), Column('condicion', String(3)),
                     Column('concepto', String(40)), Column('cuenta_base', String(12)), Column('cuenta_total', String(12)),
                     Column('es_anticipo', Boolean, default=False), Column('estado', String(20)),
                     Column('lote_id', Integer))


def _ahora() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def url_local() -> str:
    """Base SQLite temporal dentro de la app (datos/ventas.db)."""
    ruta = Path(__file__).resolve().parent.parent / 'datos' / 'ventas.db'
    ruta.parent.mkdir(parents=True, exist_ok=True)
    return f'sqlite:///{ruta}'


class BaseDatos:
    def __init__(self, url: str | None = None):
        url = url or os.environ.get('DATABASE_URL') or url_local()
        if url.startswith('postgres://'):            # formato de Heroku/Supabase antiguo
            url = 'postgresql+psycopg2://' + url[len('postgres://'):]
        self.url = url
        self.es_sqlite = url.startswith('sqlite')
        self.engine = create_engine(url, pool_pre_ping=True, future=True)
        meta.create_all(self.engine)

    # ------------------------------------------------------------ empresas
    def empresas(self) -> list[dict]:
        with self.engine.connect() as cx:
            return [dict(r._mapping) for r in cx.execute(select(empresas).order_by(empresas.c.nombre))]

    def empresa(self, ruc: str) -> dict | None:
        with self.engine.connect() as cx:
            r = cx.execute(select(empresas).where(empresas.c.ruc == ruc)).first()
        if not r:
            return None
        d = dict(r._mapping)
        d['config'] = json.loads(d.get('config') or '{}')
        return d

    def guardar_empresa(self, ruc: str, nombre: str, config: dict):
        with self.engine.begin() as cx:
            if cx.execute(select(empresas.c.ruc).where(empresas.c.ruc == ruc)).first():
                cx.execute(update(empresas).where(empresas.c.ruc == ruc).values(
                    nombre=nombre, config=json.dumps(config, ensure_ascii=False), actualizado=_ahora()))
            else:
                cx.execute(insert(empresas).values(ruc=ruc, nombre=nombre, config=json.dumps(config, ensure_ascii=False),
                                                   plan_cargado='', actualizado=_ahora()))

    # ------------------------------------------------------------ plan
    def guardar_plan(self, ruc: str, cuentas: list[dict]):
        with self.engine.begin() as cx:
            cx.execute(delete(plan_cuentas).where(plan_cuentas.c.empresa_ruc == ruc))
            if cuentas:
                cx.execute(insert(plan_cuentas), [{'empresa_ruc': ruc, 'codigo': c['codigo'], 'descripcion': c['descripcion'][:300],
                                                   'tipo': c.get('tipo', '')} for c in cuentas])
            cx.execute(update(empresas).where(empresas.c.ruc == ruc).values(plan_cargado=_ahora()))

    def plan(self, ruc: str) -> list[dict]:
        with self.engine.connect() as cx:
            return [dict(r._mapping) for r in cx.execute(select(plan_cuentas).where(plan_cuentas.c.empresa_ruc == ruc))]

    # ------------------------------------------------------------ mapa de conceptos
    def mapa(self, ruc: str) -> dict[str, dict]:
        with self.engine.connect() as cx:
            return {r.concepto: dict(r._mapping) for r in cx.execute(select(mapa_conceptos).where(mapa_conceptos.c.empresa_ruc == ruc))}

    def guardar_mapa(self, ruc: str, cuentas: dict[str, str], confirmado: bool):
        with self.engine.begin() as cx:
            cx.execute(delete(mapa_conceptos).where(mapa_conceptos.c.empresa_ruc == ruc))
            cx.execute(insert(mapa_conceptos), [{'empresa_ruc': ruc, 'concepto': k, 'cuenta': v or '', 'confirmado': confirmado,
                                                 'confirmado_en': _ahora() if confirmado else ''} for k, v in cuentas.items()])

    def palabras(self, ruc: str) -> dict[str, list[str]]:
        with self.engine.connect() as cx:
            return {r.cuenta: [p.strip().upper() for p in (r.palabras or '').split(',') if p.strip()]
                    for r in cx.execute(select(palabras_cuenta).where(palabras_cuenta.c.empresa_ruc == ruc))}

    def guardar_palabras(self, ruc: str, cuenta: str, palabras: list[str]):
        with self.engine.begin() as cx:
            cx.execute(delete(palabras_cuenta).where((palabras_cuenta.c.empresa_ruc == ruc) & (palabras_cuenta.c.cuenta == cuenta)))
            if palabras:
                cx.execute(insert(palabras_cuenta).values(empresa_ruc=ruc, cuenta=cuenta, palabras=', '.join(palabras)))

    # ------------------------------------------------------------ entidades y exterior
    def guardar_entidades(self, ruc: str, items: list[dict]):
        with self.engine.begin() as cx:
            cx.execute(delete(entidades).where(entidades.c.empresa_ruc == ruc))
            vistos, filas = set(), []
            for e in items:
                if e['codigo'] not in vistos:
                    vistos.add(e['codigo'])
                    filas.append({'empresa_ruc': ruc, 'codigo': e['codigo'][:20], 'nombre': e['nombre'][:200],
                                  'direccion': (e.get('direccion') or '')[:300]})
            if filas:
                cx.execute(insert(entidades), filas)

    def entidades(self, ruc: str) -> list[dict]:
        with self.engine.connect() as cx:
            return [dict(r._mapping) for r in cx.execute(select(entidades).where(entidades.c.empresa_ruc == ruc))]

    def exterior(self, ruc: str) -> list[dict]:
        with self.engine.connect() as cx:
            return [dict(r._mapping) for r in cx.execute(select(clientes_exterior).where(clientes_exterior.c.empresa_ruc == ruc))]

    def guardar_exterior(self, ruc: str, nuevos: list[dict]):
        with self.engine.begin() as cx:
            existentes = {r.codigo for r in cx.execute(select(clientes_exterior.c.codigo).where(clientes_exterior.c.empresa_ruc == ruc))}
            for n in nuevos:
                if n['codigo'] not in existentes:
                    cx.execute(insert(clientes_exterior).values(empresa_ruc=ruc, codigo=n['codigo'], nombre=n['nombre'][:200],
                                                                id_extranjero=n.get('id_extranjero', '')[:40], pais=n.get('pais', '')[:4],
                                                                origen='asignado', creado_en=_ahora()))

    # ------------------------------------------------------------ centros de costos (opcional)
    def guardar_centros(self, ruc: str, items: list[dict]):
        with self.engine.begin() as cx:
            cx.execute(delete(centros_costos).where(centros_costos.c.empresa_ruc == ruc))
            vistos, filas = set(), []
            for i in items:
                if i['codigo'] not in vistos:
                    vistos.add(i['codigo'])
                    filas.append({'empresa_ruc': ruc, 'codigo': i['codigo'][:9], 'descripcion': (i.get('descripcion') or '')[:200],
                                  'es_hoja': bool(i.get('es_hoja', True))})
            if filas:
                cx.execute(insert(centros_costos), filas)

    def centros(self, ruc: str) -> list[dict]:
        with self.engine.connect() as cx:
            return [dict(r._mapping) for r in cx.execute(select(centros_costos).where(centros_costos.c.empresa_ruc == ruc)
                                                         .order_by(centros_costos.c.codigo))]

    # ------------------------------------------------------------ tipo de cambio (global)
    def tipos_cambio(self) -> list[dict]:
        with self.engine.connect() as cx:
            return [dict(r._mapping) for r in cx.execute(select(tipo_cambio).order_by(tipo_cambio.c.fecha))]

    def guardar_tc(self, items: list[dict]):
        with self.engine.begin() as cx:
            for t in items:
                cx.execute(delete(tipo_cambio).where(tipo_cambio.c.fecha == t['fecha']))
                cx.execute(insert(tipo_cambio).values(**t, cargado=_ahora()))

    # ------------------------------------------------------------ reglas y memoria
    def reglas(self, ruc: str) -> list[dict]:
        with self.engine.connect() as cx:
            rs = cx.execute(select(reglas).where(reglas.c.empresa_ruc.in_([ruc, '*']))).all()
        return [{**dict(r._mapping), 'palabras': [p.strip() for p in (r.palabras or '').split(',') if p.strip()]} for r in rs]

    def guardar_regla(self, ruc: str, nombre: str, palabras: list[str], cuenta: str, ruc_cliente: str = ''):
        with self.engine.begin() as cx:
            cx.execute(insert(reglas).values(empresa_ruc=ruc, nombre=nombre, palabras=', '.join(palabras), cuenta=cuenta,
                                             ruc_cliente=ruc_cliente, prioridad=100, activa=True))

    def borrar_regla(self, id_: int):
        with self.engine.begin() as cx:
            cx.execute(delete(reglas).where(reglas.c.id == id_))

    def memoria(self, ruc: str) -> dict[tuple[str, str], str]:
        with self.engine.connect() as cx:
            return {(r.ruc_cliente, r.firma): r.cuenta for r in cx.execute(select(memoria).where(memoria.c.empresa_ruc == ruc))}

    def aprender(self, ruc: str, ruc_cliente: str, firma: str, cuenta: str):
        if not firma or not cuenta:
            return
        with self.engine.begin() as cx:
            cond = (memoria.c.empresa_ruc == ruc) & (memoria.c.ruc_cliente == ruc_cliente) & (memoria.c.firma == firma)
            r = cx.execute(select(memoria).where(cond)).first()
            if r:
                cx.execute(update(memoria).where(cond).values(cuenta=cuenta, veces=(r.veces or 0) + 1, ultima=_ahora()))
            else:
                cx.execute(insert(memoria).values(empresa_ruc=ruc, ruc_cliente=ruc_cliente, firma=firma[:200], cuenta=cuenta,
                                                  veces=1, ultima=_ahora()))

    # ------------------------------------------------------------ lotes y comprobantes exportados
    def guardar_lote(self, ruc: str, periodo: str, usuario: str, resumen: dict, filas: list[dict]) -> int:
        with self.engine.begin() as cx:
            r = cx.execute(insert(lotes).values(empresa_ruc=ruc, periodo=periodo, usuario=usuario, creado_en=_ahora(),
                                                n_xml=resumen.get('n_xml', 0), n_ok=resumen.get('n_ok', 0),
                                                n_excluidos=resumen.get('n_excluidos', 0), n_revisar=resumen.get('n_revisar', 0),
                                                resumen=json.dumps(resumen, ensure_ascii=False, default=str)))
            lote_id = r.inserted_primary_key[0]
            for f in filas:
                cond = ((comprobantes.c.empresa_ruc == ruc) & (comprobantes.c.tipo == f['tipo']) &
                        (comprobantes.c.serie == f['serie']) & (comprobantes.c.numero == f['numero']))
                cx.execute(delete(comprobantes).where(cond))
                cx.execute(insert(comprobantes).values(empresa_ruc=ruc, lote_id=lote_id, **f))
        return lote_id

    def comprobante(self, ruc: str, tipo: str, serie: str, numero: int) -> dict | None:
        with self.engine.connect() as cx:
            r = cx.execute(select(comprobantes).where((comprobantes.c.empresa_ruc == ruc) & (comprobantes.c.tipo == tipo) &
                                                      (comprobantes.c.serie == serie) & (comprobantes.c.numero == numero))).first()
        return dict(r._mapping) if r else None

    def ultimos_numeros(self, ruc: str, antes_de: str) -> dict[tuple[str, str], int]:
        """Último número exportado por (tipo, serie) en periodos anteriores a `antes_de` (AAAA-MM)."""
        with self.engine.connect() as cx:
            rs = cx.execute(select(comprobantes.c.tipo, comprobantes.c.serie, func.max(comprobantes.c.numero))
                            .where((comprobantes.c.empresa_ruc == ruc) & (comprobantes.c.periodo < antes_de))
                            .group_by(comprobantes.c.tipo, comprobantes.c.serie)).all()
        return {(t, s): n for t, s, n in rs}

    def ya_exportados(self, ruc: str, claves: list[tuple[str, str, int]]) -> dict[tuple[str, str, int], str]:
        if not claves:
            return {}
        with self.engine.connect() as cx:
            rs = cx.execute(select(comprobantes.c.tipo, comprobantes.c.serie, comprobantes.c.numero, comprobantes.c.lote_id, lotes.c.creado_en)
                            .join(lotes, lotes.c.id == comprobantes.c.lote_id)
                            .where(comprobantes.c.empresa_ruc == ruc)).all()
        pedidas = set(claves)
        return {(t, s, n): fecha for t, s, n, _, fecha in rs if (t, s, n) in pedidas}

    def lotes(self, ruc: str) -> list[dict]:
        with self.engine.connect() as cx:
            return [dict(r._mapping) for r in cx.execute(select(lotes).where(lotes.c.empresa_ruc == ruc).order_by(lotes.c.id.desc()))]
