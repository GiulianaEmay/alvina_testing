# alvina_stock_request — Odoo 19

Solicitudes internas de insumos por departamento. Compatible por diseño con Odoo
19 Enterprise; depende de `stock`, `mail`, `qrcode` y ReportLab para generar los
comprobantes QR y Code128.
No requiere `hr`, no crea almacenes ni tipos de operación, y no cambia la
configuración del tipo de traslado interno existente.

La aplicación aparece como **Requerimientos**, con un icono propio de cajas
moradas sobre un pallet incluido en el módulo. El nombre y el icono se instalan
sin utilizar Odoo Studio.

## Instalación y configuración

1. Añadir el directorio padre de este módulo a `addons_path`; instalar
   `alvina_stock_request` en una base de pruebas Odoo 19. `qrcode` y ReportLab
   forman parte de los requisitos estándar de Odoo. Para generar el PNG Code128,
   ReportLab necesita un backend `renderPM` funcional; puede proporcionarlo
   `rl-renderPM==4.0.3` o un backend compatible de los paquetes oficiales de la
   distribución, como `rlPyCairo` con sus dependencias Cairo. Comprobar la
   generación del comprobante en la base de pruebas antes de actualizar el
   servidor. Ver [UPGRADE.md](UPGRADE.md).
2. En Inventario, comprobar que existe el almacén **Insumos**, código **INS**, y
   que su ubicación principal (`lot_stock_id`) es **INS/Existencias**, interna y
   de la misma empresa. El módulo valida el código INS, el nombre Existencias y
   que esta ubicación pertenece directamente a la vista del almacén. No busca
   ubicaciones globales por nombre ni las crea/renombra automáticamente.
3. El tipo de operación de traslados internos de Insumos debe estar activo.
   Seleccionarlo en **Requerimientos → Departamentos y rutas**. El selector
   admite exclusivamente tipos internos existentes de ese almacén y empresa.
   No se modifican sus ubicaciones predeterminadas, secuencias ni política de
   backorders. La ruta se establece en cada traslado generado.
4. Crear un departamento receptor y seleccionar su ubicación interna de
   destino, distinta de Existencias y perteneciente a la misma empresa.
5. Asignar explícitamente los roles según esta tabla. Tener permisos estándar
   de Inventario o configurar departamentos no autoriza a entregar solicitudes.

| Rol | Facultades |
| --- | --- |
| Solicitante | Crear/editar borradores propios, enviar, consultar sus documentos y su comprobante QR/Code128, y anular solicitudes propias en estado Borrador o Solicitada antes de la entrega |
| Almacenero autorizado | Consultar solicitudes de empresas habilitadas, leer el comprobante, revisar productos y existencias, y autorizar la entrega |
| Configuración de departamentos | Configurar rutas; implica administrador de Inventario, pero no implica almacenero autorizado |

El almacenero implica usuario estándar de Inventario. Asignar este rol únicamente
a personas que deban tener esa autoridad. ACL y reglas globales de empresa se
combinan con reglas de propietario; el rol de almacenero amplía la visibilidad
solo dentro de las empresas activas autorizadas del usuario.

### Actualización del módulo

La versión `19.0.2.2.0` añade un comprobante con QR y Code128, lectura con cámara,
imagen, lector físico o entrada manual. La aplicación sigue llamándose
**Requerimientos**, con el icono de cajas moradas sobre un pallet. Se conservan
la anulación por el creador, los nombres técnicos de campos y el estado
`cancelled`, visible como **Anulada**.

Los QR válidos de solicitudes pendientes de `19.0.2.1.1` se conservan. La
actualización añade Code128 a solicitudes **Solicitadas** con QR vigente mediante una
migración idempotente; la reapertura del comprobante también puede completar
ese dato bajo bloqueo si todavía falta. No se vuelven a enviar solicitudes ni
se reservan existencias durante este proceso. El primer flujo del PR
(`19.0.1.*`, anterior al QR inmediato) requiere una migración separada y no
está cubierto por esta actualización.

