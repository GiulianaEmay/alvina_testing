# ALVIÑA — Descuento de Colaboradores · Odoo 19

Versión **19.0.1.1.2** · revisión de operación continua.

Addon creado desde cero para `point_of_sale` + `hr`. No modifica el núcleo,
no necesita repositorios privados ni librerías adicionales en producción.
Licencia LGPL-3.0-or-later.

## Comportamiento

- Beneficio inicial del **15 % del total con impuestos**, hasta **S/ 100** por
  colaborador y semana, compartido entre sucursales y compañías de la misma BD.
- Semana calendario **lunes 00:00 a domingo**, en **America/Lima**. El cupo de
  cada semana es independiente: sin cron, ventana móvil ni arrastre de saldos.
- Compras ilimitadas hasta agotar el cupo. Reservas pendientes de otras cajas
  cuentan contra el saldo. Solo POS en moneda **PEN**.
- DNI de ocho dígitos de `hr.employee`, con autorización explícita y empleado
  activo. En Odoo 19 la identificación estándar reside en `hr.version`: la clave
  única se toma de la versión actual, no de una versión histórica seleccionada.
- No se descarga el padrón ni los DNI. El RPC devuelve solo nombre y cifras de
  la consulta exacta; el ticket no incluye nombre ni DNI. Auditoría: DNI oculto
  salvo los últimos cuatro dígitos.
- Se exige al cajero confirmar presencialmente la identidad. Conocer el DNI no
  autentica al comprador. El sistema no verifica documentos biométricamente.

## Instalar y actualizar

1. Disponer de Odoo **19.0** y PostgreSQL con `point_of_sale` y `hr` disponibles.
2. Añadir **la raíz de este repositorio** al `addons_path`, junto con los addons
   oficiales. El directorio interno `alvina_pos_employee_discount` es el addon.
3. Actualizar la lista de aplicaciones e instalar **ALVIÑA — Descuento de
   Colaboradores**, o ejecutar:

   ```sh
   python3 /ruta/odoo/odoo-bin -c /ruta/odoo.conf -d BASE \
     -i alvina_pos_employee_discount --stop-after-init
   ```

4. Para una actualización, respaldar BD/filestore y usar `-u` en lugar de `-i`.
5. Reiniciar Odoo y recargar las cajas para cargar los assets nuevos.

Al actualizar desde 19.0.1.0.0, la migración marca como confirmados únicamente
los pedidos pagados/contabilizados/facturados con consumo registrado. No aumenta,
reinicia ni vuelve a consumir cupos. Actualizar el addon en el servidor antes de
recargar las cajas: esta versión añade un campo de confirmación del pago.

No se instalan colaboradores, productos de prueba ni credenciales con el addon.
Los scripts `tools/*fixture*` son exclusivamente para una base desechable.

## Configurar y operar

- RR. HH. → empleado → identificación: registrar DNI y marcar **Beneficio
  ALVIÑA autorizado**. No hace falta usuario Odoo para el colaborador.
- El grupo **ALVIÑA: Administrador del beneficio** permite consultar la auditoría
  y configurar la política en Ajustes → Punto de venta → ALVIÑA. El administrador
  inicial recibe este grupo. No concede acceso privado de RR. HH.; para autorizar
  trabajadores hace falta además el permiso estándar de RR. HH.
- Valores iniciales: 15 %, S/ 100 y America/Lima. El administrador puede reducir
  porcentaje/cupo dentro de estos máximos y habilitar cada POS. La política es
  global; nunca hay cupos diferentes por caja. La zona horaria se fija a Lima.
- En productos: **tres puntos → Acciones → Descuento colaborador**, DNI,
  **Consultar**, verificar al titular,
  marcar la confirmación y **APLICAR DESCUENTO**. El total cambia inmediatamente.
- La reserva dura **10 minutos**. Repetir la aplicación sobre el mismo pedido
  actualiza la misma operación. **Cancelar** cierra la ventana; **Retirar beneficio**
  libera expresamente la reserva y quita el descuento. Después se puede cambiar
  de colaborador conservando el intento cancelado en la auditoría. Cancelar el
  pedido también libera su reserva. Si se abandona una caja, la reserva expira automáticamente y
  se descuenta del cálculo de saldo sin necesidad de cron. Reaplicar una reserva
  vencida exige consulta y autorización nuevas en línea.
