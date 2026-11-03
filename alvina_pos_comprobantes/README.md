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

## Compatibilidad de arranque — 19.0.3.0.3

El formato electrónico y todas las opciones JSON.PE se guardan por compañía en
los parámetros de Odoo. La lectura de compañías no exige ninguna de las 14
columnas de configuración del módulo. Esto permite reiniciar tras desplegar el
código aunque falten esas columnas y acceder a Apps para realizar las
actualizaciones pendientes. Los valores de columnas antiguas que sí existan se
conservan como respaldo hasta que el administrador cambie o borre la opción.
Los tokens siguen restringidos a administradores y no se exportan al POS.
El arreglo no crea columnas ni modifica plantillas al arrancar. Se comprobó el
arranque HTTP de la portada, `/odoo` y `/web/login` con las 14 columnas ausentes,
sin ejecutar una actualización del módulo antes de arrancar.