Para actualizar desde `19.0.2.0.0`, `19.0.2.0.1`, `19.0.2.1.0` o `19.0.2.1.1`,
seguir [UPGRADE.md](UPGRADE.md): preparar y probar el paquete, detener todos los
procesos de Odoo que utilizan la base, colocar el nuevo código, actualizar
únicamente `alvina_stock_request` y arrancar todos los procesos con esa versión.
**Actualizar** en Aplicaciones no descarga código desde GitHub.

## Operación

- Las etiquetas del módulo están en español. En el borrador se completan
  **Empresa**, **Área solicitante**, **Motivo** y las líneas de **Producto** y
  **Cantidad**. El **Solicitante** es el usuario que crea el registro y la
  **Unidad de medida** procede del producto; no se introducen manualmente.
  La **Ruta de entrega** permanece vacía hasta **Enviar solicitud**, cuando se
  asignan automáticamente el tipo de traslado, origen y destino del área.
  La transferencia y los datos de entrega se registran al autorizar el almacenero.
- El solicitante crea un borrador, selecciona departamento y productos
  almacenables activos y solicita cantidades positivas en la unidad del producto.
  Una línea por producto. No se admiten servicios ni cantidades no finitas.
- **Enviar solicitud** congela líneas y ruta, registra fecha y solicitante y
  genera inmediatamente un comprobante de retiro. El solicitante puede verlo
  entonces y volver a abrirlo mientras la solicitud siga **Solicitada**. El
  asistente muestra referencia, área solicitante, productos y cantidades, QR,
  Code128 y el número de retiro legible. La imagen Code128 mide 900 × 180 píxeles.
  No requiere preparación previa del almacenero ni reserva de stock.
- El solicitante presenta el comprobante en almacén. QR y Code128 no vencen ni
  se renuevan: permanecen válidos hasta una entrega exitosa o la anulación
  autorizada por su creador. La referencia de la solicitud identifica el
  documento; **no es un código que autorice el retiro**.
- Un almacenero inicia sesión y abre el asistente de autorización. Introduce
  el contenido QR o el número Code128 mediante cualquiera de los métodos
  descritos abajo y solicita la revisión. Se muestran la solicitud, sus
  productos, cantidades y existencias disponibles en **INS/Existencias**.
  Leer el código y revisarlo no reserva ni entrega productos. La disponibilidad
  mostrada puede cambiar y se comprueba de nuevo al autorizar.
- Tras revisar, el almacenero pulsa **Autorizar y entregar**. Se crea un único
  `stock.picking` interno con un `stock.move` por línea, reutilizando el tipo de
  traslado interno de Insumos sin modificarlo. Odoo reserva y valida el traslado
  desde **INS/Existencias** a la ubicación del departamento en una sola operación,
  incluidos lotes/series ya existentes en los quants.
- Si falta stock o falla la validación, no hay entrega parcial ni backorder:
  se revierte el intento completo, sin conservar traslado ni reservas. La
  solicitud sigue **Solicitada** y ambos códigos pueden utilizarse tras reponer
  stock, siempre que su creador no la haya anulado.
- Solo una entrega exitosa cambia la solicitud a **Entregada**, registra
  almacenero/fecha e invalida QR y Code128 en la misma transacción. Un código
  usado, anulado, modificado o de otra empresa no autoriza la entrega.
- No hay endpoint público ni URL que autorice stock al abrirse. La cámara del
  teléfono por sí sola no valida el traslado. El comprobante no sustituye la
  comprobación presencial de quién recibe los insumos.
- **Anular solicitud** está permitido exclusivamente al usuario que la creó,
  mientras esté en **Borrador** o **Solicitada** y aún no se haya entregado. Debe
  introducir un **Motivo de anulación**; Odoo registra autor, fecha y motivo en
  la solicitud y su historial, cambia el estado a **Anulada** e invalida QR y
  Code128 inmediatamente. Anular conserva el registro y todas sus líneas; no
  elimina la solicitud ni mueve existencias. Un almacenero no puede anular solicitudes
  ajenas por tener el rol de almacén.