- Cambiar productos, cantidades o precios retira el importe mostrado y exige
  reaplicar el beneficio. Cambios fiscales, de cliente o de lista también se
  verifican contra la huella del servidor antes de cobrar.
- **Sin conexión no se aplica ni se confirma el beneficio**. Si falla la respuesta
  de pago, no se imprime como venta confirmada ni se toma el flujo offline estándar:
  el pedido y sus pagos permanecen en IndexedDB, incluso al recargar el navegador.
  Reconectar y pulsar Validar para consultar si el servidor confirmó. No volver a
  cobrar mientras exista esa incertidumbre. Para continuar sin el beneficio,
  retirarlo en línea una vez resuelto el estado.

## Impuestos y promociones

El cálculo usa las líneas fiscales y el motor `account.tax` de Odoo 19, incluida
su agregación y redondeo monetario. El importe objetivo es
`min(redondear(total_original × 15 %), saldo)`. Se busca un porcentaje efectivo
sobre las líneas que produzca una reducción fiscal que no exceda ese importe.
Con impuestos porcentuales ordinarios es 15 %; con cupo parcial es menor. Con
impuestos fijos puede diferir el porcentaje de base para conservar el descuento
sobre el total con impuestos. En los límites del redondeo puede aplicarse un
céntimo menos; jamás se excede el cupo.

Por seguridad se rechazan compras con descuentos previos, líneas negativas,
recompensas, reglas de listas de precios, precios marcados como manuales y combos.
Así se evita acumular promociones detectables por las APIs estándar. Productos
ordinarios sin estas condiciones son elegibles. Rebajas incorporadas directamente
al precio de catálogo no tienen una marca universal de promoción en Odoo: deben
controlarse administrativamente. Integraciones promocionales propias requieren
validación adicional antes de habilitar este beneficio.

El descuento se almacena en `pos.order.line.discount`. Odoo conserva el cálculo
normal de IGV/impuestos, pagos, stock, cierre e ingreso contable. Se traslada el
porcentaje estándar a la factura cuando su precisión permite representarlo. Si
el cupo parcial requiere más decimales que la precisión contable de descuento,
se utiliza el precio unitario neto y se indica «Descuento Colaborador incluido
en el precio neto». Esto conserva exactamente el total del POS y de la factura,
incluidas notas de crédito, sin cambiar la precisión global de Odoo. No se parchea
la facturación asíncrona. Emitir la factura después del pago no vuelve a consumir el cupo.

## Transacciones, permisos y auditoría

Cada operación tiene una referencia única igual al UUID del pedido. El servidor
sincroniza primero el borrador y calcula desde sus líneas persistidas. Reserva,
consumo y reversión bloquean la fila del empleado mediante un `UPDATE` sin cambio
lógico. Esto cambia la versión MVCC de la fila: bajo REPEATABLE READ una caja con
snapshot anterior debe reintentar la transacción, evitando lecturas de saldo
obsoleto. Odoo reintenta los conflictos de serialización de sus RPC normales.
No se hacen commits manuales dentro de los modelos.

En la transición real de borrador a pagado se verifica en la misma transacción:
sesión/usuario/compañía, autorización activa, vigencia y semana de la reserva,
huella completa del carrito, porcentaje, cálculo fiscal y saldo global. Solo
entonces se consume. Un fallo posterior de pago/factura revierte toda la
transacción. La sincronización repetida de un pedido pagado no consume otra vez.
Las ventas confirmadas con beneficio no admiten cambios financieros: corregir
con devoluciones estándar.

Los cajeros no tienen ACL sobre el historial ni acceso privado HR. Las elevaciones
`sudo()` están limitadas al lookup exacto del DNI y al libro interno de beneficios,
tras comprobar permisos POS, sesión propia y compañía. Los métodos auxiliares
son privados. Los administradores tienen auditoría de solo lectura con reglas
multicompañía; consumo, configuración POS, periodo, cajero, importes y reversión.
El saldo mostrado es el de la semana vigente, incluido el consumo reservado.
Leer la auditoría consulta una instantánea sin bloquear a los empleados ni
modificar reservas vencidas. Los bloqueos se usan al reservar, consumir o liberar.

## Devoluciones y cancelaciones

