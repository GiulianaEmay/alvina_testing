# Validación — `19.0.2.2.0`, 13 de octubre de 2026

## Resultados realmente ejecutados

| Comprobación | Resultado |
| --- | --- |
| Instalación desde base vacía (`alvina_code128_clean`) | 38 métodos, **0 fallos / 0 errores** en la primera revisión |
| Suite final sobre instalación nueva (`alvina_code128_env`) | 42 métodos, **0 fallos / 0 errores** |
| Actualización real desde `19.0.2.1.1` (`alvina_code128_upgrade`) | 42 métodos, **0 fallos / 0 errores** |
| Dos autorizaciones concurrentes, QR contra Code128 | **PASS**: dos conexiones y dos almaceneros; una entrega, un rechazo, un picking, origen 15 y destino 5; ambos códigos invalidados |
| Anulación contra entrega Code128, anulación primero | **PASS**: un reintento de serialización y rechazo de entrega; cero pickings, origen 20, destino 0; ambos códigos invalidados |
| Anulación contra entrega Code128, entrega primero | **PASS**: un reintento de serialización y rechazo de anulación; un picking, origen 15, destino 5; ambos códigos invalidados |
| Actualización de comprobantes pendientes | **PASS**: dos QR previos conservados, Code128 generado por post-migrate antes de reabrir, incluida empresa archivada; sin cambios de stock ni auditoría |
| PNG generados por el addon | **PASS**: QR y Code128 reales de ambos comprobantes actualizados decodificados con ZXing C++; mismo texto que sus credenciales |
| Instalación y arranque reproducibles del entorno local | **PASS**: instalación/refresco repetidos y HTTP con formulario de login y CSRF en base desechable |
| Decoder local en Chromium | **14 casos PASS**: QR nuevo/anterior, PNG/JPEG/WebP, imágenes inválidas y carreras de cámara |
| Interfaz Odoo/Owl real en Chromium | **9 verificaciones PASS**, 0 errores Runtime; campo, diálogo, revisión y comprobante 900 × 180 |
| Python, XML de vistas y sintaxis JS; `git diff --check` | Correctos |

El runner Odoo incluye setup/teardown en sus estadísticas (`44 tests` para la
suite final); el resultado funcional es **42 métodos** del addon. Las pruebas
no cuentan módulos dependientes ni ensayos físicos como casos del addon.

## Cobertura del servidor

Los casos verifican emisión inmediata de ambos códigos, Code128 ASCII de
40 dígitos con ceros iniciales, digest SHA-256 y unicidad real en PostgreSQL,
reapertura sin rotación, compatibilidad del QR anterior, backfill idempotente,
rechazo de credenciales incompletas/corruptas y referencias secuenciales.

Ambos formatos revisan la misma solicitud sin reserva ni transferencia. La
entrega verifica nuevamente permiso, empresa, código, estado, ruta, cantidades
y reserva completa bajo bloqueo. Entregar con uno invalida ambos, incluso en
asistentes previamente revisados. Cambiar el código exige nueva revisión.
Stock insuficiente o fallos posteriores a mover stock revierten el intento
y preservan ambas credenciales. Se cubren lotes/series, varios productos,
productos/cantidades inválidos y operaciones ordinarias ajenas al addon.

La suite cubre solicitantes, usuarios de Inventario sin rol de almacenero,
usuario público, propietarios ajenos, empresas no autorizadas, campos privados,
asistentes por propietario/empresa y manipulación por RPC/contexto. La anulación
propia exige motivo, conserva historial/líneas y stock, invalida ambos códigos
y comparte bloqueo con la entrega. El rollback incluye auditoría/chatter.

Una prueba resuelve los assets efectivos de `web.assets_backend` y las vistas
que Odoo entrega al usuario: campo lector, comprobante QR/Code128, productos,
existencias y estado. La prueba SQL fuerza un digest duplicado y exige
`UniqueViolation`; no se limita a comprobar una declaración Python.

Los probes de concurrencia confirman revisión en una transacción separada y
compiten desde conexiones PostgreSQL independientes con el aislamiento real
de Odoo. La transacción perdedora reintenta serialización y después rechaza la
credencial o transición consumida. Realizan commits de fixtures: **solo bases
desechables**. La aplicación no realiza commits manuales.

## Actualización de QR existentes

Se archivó el addon anterior del commit
`547b1698ad811da3be4fcd10fa39ad71c3ae3244` (`19.0.2.1.1`) y se instaló en
`alvina_code128_upgrade`. `tests/upgrade_probe.py` creó dos solicitudes pendientes
con QR y transitorios anteriores, dos borradores, dos anuladas y una entregada.
Una empresa se archivó después de generar su QR.

