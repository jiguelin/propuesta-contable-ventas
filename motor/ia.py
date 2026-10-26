# -*- coding: utf-8 -*-
"""
motor/ia.py — Consulta a Claude (Anthropic) SOLO para comprobantes dudosos (confianza < 90 %).

Reglas:
  * la IA elige exclusivamente entre cuentas reales del plan de la empresa (familias 70, 75 y 122);
    si responde una cuenta fuera de la lista, se descarta;
  * no se envían DNI, RUC ni nombres de clientes: solo el rubro de la empresa y los ítems;
  * la app funciona igual sin clave (ANTHROPIC_API_KEY en secrets o variable de entorno).
"""
from __future__ import annotations

import json
import os
import urllib.request

MODELO_DEFECTO = os.environ.get('VENTAS_MODELO_IA', 'claude-haiku-5-5')


def _llamar(prompt: str, api_key: str, modelo: str, max_tokens: int = 300) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=modelo, max_tokens=max_tokens, messages=[{'role': 'user', 'content': prompt}])
        return msg.content[0].text
    except ImportError:
        body = json.dumps({'model': modelo, 'max_tokens': max_tokens,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode()
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body, method='POST',
                                     headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01',
                                              'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        return ''.join(b.get('text', '') for b in data.get('content', []))


def crear_ia(api_key: str | None = None, modelo: str = MODELO_DEFECTO, max_candidatas: int = 25):
    """Devuelve ia(fila, ctx) -> (cuenta, confianza, razón) | None, o None si no hay clave."""
    api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return None
    cache: dict[tuple, tuple | None] = {}

    def ia(f, ctx):
        c = f.c
        cands = [cu for pref in ('70', '75', '122') for cu in ctx.plan.hojas_bajo(pref)][:max_candidatas]
        if not cands:
            return None
        items = '\n'.join(f'- {l.cantidad} {l.unidad} {l.descripcion[:90]} → {c.simbolo} {l.valor_venta}' for l in c.lineas[:25])
        llave = (ctx.cfg.ruc, items, f.es_exportacion)
        if llave in cache:
            return cache[llave]
        cuentas_txt = '\n'.join(f'{cu.codigo} | {cu.descripcion[:80]}' for cu in cands)
        prompt = f"""Eres contador peruano experto en el PCGE y en el registro de ventas.
Elige UNA cuenta para el ingreso de este comprobante de VENTA, solo entre las cuentas listadas
(son las del plan de la empresa). Está prohibido responder una cuenta que no esté en la lista.

Actividad de la empresa: {ctx.cfg.actividad}. Rubro: {ctx.cfg.rubro or ctx.plan.giro or '(no indicado)'}.
Comprobante: {c.tipo_nombre}. {'Es EXPORTACIÓN.' if f.es_exportacion else 'Venta local.'}
Ítems:
{items or '(sin detalle)'}

Propuesta por reglas: {f.cuenta_base or '(ninguna)'} — {f.explicacion}

Cuentas (código | descripción):
{cuentas_txt}

Responde SOLO un JSON: {{"cuenta": "<código exacto de la lista>", "confianza": <0-100>, "razon": "<una línea>"}}"""
        raw = _llamar(prompt, api_key, modelo)
        s, e = raw.find('{'), raw.rfind('}') + 1
        data = json.loads(raw[s:e])
        cuenta = str(data.get('cuenta', '')).strip()
        res = None
        if cuenta in {cu.codigo for cu in cands}:
            res = (cuenta, min(int(data.get('confianza', 80)), 94), str(data.get('razon', ''))[:160])
        cache[llave] = res
        return res

    return ia
