/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';
import { rpc } from '@web/core/network/rpc';

publicWidget.registry.MyForm = publicWidget.Widget.extend({
    init: function (parent, options) {
        this._super.apply(this, arguments);
        this.exist_other_orders = false;
    },
    selector: 'form#portal_production_form',
    //template: 'production_form',

    events: {
        'change select[name="agrupador_id"]': '_onSelectAgrupador',
        'change select[name="company_id"]': '_onSelectCompany',
        'submit': '_onSubmit',
        'change input[name="date_order"]': '_onChangePartnerDate',
        'change select[name="partner_id"]': '_onChangePartnerDate',
    },
    async _onSubmit(ev) {
        ev.preventDefault();

        const $form = this.$el;
        const $submitBtn = $form.find('button[type="submit"]');

        // Evitar doble submit
        if ($submitBtn.prop('disabled')) {
            return;
        }

        // Deshabilitar submit
        $submitBtn.prop('disabled', true).text('Enviando...');

        try {
            const payload = await this._collectFormData();
            console.log("payload", payload)
            const result = await rpc('/portal/production/create', payload);

            // Redirección
            console.log("results", result);
            if (result && result.redirect_url) {
                window.location.href = result.redirect_url;
            }
            if (result && result.error) {
                alert(`Error: ${result.error}`)
                $submitBtn.prop('disabled', false).text('Enviar');

            }

        } catch (error) {
            console.error('Error en submit', error);
            alert('Ocurrió un error al enviar el formulario.');
            $submitBtn.prop('disabled', false).text('Enviar');
        }
    },

    async _collectFormData() {
        const partnerId = this.$el.find('select[name="partner_id"]').val();
        const dateOrder = this.$el.find('input[name="date_order"]').val();
        const csrf_token = this.$el.find('input[name="csrf_token"]').val();
        const agrupador_id = this.$el.find('select[name="agrupador_id"]').val()
        const company_id = this.$el.find('select[name="company_id"]').val()

        const products = [];

        this.$el.find('#productos_table_body tr').each(function () {
            const productId = $(this).data('product-id');
            const qty = $(this).find('input[name="qty"]').val();

            if (productId && qty && parseFloat(qty) > 0) {
                products.push({
                    product_id: productId,
                    qty: parseFloat(qty),
                });
            }
        });


        return {
            partner_id: partnerId,
            date_order: dateOrder,
            lines: products,
            csrf_token: csrf_token,
            agrupador_id,
            company_id,
            exist_other_orders: this.exist_other_orders,
        };
    },
    async _onSelectAgrupador(ev) {
        const agrupador_id = ev.currentTarget.value;
        var productTable = $("#productos_table_body")
        productTable.empty();
        if (agrupador_id) {
            try {
                const data = await  rpc("/portal/production/agrupador_products", {
                    agrupador_id: agrupador_id,
                });

                if (data.error) {
                    alert(data.error);
                    return;
                }
                console.log("data", data)
                data.products.forEach(p => {
                    productTable.append(`
                        <tr data-product-id="${p.id}">
                            <td>${p.name}</td>
                            <td>
                            <input type="number"
                                   min="0"
                                   value="0"
                                   step="1"
                                   name="qty"
                                   required
                                   class="form-control qty_input"
                                   data-product-id="${p.id}"
                                   data-uom-id="${p.uom_id || ''}">
                        </td>
                    </tr>
                    `)
                });
            } catch (err) {
            console.error("Error loading agrupador products:", err);
            alert("Error cargando productos del agrupador.");
        }
        }
    },
    async _check_duplicate_orders(date, partner_id){
        return await  rpc("/portal/mrp/check_partner_date", {
            partner_id: partner_id,
            date_str: date 
        });
        

    },
    async _onChangePartnerDate(ev) {
        let date = $('input[name="date_order"]').val();
        let partner_id = $('select[name="partner_id"]').val();

        console.log("before exist_other_orders", this.exist_other_orders)
        if (date ) {
            var $resultDiv = $('#msg_warning');
            $resultDiv.empty();
            const data = await this._check_duplicate_orders(date, partner_id)
            if (data && data.exists) {
                // Si existe: Mostrar mensaje exitoso (verde o el color que prefieras)
                this.exist_other_orders = true;
                console.log("after exist_other_orders", this.exist_other_orders)
                $resultDiv.css("display", "block"); 
                $resultDiv.html('<span  class="">' + data.msg_user + '</span>');
            } else {
                $resultDiv.css("display", "none"); 
                this.exist_other_orders = false;

            }
        }
    },
    start() {
        const data = this.$el.data();
        //console.log("data", data)
        // const today = this.$el.data('today');
        // this.$el.find('input[name="date_order"]').attr('min', today);
        // console.log("today ", today)
        
        return this._super(...arguments);
    },

    async _onSelectCompany(ev) {
        const company_id = ev.currentTarget.value;
        var partnerList = $("#partners")
        partnerList.empty();
        partnerList.append('<option value="">Seleccione ...</option>');
        if (company_id) {
            try {
                const data = await  rpc("/portal/get_partners", {
                    company_id: company_id,
                });

                if (data.error) {
                    alert(data.error);
                    return;
                }
                console.log("data partners ", data)
                data.forEach(partner => {
                    partnerList.append(
                        '<option value="' + partner.id + '">' +
                        partner.name +
                        '</option>'
                    )
                });
            } catch (err) {
            console.error("Error loading agrupador products:", err);
            alert("Error cargando productos del agrupador.");
        }
        }
    },
});

export default {
    MyForm: publicWidget.registry.MyForm
}
