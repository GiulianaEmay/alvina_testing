# Requerimientos de sucursales — Odoo 19

## Flujo de Compras (19.0.3.2.0)

1. **Compras → Órdenes → Formulario Web** abre una pestaña nueva.
2. Enviar el formulario registra el requerimiento y una **solicitud de
   cotización en borrador**. No confirma compras, ventas, existencias ni facturas.
3. La cotización usa las acciones normales de Odoo: **Enviar solicitud de
   cotización**, **Confirmar orden**, **Imprimir** y **Cancelar**.
4. **Confirmar orden** confirma la compra y su venta de Industria vinculada.
   Si la compañía exige doble aprobación, espera al aprobador de Compras; no
   omite ese control. La recepción aparece al confirmar efectivamente la compra.
5. Dentro de la recepción: **Validar**, **Imprimir** y **Autocompletar cantidades**.
   Autocompletar registra las cantidades previstas, pero no valida existencias.
   Revise y corrija esas cantidades según lo recibido físicamente antes de validar.
6. Después de validar: **Imprimir** y **Generar guía de remisión**. Esta última
   genera el PDF de la **guía interna valorizada de recepción**, sin envío a SUNAT.
   Se documenta la cantidad realmente recibida y el precio de la OC. Reimprimir
   conserva la misma guía, sus cantidades y su valorización original.

Una recepción parcial conserva un documento pendiente por el saldo. Si la
operación está configurada para cancelar automáticamente los saldos, el módulo
pide corregir esa configuración antes de validar una recepción parcial.
Recibir no valida ni inventa un despacho de Industria. Las cantidades recibidas
y las despachadas siguen siendo magnitudes diferentes.

Las acciones requieren acceso de Compras, la compañía compradora seleccionada y
asignación a la sucursal. La elevación técnica se limita a esa recepción y
mantiene las validaciones estándar de cantidades, lotes y números de serie.

## Historial y actualización

Actualizar `alvina_branch_requisition` a **19.0.3.2.0** después de respaldar la
base de datos y el filestore, y reiniciar Odoo. Por ejemplo:

```sh
python /ruta/odoo/odoo-bin -c /ruta/odoo.conf -d BASE \
  -u alvina_branch_requisition --stop-after-init
```

El nuevo recorrido se registra en las solicitudes nuevas. Las solicitudes
anteriores conservan su recorrido y las OC confirmadas no vuelven a borrador.
Las guías históricas de despacho y su confirmación de recepción siguen
disponibles. No se modifica el flujo de compras ajenas a requerimientos.

Si está instalado el automatismo interempresa de Enterprise, la cotización
conserva los precios calculados al enviar el formulario. La venta nativa se
genera al confirmar la compra. Un fallo de confirmación conserva la cotización
y revierte los documentos y movimientos parciales de ese intento.

## Desarrollo y pruebas

Se necesita Odoo 19, PostgreSQL y las dependencias oficiales. Añada la raíz de
este repositorio al `addons_path`. Use una base desechable para las pruebas:

```sh
python /ruta/odoo/odoo-bin \
  --addons-path=/ruta/odoo/addons,/ruta/alvina_testing \
  -d alvina_branch_test -i alvina_branch_requisition --without-demo=True \
  --test-enable --test-tags=/alvina_branch_requisition --stop-after-init
```

Las pruebas incluyen HTTP, un navegador Chromium y PDF real mediante
wkhtmltopdf con Qt parcheado. `websocket-client` habilita la prueba de navegador.
Las pruebas del adaptador interempresa simulan el contrato de creación de
Enterprise; no sustituyen una prueba con sus módulos licenciados y la
configuración real de una copia del servidor.