- Una solicitud **Entregada** no se puede anular. Las devoluciones físicas
  siguen el procedimiento de Inventario de la organización y no reactivan los
  códigos ni la solicitud.

### Formas de leer el comprobante

| Método | Uso |
| --- | --- |
| Lector físico | Seleccionar el campo de código y escanear QR o Code128 con un lector compatible que funcione como teclado USB/Bluetooth HID. |
| Cámara | Abrir el lector de cámara, permitir el acceso y seleccionar la cámara disponible para enfocar el QR o Code128. |
| Subir imagen | Elegir una imagen PNG, JPEG o WebP de hasta 5 MiB que contenga un código legible. |
| Entrada manual | Pegar el contenido QR o escribir los 40 dígitos del número Code128 exactamente como aparecen en el comprobante. |

Para Code128, habilitar esa simbología en el lector físico y desactivar cualquier
conversión numérica que quite ceros iniciales. Configurar un sufijo ENTER según
el manual del lector y comprobar el resultado en la base de pruebas. El código
es texto: no sustituirlo por la referencia de la solicitud ni abreviarlo.
La lectura solo rellena el campo; no autoriza la entrega automáticamente. El
almacenero debe revisar y pulsar **Autorizar y entregar**.

La cámara requiere un navegador compatible con acceso a cámara, contexto seguro
HTTPS y permiso del usuario. En móvil, revisar los permisos del navegador y
del sistema; en una aplicación que encapsula la página, revisar también los
permisos y la política de cámara de ese contenedor. No desactivar la seguridad
del navegador para habilitarla. Si hay varias cámaras, seleccionar la trasera
cuando esté disponible. Al detener la lectura o cerrar el asistente, se liberan
las cámaras. Si el dispositivo no admite esta función o deniega el permiso,
utilizar el lector físico, una imagen o la entrada manual.

La imagen se decodifica dentro del navegador; sus bytes no se suben a Odoo ni a
servicios externos y no se guardan como adjuntos. Solo se envía a Odoo el texto
decodificado para su revisión. La biblioteca de lectura ZXing se incluye en los
assets del módulo: no requiere CDN ni conexión a un servicio de lectura externo.
Consultar los archivos de licencia y atribución incluidos con esa biblioteca.
Una imagen borrosa, recortada o sin suficiente contraste puede no reconocerse;
presentar el código completo o usar la entrada manual.

## Seguridad, concurrencia y trazabilidad

Las restricciones se aplican en el servidor, también por RPC: los campos de
estado, propietario, vínculo y auditoría no se escriben directamente. Se filtran
los valores `default_*` del contexto que podrían falsificar datos de control.
Las líneas bloquean la solicitud padre antes de editarla. Las solicitudes en estado
**Solicitada** o **Anulada** no se eliminan, y las rutas departamentales con
solicitudes activas no se cambian.

Cada transición bloquea la fila de solicitud con `SELECT ... FOR UPDATE`, invalida
la caché y vuelve a comprobar permisos/estado. PostgreSQL y las restricciones
únicas aseguran un traslado por solicitud. Dos transacciones sobre la misma fila
se serializan; con el aislamiento REPEATABLE READ de Odoo, la perdedora puede
recibir un conflicto de serialización que el despachador RPC reintenta. Tras el
reintento los códigos ya están invalidados y el segundo retiro se rechaza, incluso
si el primer intento usó QR y el segundo Code128. Anular y entregar
utilizan el mismo bloqueo de fila: si compiten, solo una transición puede
completarse. Si gana la anulación, se rechaza la entrega incluso si el código ya
había sido leído; si gana la entrega, se rechaza la anulación. No se realizan
commits manuales en el flujo de aplicación. La entrega tiene además un savepoint:
un fallo incluso después de mover stock revierte creación del traslado,
reservas, entrega, auditoría y consumo de ambos códigos. Leer o revisar el
comprobante no concede una autorización permanente: los permisos, la empresa,
el código, el estado y las existencias se comprueban nuevamente bajo el bloqueo
dentro de la operación de entrega. Cámara, imagen, lector y entrada manual
utilizan el mismo control del servidor.