La actualización ejecutó `migrations/19.0.2.2.0/post-migrate.py`. El probe comprobó
las columnas persistidas **antes de abrir el comprobante**: QR/UUID/hash originales
intactos, Code128 nuevo, mismos estados, líneas, rutas, registros, mensajes,
auditoría, pickings, movimientos, quants y reservas. Borradores, anuladas y
entregadas no recibieron credenciales nuevas. El baseline contiene únicamente
secretos de fixtures y se guarda con permisos 0600 fuera del repositorio.

Reabrir muestra ambos códigos; revisar cualquiera no cambia inventario. Odoo
requiere una empresa activa para operar normalmente: el probe reactiva la
empresa archivada temporalmente para ese tramo y revierte el cambio al terminar.
El backfill sí se comprobó mientras la empresa estaba archivada.

La primera actualización detectó una advertencia NOT NULL en el nuevo campo
visual del transitorio antiguo. Se retiró `required=True` de ese campo: `create()`
continúa generándolo siempre en comprobantes nuevos y bloquea asignación externa.
No se modifica el QR de los comprobantes temporales anteriores. La ejecución
final volvió a pasar 42 métodos sin fallos, errores ni advertencias. Tras repetir
`-u` y ejecutar nuevamente la función de migración, los códigos permanecieron
estables. Se confirmó la restricción SQL y se revisaron ocho logs sin hallar los
secretos, UUID o digests de esos comprobantes pendientes.

## Navegador

El runner persistente `tests/browser_reader.mjs` carga el bundle local real en
Chromium y genera imágenes con qrcode/ReportLab. Comprueba QR actuales y previos,
Code128 PNG/JPEG/WebP, imagen ilegible, MIME/extensión/firma/tamaño, permisos
denegados, selección de cámara y liberación de recursos en carreras de inicio,
cierre y resultado. Todos los requests al servidor de prueba son GET; las
imágenes se decodifican localmente. Las cámaras de estas pruebas son simuladas.

La detección inicial falló con un QR generado nítido. Se añadió una segunda
lectura QR con el hint PURE_BARCODE después de fallar la detección normal;
se comprobó la corrección con imágenes reales. Se mantuvo la distribución
verificada `@zxing/browser@0.1.5`.

También se arrancó un servidor local separado (8082, `alvina_code128_clean`)
y se automatizó Chromium contra Odoo 19 con Owl, CharField y assets reales.
Se verificó entrada manual/HID, campo actualizado por lectura sin revisión o
entrega automática, apertura/cierre del diálogo, revisión explícita, productos,
estado solicitado sin transferencia y comprobante con imagen natural 900 × 180
mostrada a 900 px con desplazamiento horizontal. Un cambio nativo del selector
de archivos decodifica el PNG real del addon, rellena el campo y deja la revisión
pendiente, sin RPC automático de revisión ni entrega. Los runners persistentes
son `tests/browser_reader.mjs` y `tests/browser_odoo_smoke.mjs`; el segundo utiliza
`tests/browser_fixture.py` y acepta exclusivamente servidor/base locales de prueba.
Logs: `/tmp/alvina-browser-reader-final.log` y `/tmp/alvina-ui-smoke-final.log`. No se reinició el servidor
preexistente ni el servidor dedicado del usuario.

## Entorno y comandos

- Python **3.12.14**, Odoo 19 Community core
  `74c927424f7da4b9ec9713e10749de93ec0dcf46`.
- PostgreSQL 16 local, imagen verificada
  `postgres@sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d`.
- Requisitos oficiales de ese commit; desarrollo usa `psycopg2-binary==2.9.9`
  y omite LDAP opcional. `qrcode==7.4.2`, `reportlab==4.1.0`,
  backend PNG `rl-renderPM==4.0.3`.
- Decoder del cliente: `@zxing/browser@0.1.5`, bundle local sin CDN, integridad
  npm y SHA-256 verificados; licencias y procedencia en `static/lib/zxing_browser`.
- ZXing C++ 2.3.0 y NumPy 2.2.6 se usaron solo para pruebas de PNG; no son
  dependencias del addon. Node 24.19.0 y Chromium para pruebas de navegador.

Suite final sobre instalación nueva:

