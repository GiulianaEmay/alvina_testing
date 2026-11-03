from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


from pytz import timezone
from datetime import datetime

DEFAULT_PE_DATE_FORMAT = '%Y-%m-%d'
PE_TRANSFER_REASONS = [
    ('01', 'Sale'),
    ('03', 'Sale with delivery to third parties'),
    ('04', 'Transfer between establishments of the same company'),
    ('05', 'Consignment'),
    ('13', 'Others'),
    ('14', "Sale subject to buyer's confirmation"),
    ('17', 'Transfer of goods for transformation'),
    ('18', 'Itinerant issuer transfer CP'),
]
PE_RELATED_DOCUMENT = [
    ('01', 'Factura'),
    ('03', 'Boleta de Venta'),
    ('04', 'Liquidación de Compra'),
    ('09', 'Guía de Remisión Remitente'),
    ('12', 'Ticket o cinta emitido por máquina registradora'),
    ('31', 'Guía de Remisión Transportista'),
    ('48', 'Comprobante de Operaciones - Ley N° 29972'),
    ('49', 'Constancia de Depósito - IVAP (Ley 28211)'),
    ('50', 'Declaración Aduanera de Mercancías'),
    ('52', 'Declaración Simplificada (DS)'),
    ('65', 'Autorización de Circulación para transportar MATPEL - Callao'),
    ('66', 'Autorización de Circulación para transporte de carga y mercancías en Lima Metropolitana'),
    ('67', 'Permiso de Operación Especial para el servicio de transporte de MATPEL - MTC'),
    ('68', 'Habilitación Sanitaria de Transporte Terrestre de Productos Pesqueros y Acuícolas'),
    ('69', 'Permiso / Autorización de operación de transporte de mercancías'),
    ('71', 'Resolución de Adjudicación de bienes - SUNAT'),
    ('72', 'Resolución de Comiso de bienes - SUNAT'),
    ('73', 'Guía de Transporte Forestal o de Fauna - SERFOR'),
    ('74', 'Guía de Tránsito - SUCAMEC'),
    ('75', 'Autorización para operar como empresa de Saneamiento Ambiental - MINSA'),
    ('76', 'Autorización para manejo y recojo de residuos sólidos peligrosos y no peligrosos'),
    ('77', 'Certificado fitosanitario la movilización de plantas, productos vegetales, y otros artículos reglamentados'),
    ('78', 'Registro Único de Usuarios y Transportistas de Alcohol Etílico'),
    ('80', 'Constancia de Depósito - Detracción'),
    ('81', 'Código de autorización emitida por el SCOP'),
    ('82', 'Declaración jurada de mudanza'),
]

