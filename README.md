# Propuesta Contable de Ventas

Sube los **XML de ventas del mes** de una empresa → obtienes el **Excel listo para importar en NewContaSis**
(Utilitarios → Asistentes → Importación Registro de Ventas, 44 columnas A–AR), con la cuenta de ventas,
la cuenta del total, el tipo de cambio, los anulados y los anticipos ya resueltos.

App independiente de la de compras (otro repositorio, otra base de datos, otro personal).

```
XML / ZIP ─▶ lector UBL (corrige codificación) ─▶ ¿es de la empresa y del mes? ─▶ clasificación
         ─▶ cliente (03 sin DNI · código de anulado · clientes del exterior) ─▶ contado/crédito
         ─▶ concepto (mercadería / servicio / exportación / anticipo) ─▶ cuenta del plan de la empresa
         ─▶ huecos de correlativos → anulados · anticipos → Debe 1221 / Haber 70
         ─▶ Excel NewContaSis + Excel de revisión + reporte
```

## Cómo se usa

1. **Empresa** (barra lateral): RUC, razón social y, opcional, el rubro en texto libre ("compra y revende joyas").
2. **② Plan y conceptos** (una vez por empresa): subir el plan de cuentas exportado de NewContaSis, revisar
   el mapa de conceptos y **confirmarlo**. Si una cuenta tiene subcuentas (70321 → 7032101 asesoría,
   7032102 diseño web, 7032103 capacitaciones) la app elige la subcuenta por las palabras de los ítems.
3. **① Empresa → Entidades** (opcional): subir el Excel "CLIENTES - PROVEEDORES (TODOS)". De ahí salen el
   código de ANULADO (ej. `0000`) y el de BOLETA sin documento (`03`), y se reconocen los clientes del exterior.
4. **③ Tipo de cambio** (una vez al mes, sirve para todas las empresas): subir el PDF de SUNAT.
5. **④ Ventas del mes**: elegir el mes, subir los XML (sueltos o ZIP, también comunicaciones de baja y
   resúmenes diarios) → **GENERAR PROPUESTA** → revisar, corregir y **descargar**.

## Reglas principales

| Tema | Regla |
|---|---|
| Periodo | Solo entra lo emitido en el mes elegido; lo demás va a Excluidos. |
| Empresa | Si el RUC emisor no es la empresa, se excluye (es una compra u otra empresa). |
| Contado / crédito | De la forma de pago del XML. Cuenta total: contado soles **1011**, contado dólares **1012**, crédito **1212** (editable fila por fila y en bloque). |
| Ventas al contado | Como AD ya es la caja, al importar marque **"Generar solo – Asientos de Venta"** en NewContaSis. |
| Boleta sin DNI | F = `0`, G = código de boleta de la empresa (`03`). Nunca `-`. Aviso si pasa de S/ 700. |
| Anulados | Huecos en la numeración → se proponen como anulados con el código de la empresa (`0000` u `00000000000`); se exportan solo los confirmados. Si el número es un anticipo referenciado en otro XML, es "XML faltante", no anulado. |
| Anticipos | El anticipo va a **1221**. La factura/boleta final se importa con su base neta y la app lista el asiento complementario **Debe 1221 / Haber 70** por cada anticipo aplicado. |
| Exportación | Cliente del exterior ya creado → su código; nuevo → 10000000001, 10000000002… (nunca se repite). |
| Detracción / percepción | Solo informativas en ventas: no se registran (AE–AJ, AO, AP vacíos). |
| Tipo de cambio | En todas las filas (soles y dólares), el del día de emisión o el inmediato anterior. Dólares: I–P en soles y W = total en US$. |
| Cuentas | Siempre del plan de la empresa. Orden: regla de la empresa → memoria de correcciones → señales del XML → actividad/rubro → IA (opcional, solo entre cuentas reales). |

## Publicar en Streamlit Community Cloud

1. En https://share.streamlit.io → **Create app** → repositorio `jiguelin/propuesta-contable-ventas`, rama `main`, archivo `app.py`.
2. En **Advanced settings → Secrets** pegar (ver `.streamlit/secrets.toml.example`):
   ```toml
   APP_PASSWORD = "una-clave"            # muy recomendado: la dirección es pública
   DATABASE_URL = "postgresql+psycopg2://usuario:clave@host:5432/postgres"   # Supabase o Neon
   ANTHROPIC_API_KEY = "sk-ant-..."      # opcional
   ```
3. Cada cambio que se sube al repositorio se publica solo.

> Sin `DATABASE_URL` la app usa una base SQLite temporal: en Streamlit Cloud **se borra al reiniciar**
> (planes, mapas, memoria y códigos de clientes del exterior). Para trabajar en serio, configure Postgres
> (Supabase o Neon tienen plan gratuito).

## Línea de comandos

```bash
pip install -r requirements.txt
python cli.py --ruc 20000000101 --periodo 2026-09 --actividad comercial \
  --plan tests/datos/plan_prueba.xlsx --tc tests/datos/tipocambio_2026-09.pdf \
  --entidades tests/datos/entidades_prueba.xlsx --confirmar-anulados tests/datos/joyeria_set/*.xml
```

## Pruebas

```bash
python -m pytest -q
```

Los XML de `tests/datos/` son copias **anonimizadas** de comprobantes reales (clientes, emisores y cuentas
bancarias reemplazados por datos ficticios). **Este repositorio es público: no suba XML, planes ni entidades
reales de clientes.** Use la carpeta `privado/` (ignorada por git) para pruebas locales con datos reales.

## Estructura

```
app.py                       ← interfaz Streamlit (solo dibuja)
cli.py                       ← mismo motor por línea de comandos
motor/
  xml_parser.py              ← lector UBL 2.1 (copia de compras + codificación, anticipos, exportación)
  lector.py                  ← XML/ZIP, comunicaciones de baja (RA) y resúmenes diarios (RC)
  texto.py                   ← limpieza de nombres y glosas (copia de compras)
  tipo_cambio.py             ← PDF SUNAT (copia de compras + compra/venta)
  plan_cuentas.py            ← plan de NewContaSis, cuentas hoja por prefijo, giro
  entidades.py               ← Excel de entidades, códigos ANULADO/BOLETA, clientes del exterior
  conceptos.py               ← conceptos contables, mapa por empresa, subcuentas por palabras
  clasificador_ventas.py     ← cliente, condición, anticipo, exportación, NC/ND, cuentas, glosa
  correlativos.py            ← huecos en la numeración
  proceso.py                 ← orquesta un lote (sin base de datos) y aplica ediciones
  edicion.py                 ← cambios hechos en la tabla de la pantalla
  excel_ventas.py            ← Excel A–AR, Excel de revisión y reporte
  db.py                      ← base propia (Postgres o SQLite)
  servicio_ventas.py         ← une base de datos y motor
  ia.py                      ← Claude, opcional, solo entre cuentas del plan
tests/                       ← pytest con XML anonimizados y sintéticos
```

## A confirmar en la primera importación real en NewContaSis

- Importes en dólares convertidos a soles (I–P) con W = total en US$ (igual que en compras). Si NewContaSis
  los espera en dólares, desmarque la opción en ① Empresa.
- Notas de crédito con importes negativos (opción en ① Empresa).
- Fila de anulado con importes en cero y la entidad de anulado.
- ICBPER solo en AQ (no se suma en O).
