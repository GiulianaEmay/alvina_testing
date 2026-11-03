# ALVIÑA POS

Primero se cobra y se entrega el ticket de atención habitual. Al final, en la
pantalla de pago exitoso, Boleta/Factura consulta el DNI/RUC y muestra los datos.
Confirmar y emitir genera el comprobante fiscal del mismo pedido, lo envía mediante
la integración SUNAT/OSE de Odoo y abre su PDF cuando se confirma el envío.
Se obtienen dos documentos: el ticket de atención y la boleta o factura fiscal.
No requiere alta manual de contactos ni un proceso nocturno.

Los clientes nuevos quedan como identidades fiscales internas archivadas.
Consulte el README del repositorio para instalación, actualización y pruebas.
Dependencias: point_of_sale, l10n_pe, account_edi, requests.

## Compatibilidad de arranque — 19.0.3.0.2

El formato electrónico opcional se guarda por compañía en los parámetros de
Odoo, sin exigir la columna `res_company.alvina_edi_format_id`. Esto permite
reiniciar tras desplegar el código aunque esa columna todavía no exista y
acceder a Apps para realizar las actualizaciones pendientes. Si la columna ya
existe, su selección anterior se conserva como valor de respaldo hasta que el
administrador cambie o borre la selección en la configuración de compañía.
El arreglo no crea columnas ni modifica plantillas al arrancar.