class StockPicking(models.Model):
    _inherit = "stock.picking"
    
    def action_autocomplete_qty_done(self):
        for picking in self:
            for line in picking.move_ids:
                line.quantity =  line.product_uom_qty
        return True
    
    def action_open_sales_register(self):

        return {
            "type": "ir.actions.act_window",
            "name": "Registro de ventas",
            "res_model": "stock.picking",
            "view_mode": "form",
            "res_id": self.id,
            "view_id": self.env.ref(
                "alvinha_stock.stock_picking_sales_view"
            ).id,
            "target": "current",
        }


    def action_send_delivery_guide_purchase(self):
        """Check required fields, generate the XML delivery guide, and send it to SUNAT"""
        self._check_company()
        self._l10n_pe_edi_check_required_data_purchase()
        for record in self:
            # == Generate a document number ==
            if not record.l10n_latam_document_number:
                sunat_sequence = self.env['ir.sequence'].search([
                    ('code', '=', 'l10n_pe_edi_stock.stock_picking_sunat_sequence'),
                    ('company_id', '=', record.company_id.id)], limit=1)
                if not sunat_sequence:
                    sunat_sequence = self.env['ir.sequence'].sudo().create({
                        'name': 'Stock Picking Sunat Sequence %s' % record.company_id.name,
                        'code': 'l10n_pe_edi_stock.stock_picking_sunat_sequence',
                        'padding': 8,
                        'company_id': record.company_id.id,
                        'prefix': 'T001-',
                        'number_next': 1,
                    })
                record.l10n_latam_document_number = sunat_sequence.next_by_id()

            # == Send the delivery guide ==
            record.l10n_pe_edi_status = 'to_send'
            edi_str = record._l10n_pe_edi_create_delivery_guide_purchase()
            res = record._l10n_pe_edi_sign(edi_str)

            if 'error' in res:
                record.l10n_pe_edi_error = res['error']
                continue

            # == Create the attachments ==
            if res.get('cdr'):
                attachments = self.env['ir.attachment'].create([
                    {
                        'name': '%s-09-%s.xml' % (record.company_id.vat, record.l10n_latam_document_number),
                        'res_model': record._name,
                        'res_id': record.id,
                        'type': 'binary',
                        'raw': edi_str,
                        'mimetype': 'application/xml',
                        'description': _('Guía de remisión para transferencia %s', record.name),
                    },
                    {
                        'name': 'cdr-%s-09-%s.xml' % (record.company_id.vat, record.l10n_latam_document_number),
                        'res_model': record._name,
                        'res_id': record.id,
                        'type': 'binary',
                        'raw': res['cdr'],
                        'mimetype': 'application/xml',
                        'description': _('Recibo de guía de entrega (CDR) para transferencia %s', record.name)
                    }
                ])
                message = _("La guía de entrega fue firmada satisfactoriamente por SUNAT.")
                record._message_log(body=message, attachment_ids=attachments.ids)
                record.write({'l10n_pe_edi_error': False, 'l10n_pe_edi_status': 'sent'})
        
    def _l10n_pe_edi_check_required_data_purchase(self):
        """Some attributes are required to allow generate the XML file, that attributes are review here to avoid SUNAT
        errors."""
        for record in self:
            errors = record._l10n_pe_edi_generate_missing_data_error_list_purchase()
            if not errors:
                continue
            error_msg = self.env._("Configuración de selección no válida:") + "\n\n" + "\n".join(errors)
            raise UserError(error_msg)

    def _l10n_pe_edi_generate_missing_data_error_list_purchase(self):
        """ Check that all the required data is present before generating the delivery guide.
            Based on SUNAT resolution 000123-2022 (published 2022-07-12), pages 48-54,
            and on the list of checks published at
            https://cpe.sunat.gob.pe/sites/default/files/inline-files/ValidacionesGREv20221020_publicacion.xlsx
        """
        errors = []
        if not self.company_id.partner_id:
            errors.append(_('Por favor, indique una dirección de entrega, ya que la guía de entrega la requiere.'))
        if not self.company_id.partner_id.l10n_pe_district:
            errors.append(_('La empresa debe tener un distrito definido.'))
        if not self.l10n_pe_edi_transport_type:
            errors.append(_('Debe seleccionar un tipo de transporte para generar la guía de entrega.'))
        if not self.l10n_pe_edi_reason_for_transfer:
            errors.append(_('Debes elegir el motivo de la transferencia.'))
        if not self.l10n_pe_edi_departure_start_date:
            errors.append(_('Debes elegir la fecha de inicio de la transferencia.'))
        if self.l10n_pe_edi_transport_type == '02' and not self.l10n_pe_edi_vehicle_id:
            errors.append(_('Debe elegir el vehículo de traslado.'))
        if not self.partner_id.l10n_latam_identification_type_id:
            errors.append(_('El proveedor requiere un tipo de documento específico.'))
        if not self.partner_id.vat:
            errors.append(_('El proveedor necesita un número de identificación.'))
        warehouse_address = self.partner_id
        if not warehouse_address.l10n_pe_district:
            errors.append(_('La dirección de origen debe tener un distrito definido.'))
        if not warehouse_address.street:
            errors.append(_('La dirección de origen debe tener una calle definida.'))
        if self.partner_id.l10n_latam_identification_type_id.l10n_pe_vat_code != "6":
            errors.append(_("En el contacto del proveedor, el tipo de identificación de la empresa debe estar configurado como RUC."))
        if (self.l10n_pe_edi_transport_type == '02' and not self.l10n_pe_edi_operator_id and not self.l10n_pe_edi_vehicle_id.is_m1l
            or self.l10n_pe_edi_transport_type == '01' and not self.l10n_pe_edi_operator_id):
            errors.append(_("Debe elegir el operador de transferencia."))
        return errors
        

    def _l10n_pe_edi_create_delivery_guide_purchase(self):
        """ Generate the delivery guide XML. """
        values = self._l10n_pe_edi_get_delivery_guide_values_purchase()
        return self.env['ir.qweb']._render('l10n_pe_edi_stock.sunat_guiaremision', values).encode()

    def _l10n_pe_edi_get_delivery_guide_values_purchase(self):
        """ Used to generate the XML file that will be send to stamp in SUNAT
        The document number comes from the sequence with code "l10n_pe_edi_stock.stock_picking_sunat_sequence", and
        will be generated automatically if this not exists."""
        self.ensure_one()

        def format_date(date):
            return date.strftime(DEFAULT_PE_DATE_FORMAT) if date else ''

        def format_float(val, digits=2):
            return '%.*f' % (digits, val)

        date_pe = datetime.now(tz=timezone('America/Lima')).date()
        return {
            'date_issue': date_pe.strftime(DEFAULT_PE_DATE_FORMAT),
            'time_issue': date_pe.strftime("%H:%M:%S"),
            'l10n_pe_edi_observation': self.l10n_pe_edi_observation or 'Guía',
            'record': self,
            'weight_uom': self.env['product.template']._get_weight_uom_id_from_ir_config_parameter(),
            'warehouse_address': self.partner_id,
            'document_number': self.l10n_latam_document_number,
            'format_date': format_date,
            'moves': self.move_ids.filtered(lambda ml: ml.quantity > 0),
            'reason_for_transfer': dict(PE_TRANSFER_REASONS)[self.l10n_pe_edi_reason_for_transfer],
            'format_float': format_float,
            'related_document': dict(PE_RELATED_DOCUMENT)[self.l10n_pe_edi_related_document_type] if self.l10n_pe_edi_related_document_type else False,
        }

    pricelist_id = fields.Many2one(
        comodel_name='product.pricelist',
        string='Lista de Precios',
        check_company=True,
    )


