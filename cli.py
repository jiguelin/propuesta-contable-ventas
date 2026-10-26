# -*- coding: utf-8 -*-
"""
Uso por línea de comandos (mismo motor que la app):

    python cli.py --ruc 20565449843 --periodo 2026-09 --plan plan.xlsx --tc tipocambio.pdf xmls.zip [--entidades ent.xlsx]

Genera en la carpeta actual el Excel para NewContaSis, el Excel de revisión y el reporte.
Usa una base SQLite temporal propia (no toca la de la app) salvo que se indique --db.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from motor.servicio_ventas import Servicio


def main():
    ap = argparse.ArgumentParser(description='XML de ventas → Excel NewContaSis')
    ap.add_argument('--ruc', required=True)
    ap.add_argument('--nombre', default='EMPRESA')
    ap.add_argument('--periodo', required=True, help='AAAA-MM')
    ap.add_argument('--plan', required=True)
    ap.add_argument('--tc', action='append', required=True, help='PDF de SUNAT (repetir para varios meses)')
    ap.add_argument('--entidades')
    ap.add_argument('--actividad', default='', help='comercial | servicios | industrial | mixta')
    ap.add_argument('--rubro', default='')
    ap.add_argument('--db', default='')
    ap.add_argument('--confirmar-anulados', action='store_true', help='exportar los huecos propuestos como anulados')
    ap.add_argument('xml', nargs='+')
    a = ap.parse_args()

    db = a.db or f'sqlite:///{Path(tempfile.mkdtemp()) / "cli.db"}'
    s = Servicio(db_url=db)
    s.crear_empresa(a.ruc, a.nombre, a.rubro, a.actividad)
    s.cargar_plan(a.ruc, Path(a.plan).read_bytes())
    mapa, _ = s.mapa(a.ruc)
    s.guardar_mapa(a.ruc, {k: d.cuenta for k, d in mapa.items()})
    for t in a.tc:
        s.cargar_tc(t, Path(t).read_bytes())
    if a.entidades:
        s.cargar_entidades(a.ruc, Path(a.entidades).read_bytes())
    lote, ctx = s.procesar(a.ruc, a.periodo, [(Path(x).name, Path(x).read_bytes()) for x in a.xml], usar_ia=False)
    if a.confirmar_anulados:
        for f in lote.por_estado('hueco_anulado'):
            f.confirmado = True
    for nombre, data in s.archivos_salida(lote, ctx).items():
        Path(nombre).write_bytes(data)
        print('→', nombre)
    for b in lote.bloqueos:
        print('BLOQUEO:', b)
    print(f'{len(lote.exportables)} filas para importar · {len(lote.por_estado("revisar"))} por revisar')


if __name__ == '__main__':
    main()