La anulación también utiliza un savepoint: si falla el registro del mensaje en
el historial u otra parte de la operación, se revierten el estado, los datos de
auditoría y la invalidación de ambos códigos. La solicitud conserva su estado
previo y, si estaba **Solicitada**, el mismo QR y Code128 siguen siendo válidos.

Los traslados, movimientos y operaciones detalladas vinculados se protegen contra
validación, cancelación, cambios y eliminación directos. El flujo privado utiliza
un marcador por identidad Python, no una bandera que pueda falsificarse en un
contexto JSON-RPC. Los traslados ajenos a este módulo siguen su flujo normal.

Se guarda el secreto aleatorio de 256 bits en `token_secret`, su digest SHA-256
y un identificador UUID en la solicitud. Estos campos están protegidos con
`base.group_no_one` y no son legibles por roles operativos. El secreto permite
que el propietario vuelva a mostrar el mismo QR mediante `action_show_qr`, que
comprueba propietario, estado y empresa antes de crear un asistente transitorio
con reglas de acceso por propietario y empresa.

El número Code128 es un secreto independiente de 40 dígitos, generado mediante
`secrets.randbelow(10 ** 40)` y rellenado con ceros iniciales cuando corresponde;
ofrece aproximadamente 133 bits de aleatoriedad. Se almacena junto con su digest
en campos protegidos, sin usar la secuencia de referencia como credencial.
Debe tratarse con la misma confidencialidad que el QR. No se añaden los códigos
a mensajes ni URLs. Evitar logs SQL detallados, capturas públicas y distribución
no autorizada. Tanto la entrega exitosa como la anulación eliminan las
credenciales de ambos formatos. Los eventos y cambios de estado quedan en
chatter; fechas, actores, motivos de solicitud y anulación, líneas y traslado
constituyen la trazabilidad permanente.

Esto no limita a un administrador técnico con superusuario, acceso directo a la
base o capacidad de instalar código. Tampoco revoca la autoridad general de los
usuarios de Inventario para crear otros ajustes o traslados no vinculados.

## Pruebas automatizadas

En una base **desechable**, con Odoo 19 y PostgreSQL disponibles:

```bash
python /ruta/odoo/odoo-bin \
  --addons-path=/ruta/odoo/addons,/ruta/alvina-odoo19 \
  --db_host=127.0.0.1 --db_user=odoo -d alvina_stock_request_test \
  -i alvina_stock_request --without-demo=True \
  --test-enable --test-tags=/alvina_stock_request --stop-after-init
```

Para volver a ejecutar en una base ya instalada, sustituir `-i` por `-u`.
La validación de esta versión debe cubrir emisión inmediata y reapertura del
comprobante, conservación de QR pendientes al actualizar y backfill idempotente
de Code128; lectura/revisión antes de entregar, ambos formatos y uso único,
autenticación, ACL,
propiedad, multiempresa, falta de stock sin consumo de códigos y reintento,
anulación de solicitudes en **Borrador** o **Solicitada** exclusivamente por su creador,
motivo obligatorio, conservación de líneas e invalidación inmediata de ambos formatos,
rechazo de anulación tras entregar, manipulación de códigos, escrituras
directas/contexto RPC, productos inválidos, lotes, operación normal ajena al
módulo, rollback de una falla posterior a mover stock y rollback de anulación
si falla el mensaje del historial. Las comprobaciones de navegador deben incluir
lectura QR/Code128 desde imagen, rechazo de tipos y tamaños de archivo inválidos,
ausencia de envío de imágenes, liberación de cámara y manejo de permisos denegados.
La prueba con cámara y lector físicos debe realizarse con los equipos del almacén;
las pruebas ORM no sustituyen esa comprobación.

La prueba de concurrencia utiliza **dos conexiones PostgreSQL reales** y crea
fixtures con commit; ejecutarla exclusivamente en la base desechable:

```bash
python /ruta/odoo/odoo-bin shell \
  --addons-path=/ruta/odoo/addons,/ruta/alvina-odoo19 \
  --db_host=127.0.0.1 --db_user=odoo -d alvina_stock_request_test --no-http \
  < /ruta/alvina-odoo19/alvina_stock_request/tests/concurrency_probe.py
```