class StockMove(models.Model):
    _inherit = "stock.move"

    product_lst_price = fields.Float(
        string='Precio',
        compute='_compute_product_lst_price',
        digits='Product Price',
        store=True,
        help="Precio calculado a partir de la lista de precios"
    )
    
    qty_sold = fields.Float("Cantidad vendida", compute="_compute_qty_sold",store=True)
    total_sold = fields.Float(compute="_compute_total_sold", string="Total vendido", store=True)
    qty_remaining = fields.Float(
        string="Cantidad restante",
        store=True,
    )
    total_remaining = fields.Float(compute='_compute_total_remaining', string="Total restante", store=True)
    
    
    @api.depends('product_lst_price', 'qty_remaining')
    def _compute_total_sold(self):
        for record in self:
            if record.product_lst_price:
                record.total_sold = record.qty_sold * record.product_lst_price 
            else:
                record.total_sold = 0
    
    
    @api.depends('product_lst_price', 'qty_remaining')
    def _compute_total_remaining(self):
        for record in self:
            record.total_remaining = record.qty_remaining * record.product_lst_price
    
    @api.constrains("qty_remaining", "quantity")
    def _check_qty_remaining(self):
        for line in self:
            if line.qty_remaining > line.quantity:
                raise ValidationError(
                    f"{line.product_id.display_name}. La cantidad sobrante no puede ser mayor que la cantidad recibida."
                )
                


    @api.depends("quantity", "qty_remaining")
    def _compute_qty_sold(self):
        for rec in self:
            if rec.qty_remaining!=False:
                rec.qty_sold = rec.quantity - rec.qty_remaining
            else:
                rec.qty_sold = rec.quantity


    @api.depends('picking_id','picking_id.pricelist_id', 'product_id', 'product_uom_qty', 'product_uom')
    def _compute_product_lst_price(self):
        for move in self:
            # Si hay lista de precios y producto, calculamos el precio
            if move.picking_id and move.picking_id.pricelist_id and move.product_id:
                move.product_lst_price = move.picking_id.pricelist_id._get_product_price(
                    product=move.product_id,
                    quantity=move.product_uom_qty or 1.0,
                    uom=move.product_uom or move.product_id.uom_id,
                    date=fields.Date.today(),
                )
            else:
                move.product_lst_price = 0.0