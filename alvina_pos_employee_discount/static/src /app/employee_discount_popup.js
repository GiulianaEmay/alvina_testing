import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class EmployeeDiscountPopup extends Component {
    static template = "alvina.EmployeeDiscountPopup";
    static components = { Dialog };
    static props = { close: Function, pos: Object, order: Object };

    setup() {
        this.state = useState({ dni: "", quote: null, verified: false, busy: false, error: "" });
    }
    money(value) {
        return new Intl.NumberFormat("es-PE", { style: "currency", currency: "PEN" }).format(value);
    }
    async lookup() {
        this.state.busy = true;
        this.state.error = "";
        this.state.quote = null;
        try {
            await this.props.pos.alvinaSync(this.props.order);
            this.state.quote = await this.props.pos.data.call("pos.order", "alvina_lookup", [
                this.props.order.uuid, this.state.dni,
            ]);
        } catch (error) {
            this.state.error = error.data?.message || error.message || "No fue posible contactar al servidor.";
        } finally {
            this.state.busy = false;
        }
    }
    async apply() {
        if (!this.state.verified || this.state.busy) { return; }
        this.state.busy = true;
        try {
            const order = this.props.order;
            await this.props.pos.alvinaSync(order);
            const result = await this.props.pos.data.call("pos.order", "alvina_reserve", [
                order.uuid, this.state.dni, this.state.verified,
            ]);
            order._alvinaApplying = true;
            try {
                for (const line of order.lines) { line.setDiscount(result.effective_rate); }
                order.alvina_operation = result.operation;
                order.alvina_amount = result.amount;
                order.alvina_original_total = result.original;
                order._alvinaStale = false;
            } finally { order._alvinaApplying = false; }
            await this.props.pos.alvinaSync(order);
            this.props.close();
        } catch (error) {
            this.state.error = error.data?.message || error.message || "No se confirmó la reserva. Reintente en línea.";
        } finally { this.state.busy = false; }
    }
    async remove() {
        this.state.busy = true;
        try {
            const order = this.props.order;
            await this.props.pos.data.call("pos.order", "alvina_release", [order.uuid]);
            order._alvinaApplying = true;
            try {
                for (const line of order.lines) { line.setDiscount(0); }
                order.alvina_operation = false;
                order.alvina_amount = 0;
                order.alvina_original_total = 0;
                order._alvinaStale = false;
            } finally { order._alvinaApplying = false; }
            await this.props.pos.alvinaSync(order);
            this.props.close();
        } catch (error) {
            this.state.error = error.data?.message || error.message || "Reconecte para retirar el beneficio.";
        } finally { this.state.busy = false; }
    }
}
