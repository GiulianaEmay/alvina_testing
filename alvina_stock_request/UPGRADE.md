# Actualizar Requerimientos a `19.0.2.2.0`

Esta guía corresponde al módulo `alvina_stock_request` de la rama
`feature/stock-request-code128`. La actualización incorpora Code128, nuevas
credenciales y campos de comprobante, lógica Python, restricciones SQL, vistas
y assets del lector local de cámara/imagen. Requiere actualizar el módulo en la
base y cargar el nuevo código en todos los procesos de Odoo.

**Sí requiere reiniciar el servicio Odoo**, incluidos sus workers y cron,
y ejecutar `-u alvina_stock_request`. No requiere reiniciar el sistema operativo
del servidor. Estos pasos son instrucciones para el administrador: esta entrega
no ejecuta cambios ni reinicios en el servidor dedicado.

El paquete contiene modelos Python: se coloca como carpeta completa en un
directorio incluido en `addons_path` del servidor dedicado. El botón **Importar
módulo** para módulos de datos no instala esta lógica Python. **Actualizar** en
Aplicaciones tampoco descarga los archivos de GitHub. No es necesario fusionar
la rama a `main` para preparar o probar el paquete.

## Compatibilidad y comprobantes pendientes

Se conserva el nombre técnico del módulo, sus registros, líneas, estados,
grupos y vínculos de inventario. Los QR vigentes de solicitudes pendientes de
`19.0.2.1.1` no se rotan ni se sustituyen al actualizar.

La migración añade un código Code128 de 40 dígitos a solicitudes en estado
`submitted` con credenciales QR válidas que aún no tienen ese código. Es
idempotente: volver a ejecutar la actualización conserva los códigos ya
generados. Reabrir el comprobante puede completar ese dato bajo el mismo bloqueo
de solicitud si falta. No se vuelve a enviar la solicitud, no se crean
transferencias ni reservas y no se cambia la auditoría de entrega o anulación.

No se emiten credenciales a documentos entregados o anulados ni se reactivan sus
comprobantes. La entrega o anulación invalida ambos formatos conjuntamente.
La referencia secuencial del documento no sustituye ninguna credencial.

Las versiones `19.0.2.0.0`, `19.0.2.0.1`, `19.0.2.1.0` y `19.0.2.1.1` pertenecen
al flujo de QR inmediato. El flujo anterior `19.0.1.*` tiene documentos y estados
distintos y no cuenta con migración en esta entrega: revisar esos datos por
separado antes de actualizar una base que todavía los contenga.

## Preparación antes de detener el servicio

1. Identificar la versión instalada, base, directorios de addons, entorno Python
   y todos los procesos o instancias de Odoo que utilizan esa base. Comprobar que
   solo una copia del módulo queda visible en los paths efectivos.
2. Preparar un paquete completo del commit elegido, incluyendo migración,
   código Python, XML, imágenes y el bundle local de ZXing con su licencia.
   Mantenerlo fuera del directorio activo hasta la ventana de actualización.
3. Verificar `qrcode`, ReportLab y un backend de PNG `renderPM` funcional en el
   entorno Python que ejecutará Odoo. Puede utilizarse `rl-renderPM==4.0.3` o
   un backend compatible de paquetes oficiales de la distribución, como
   `rlPyCairo` y Cairo. Mantener la verificación de firmas, artefactos y TLS;
   comprobar la generación real del Code128 de 900 × 180 en pruebas.
4. Restaurar una copia de la base y su filestore en un entorno de pruebas
   separado. Desactivar las integraciones externas de esa copia y ensayar el
   procedimiento completo, incluyendo comprobantes pendientes.
5. Preparar una copia recuperable de base, filestore y código anterior, y medir
   el tiempo del ensayo para programar una ventana breve. Preparar el paquete y
   las dependencias antes de esa ventana evita descargar o compilar mientras
   el servicio está detenido.

## Actualización en la copia de pruebas