```bash
/tmp/alvina-venv/bin/python /tmp/alvina-odoo-runtime/odoo-bin \
  --addons-path=/tmp/alvina-odoo-runtime/addons,/workspace/alvina-odoo19 \
  --data-dir=/tmp/alvina-odoo-data \
  --db_host=127.0.0.1 --db_port=5432 --db_user=odoo \
  -d alvina_code128_env -u alvina_stock_request --without-demo=True \
  --test-enable --test-tags=/alvina_stock_request --stop-after-init \
  --http-interface=127.0.0.1 --http-port=8077 --max-cron-threads=0 \
  --logfile=/tmp/alvina-code128-final-tests-retry.log
```

La instalación inicial usó `-i`, base `alvina_code128_clean`, puerto 8075 y log
`/tmp/alvina-code128-tests.log`. El upgrade con suite usó `-u`, base
`alvina_code128_upgrade`, puerto 8076 y log `/tmp/alvina-code128-upgrade-suite.log`. La repetición final tras corregir el
transitorio usó `/tmp/alvina-code128-upgrade-final.log` y su comprobación
`/tmp/alvina-code128-upgrade-final-probe.log`.

Los probes se ejecutaron secuencialmente en `alvina_code128_clean`:

```bash
/tmp/alvina-venv/bin/python /tmp/alvina-odoo-runtime/odoo-bin shell \
  --addons-path=/tmp/alvina-odoo-runtime/addons,/workspace/alvina-odoo19 \
  --data-dir=/tmp/alvina-odoo-data \
  --db_host=127.0.0.1 --db_port=5432 --db_user=odoo \
  -d alvina_code128_clean --no-http --http-port=8075 \
  --logfile=/tmp/alvina-code128-double-delivery.log \
  < alvina_stock_request/tests/concurrency_probe.py
```

Para la carrera de anulación, se sustituyó la entrada por
`tests/cancellation_concurrency_probe.py` y el log por
`/tmp/alvina-code128-cancel-concurrency.log`.

Un primer arranque de pruebas chocó con el puerto 8069 del desarrollo existente;
se usó un puerto distinto. Otra ejecución se inició mientras se instalaba su
misma base y terminó por conflicto de serialización antes de los casos; se
repitió después de finalizar la instalación y pasó los 42 casos. No se
redujeron aserciones ni se modificó el servidor existente para resolverlo.
El decoder se ejecutó con:

```bash
ODOO_PYTHON=/tmp/alvina-venv/bin/python CHROMIUM=/usr/bin/chromium \
  node alvina_stock_request/tests/browser_reader.mjs
```

Para reproducir la migración, instalar el addon previo en una base desechable,
ejecutar `tests/upgrade_probe.py` por Odoo shell con
`ALVINA_UPGRADE_PROBE_PHASE=seed`, actualizar el addon y ejecutar fase `check`.
Repetir la actualización y ejecutar fase `idempotent`. Para las comprobaciones
PNG se añadió `PYTHONPATH=/tmp/alvina-code128-decoder` (ZXing C++ y NumPy).
No copiar su baseline, que contiene secretos de fixtures, al repositorio.

Las repeticiones del harness de interfaz encontraron interferencias del tour de
primer login y de la navegación inicial a Discuss. El fixture desactiva el tour
solo en la base desechable y el runner espera el controlador inicial antes de
abrir el asistente; la repetición final pasó las nueve verificaciones sin errores
Runtime. Las aserciones del flujo permanecieron activas.

Los logs temporales son evidencia de esta instancia, no archivos del addon.

## Límites de la validación

- No se dispone de código licenciado Enterprise, `stock_barcode` ni fuentes de
  `alvinha_stock`/`alvinha_base` del servidor. Se validó el núcleo stock de Odoo 19;
  la combinación con esas extensiones requiere ensayo en una copia del servidor.
- Las cámaras se simulan en las pruebas automatizadas. Quedan pendientes los
  lectores 1D y cámaras físicos, navegadores móviles reales, permisos del
  dispositivo y capturas comprimidas por WhatsApp del equipo del almacén.
- Se prueban carreras de dos sesiones, no carga sostenida de cientos de usuarios.
- No se ejecutaron suites ajenas al addon, correo externo, LDAP ni impresión PDF.
- El flujo anterior `19.0.1.*` requiere migración separada. Se comprobó la
  actualización real de `19.0.2.1.1`, no datos del servidor del usuario.
- No se modificaron otros módulos, fusionaron ramas, desplegó a producción ni
  reinició el servidor dedicado. Ver [UPGRADE.md](UPGRADE.md) para el procedimiento
  de actualización/reinicio del servicio Odoo y recuperación.
- La configuración del entorno cloud se guarda como borrador; su publicación
  y restauración en otra tarea no se consideran ejecutadas.