Exige una entrega, un rechazo y cinco unidades en destino, con un único traslado.

Para comprobar la carrera entre anular y entregar, ejecutar también:

```bash
python /ruta/odoo/odoo-bin shell \
  --addons-path=/ruta/odoo/addons,/ruta/alvina-odoo19 \
  --db_host=127.0.0.1 --db_user=odoo -d alvina_stock_request_test --no-http \
  < /ruta/alvina-odoo19/alvina_stock_request/tests/cancellation_concurrency_probe.py
```

Este segundo probe utiliza dos conexiones PostgreSQL independientes para las
operaciones que compiten y comprueba ambas prioridades: anulación primero y
entrega primero. Verifica el bloqueo de la operación perdedora y exige que solo
una transición termine, con ambos formatos invalidados y auditoría coherente.
Si gana la anulación, exige conservar todas las existencias y no crear traslado; si gana
la entrega, exige un único traslado completo y rechazar la anulación. También
crea fixtures con commit y debe ejecutarse únicamente en la base desechable.

Para probar el decoder local en Chromium sin Odoo ni una cámara física,
se necesitan Node 22 o posterior, Chromium y el entorno Python de Odoo con
Pillow, qrcode, ReportLab y un backend PNG funcional:

```bash
ODOO_PYTHON=/ruta/venv/bin/python CHROMIUM=/usr/bin/chromium \
  node /ruta/alvina-odoo19/alvina_stock_request/tests/browser_reader.mjs
```

El runner genera imágenes de prueba, carga el decoder local real, comprueba
PNG/JPEG/WebP y simula permisos, selección y cierre de cámara, incluidas carreras
al iniciar. No envía imágenes ni requiere credenciales de Odoo. No sustituye
los ensayos con lectores y cámaras físicos.

Para probar la integración real con Owl y los assets de Odoo, instalar y
arrancar una base local desechable `alvina_browser_*` y sembrar el fixture:

```bash
python /ruta/odoo/odoo-bin shell \
  --addons-path=/ruta/odoo/addons,/ruta/alvina-odoo19 \
  --db_host=127.0.0.1 --db_user=odoo -d alvina_browser_test --no-http \
  < /ruta/alvina-odoo19/alvina_stock_request/tests/browser_fixture.py

ODOO_URL=http://127.0.0.1:8082 CHROMIUM=/usr/bin/chromium \
  node /ruta/alvina-odoo19/alvina_stock_request/tests/browser_odoo_smoke.mjs
```

El servidor de pruebas debe servir esa base en el puerto indicado. Configurar
`ODOO_LOGIN` y `ODOO_PASSWORD` con credenciales de esa base mediante su manejo
seguro habitual; por defecto utiliza el admin inicial de la base desechable.
El fixture concede roles y crea y confirma datos de prueba, por lo que rechaza
bases ajenas a esos nombres y PostgreSQL remoto. El JSON temporal contiene
credenciales de retiro de fixtures y tiene permisos 0600. No guardarlo en Git.
El runner revisa, no entrega; comprueba campo real, diálogo, carga local de imagen,
comprobante y ausencia de RPC automáticos de revisión/entrega.

`tests/upgrade_probe.py` comprueba una actualización real en una base
desechable: fase `seed` con el addon `19.0.2.1.1`, actualización `-u` con
`19.0.2.2.0`, fase `check`, segunda actualización y fase `idempotent`.
Se controla mediante `ALVINA_UPGRADE_PROBE_PHASE`; su baseline temporal contiene
credenciales de prueba y tiene permisos 0600. Las fases de comprobación requieren
`zxing-cpp` y NumPy solo como herramientas de prueba para decodificar los PNG
realmente generados. Ver comandos y resultados en [VALIDATION.md](VALIDATION.md).

Los cambios de almacenes/tipos en fixtures son datos de prueba y no se ejecutan
al instalar el módulo. Ver [VALIDATION.md](VALIDATION.md) para resultados reales
y limitaciones de esta entrega.