Detener todos los procesos de Odoo que puedan acceder a esa base, incluidos
HTTP, cron, gevent y procesos de otras instancias que compartan la misma base.
Esperar a que terminen las transacciones activas. Colocar la carpeta completa del
nuevo módulo en el directorio de addons configurado, con los permisos habituales
del servicio; no mezclar archivos de versiones distintas.

Con el servicio detenido, ejecutar una única actualización usando su usuario,
entorno Python y configuración reales. Ejemplo con rutas que debe ajustar el
administrador y una base exclusivamente de pruebas:

```bash
python /ruta/odoo/odoo-bin \
  -c /ruta/odoo.conf -d BASE_PRUEBA \
  -u alvina_stock_request \
  --stop-after-init --no-http \
  --workers=0 --max-cron-threads=0
```

Actualizar únicamente `alvina_stock_request`; no utilizar `-u all`, `--reinit`
ni desinstalar/reinstalar el módulo. Comprobar que la configuración no añade
otras actualizaciones. No dejar `-u` configurado permanentemente en el servicio.

Esta ejecución aplica campos, migración, restricciones y vistas y después
termina. Revisar el código de salida y el log antes de arrancar el servicio.
Comprobar que existen las restricciones de unicidad declaradas por el módulo:
Odoo puede registrar como advertencia un fallo al crear una restricción y
continuar la carga. Un código de salida correcto por sí solo no acredita que
se haya creado cada restricción.

Después, arrancar todos los procesos con el mismo código nuevo. No basta con
recargar vistas o reiniciar un solo worker: los procesos conservan clases
Python cargadas. El ciclo previsto es **detener → colocar código → actualizar
base → arrancar**, sin un arranque normal intermedio antes de actualizar.

## Comprobaciones posteriores

- Versión `19.0.2.2.0`, aplicación **Requerimientos**, icono correcto y asistente
  de comprobante con referencia, área, productos, QR, Code128 y número legible.
- Solicitud pendiente previa: mismo QR y credenciales originales, Code128
  añadido una sola vez, mismas líneas, sin transferencia ni reserva nueva.
  Repetir el ensayo de actualización debe conservar ambos formatos.
- Solicitud nueva: emisión inmediata de ambos códigos y acceso exclusivo del
  propietario al comprobante dentro de su empresa autorizada.
- Lectura con lector físico y entrada manual, incluidos códigos con ceros
  iniciales. La referencia de solicitud no debe autorizar la entrega.
- En HTTPS, cámara compatible y permisos; selección de cámara y liberación al
  cerrar. Imágenes PNG/JPEG/WebP de hasta 5 MiB, decodificadas en el navegador,
  sin subir sus bytes al servidor ni a servicios externos.
- Revisión separada de autorización, stock insuficiente sin consumo de
  credenciales ni reservas persistentes, entrega exitosa que invalida ambos
  formatos y rechazo de cualquier segundo retiro.
- Anulación propia con motivo, auditoría y stock intacto; rechazo de códigos
  previamente leídos y de anulación después de entregar. Comprobar también
  permisos multiempresa y los probes de concurrencia en bases desechables.

Los comandos de pruebas están en [README.md](README.md). Los resultados
realmente ejecutados y sus limitaciones se registran en
[VALIDATION.md](VALIDATION.md); esta guía describe el procedimiento y las
comprobaciones requeridas, no certifica una actualización del servidor real.

## Recuperación y ventana de servicio

Si falla la actualización, mantener el servicio detenido, conservar el log y
resolver la causa en la copia de pruebas antes de volver a intentarlo. No
activar workers de una versión distinta a la que corresponde al esquema de la
base. Si se decide volver a la versión anterior, restaurar **base, filestore y
código compatibles del mismo punto de recuperación**, no únicamente los
archivos Python después de haber cambiado el esquema o migrado datos.

Una restauración puede perder operaciones realizadas después del punto de
recuperación. Mantener la aplicación fuera de servicio durante el cambio y
validar antes de reabrir reduce esa exposición. El procedimiento real del
servidor, sus tiempos y su recuperación corresponden al administrador; no se
ha reiniciado, actualizado ni desplegado ese servidor como parte del trabajo
en el repositorio.