Las cancelaciones de borradores liberan reservas. Una venta pagada se corrige
mediante devoluciones vinculadas a las líneas originales, con su precio,
impuestos mapeados, moneda, posición fiscal, unidad y porcentaje originales. Una devolución parcial repone la parte de
descuento correspondiente a la cantidad devuelta; una completa repone el consumo
original completo. Se limita la reversión al importe consumido y a las cantidades
originales. Una devolución que ya repuso cupo queda protegida frente a cambios
financieros o de su vínculo con la compra original. Cada devolución tiene un registro único y se procesa bajo el mismo
bloqueo del empleado, sin descuentos negativos ni reposiciones duplicadas.

La reposición modifica **la semana original**. Una devolución de la semana pasada
no aumenta el cupo actual. Un cambio manual de precio/descuento/impuestos en la
devolución no se trata como una restitución válida del beneficio.

## Pruebas reproducibles

Con un checkout **público** de Odoo 19, sus dependencias instaladas y PostgreSQL:

```sh
python3 tools/validate_static.py
node tools/test_frontend.mjs                 # ODOO_ROOT apunta al checkout 19.0
export ODOO_ROOT=/ruta/odoo
export ODOO_PYTHON=/ruta/venv/bin/python
export PGHOST=/ruta/socket PGPORT=5432 PGUSER=odoo DB_NAME=alvina_test
bash tools/run_odoo_tests.sh
```

`tools/test_concurrency.py` se ejecuta mediante `odoo-bin shell` sobre una base
**desechable cuyo nombre termine en `_test`**, con este addon y un plan contable
estándar instalados. Crea fixtures locales, confirma dos reservas concurrentes en
conexiones PostgreSQL independientes, comprueba S/ 90 + S/ 10 = S/ 100 y limpia
sus registros. Usa commits únicamente en este script de prueba, nunca en producción.

Para la prueba de navegador, ejecutar `tools/prepare_browser_fixture.py` mediante
`odoo-bin shell` en una base desechable; crea datos locales y un acceso temporal
aleatorio en `/tmp/alvina-browser-fixture.json` con permisos 0600. Arrancar Odoo en
loopback puerto 8079 y ejecutar `node tools/browser_smoke.cjs` con Playwright y
Chromium instalados como herramientas de desarrollo. El script valida consulta,
reserva, pago y ticket real. `ALVINA_BROWSER_FIXTURE` permite elegir otro archivo;
`CHROMIUM_PATH` permite elegir el navegador. Nunca usar la fixture en producción.

Las pruebas backend cubren DNI, versiones HR, autorización, agotamiento, varias
compras/sucursales, lunes de Lima, semanas sin arrastre, reservas, expiración,
idempotencia, manipulación, devolución, impuestos y factura contablemente
balanceada. Las pruebas unitarias JS ejercitan los parches de producción con
fronteras de red/navegación controladas; no sustituyen la prueba de navegador.

Con Odoo ejecutándose en loopback puerto 8079 contra la misma base de prueba,
se pueden ejecutar los tres escenarios reales de navegador con:

```sh
# Reutiliza ODOO_ROOT, ODOO_PYTHON, PGHOST, PGPORT, PGUSER y DB_NAME de arriba.
# ODOO_DATA_DIR, si se define, debe coincidir con el filestore del servidor.
bash tools/run_browser_tests.sh
```

Se prueba venta normal, respuesta perdida después del commit y desconexión antes
del commit; en ambos fallos se recarga el navegador y se comprueba un solo pedido,
una sola línea de pago y un solo consumo. Se crea una fixture nueva por escenario
y se elimina el archivo temporal de acceso al terminar. La edición del pedido
se bloquea mientras el pago siga incierto.

Ver [VALIDATION.md](../VALIDATION.md) para evidencia exacta de la ejecución.

## Antes de instalar en la instancia real

Se validó Odoo 19 **Community** con los módulos estándar. Falta comprobar la
edición Enterprise, localización peruana y emisión electrónica de boletas/facturas,
hardware fiscal, terminales reales y módulos particulares de facturación diferida
o promociones. No se afirma certificación SUNAT ni prueba en producción.

Las cajas deben usar una BD central. Bases independientes no comparten cupos.
Mantener TLS, permisos y acceso de red de la instancia, y aplicar su política de
retención de datos HR/auditoría. No conceder administración técnica a los cajeros.
