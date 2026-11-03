import { Mutex } from "@web/core/utils/concurrency";
import { patch } from "@web/core/utils/patch";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

patch(PosOrder.prototype, {
    get finalized() {
        if (this.alvina_operation && this.state === "paid" && !this.alvina_confirmed) {
            return false; // Keep the recovery payment screen reachable after reload.
        }
        return super.finalized;
    },
    assertEditable() {
        if (this.alvina_operation && this.state === "paid" && !this.alvina_confirmed) {
            throw new Error("El pago está pendiente de confirmación. Reconecte y pulse Validar antes de editar.");
        }
        return super.assertEditable(...arguments);
    },
    get canBeRemovedFromIndexedDB() {
        // Authorization sync gives drafts a numeric ID before the payment RPC.
        // A numeric ID therefore does not prove the payment reached the server.
        if (this.alvina_operation && this.state === "paid" && !this.alvina_confirmed) {
            return false;
        }
        return super.canBeRemovedFromIndexedDB;
    },
    get isUnsyncedPaid() {
        if (this.alvina_operation && this.state === "paid" && !this.alvina_confirmed) {
            return true;
        }
        return super.isUnsyncedPaid;
    },
    alvinaInvalidate() {
        if (!this.alvina_operation || this._alvinaApplying || this.state !== "draft") { return; }
        this._alvinaApplying = true;
        try {
            for (const line of this.lines) { line.discount = 0; }
            this.alvina_amount = 0;
            this._alvinaStale = true;
        } finally { this._alvinaApplying = false; }
    },
    removeOrderline(line) {
        this.alvinaInvalidate();
        return super.removeOrderline(...arguments);
    },
    setPartner() {
        if (this.alvina_operation && this.state === 'paid' && !this.alvina_confirmed) { this.assertEditable(); }
        this.alvinaInvalidate();
        return super.setPartner(...arguments);
    },
    setPricelist() {
        if (this.alvina_operation && this.state === 'paid' && !this.alvina_confirmed) { this.assertEditable(); }
        this.alvinaInvalidate();
        return super.setPricelist(...arguments);
    },
});
patch(PosOrderline.prototype, {
    setQuantity() {
        if (this.order_id?.alvina_operation && this.order_id.state === 'paid' && !this.order_id.alvina_confirmed) { this.order_id.assertEditable(); }
        this.order_id?.alvinaInvalidate();
        return super.setQuantity(...arguments);
    },
    setUnitPrice() {
        // setOptions/setup may normalize the same price; only real edits invalidate.
        if (Number(arguments[0]) !== this.price_unit) {
            if (this.order_id?.alvina_operation && this.order_id.state === 'paid' && !this.order_id.alvina_confirmed) { this.order_id.assertEditable(); }
            this.order_id?.alvinaInvalidate();
        }
        return super.setUnitPrice(...arguments);
    },
    setDiscount() {
        if (this.order_id?.alvina_operation && !this.order_id._alvinaApplying) {
            this.order_id.alvinaInvalidate();
            return; // A cashier cannot replace the server-assigned benefit rate.
        }
        return super.setDiscount(...arguments);
    },
});
patch(PosStore.prototype, {
    async syncAllOrders() {
        // Also serialize background sync: otherwise authorization can mistake an
        // already-in-flight draft sync for an empty/failed synchronization.
        this._alvinaSyncMutex ||= new Mutex();
        const args = arguments;
        return await this._alvinaSyncMutex.exec(() => super.syncAllOrders(...args));
    },
    async alvinaSync(order) {
        if (this.data.network.offline) { throw new Error("Se requiere conexión con el servidor."); }
        this.addPendingOrder([order.id]);
        const result = await this.syncAllOrders({ orders: [order], force: true, throw: true });
        if (!result?.length) { throw new Error("El pedido aún no se sincronizó. Reintente."); }
    },
    async addLineToOrder(vals, order) {
        order.alvinaInvalidate();
        return await super.addLineToOrder(...arguments);
    },
});
patch(OrderPaymentValidation.prototype, {
    async validateOrder() {
        if (this.order.alvina_operation) {
            try {
                this.pos.numberBuffer.capture();
                // Always resolve by UUID, including a draft restored from an
                // older IndexedDB snapshot after the payment response was lost.
                const status = await this.pos.data.call("pos.order", "alvina_status", [this.order.uuid]);
                if (status.confirmed) {
                    await this.pos.data.read("pos.order", [status.id]);
                    this.order.alvina_confirmed = true;
                    this.pos.data.localUnsyncedPaidOrderUuids.delete(this.order.uuid);
                    this.pos.removePendingOrder(this.order);
                    await this.pos.data.synchronizeLocalDataInIndexedDB();
                    this.pos.navigate("ReceiptScreen", { orderUuid: this.order.uuid });
                    return true;
                }
                if (status.state && status.state !== "draft") {
                    throw new Error("El servidor no permite volver a cobrar este pedido. Consulte su estado en Pedidos.");
                }
                if (this.order.state === "paid") {
                    this.order.state = "draft";
                    this.pos.data.localUnsyncedPaidOrderUuids.delete(this.order.uuid);
                    await this.pos.data.synchronizeLocalDataInIndexedDB();
                }
                if (this.order._alvinaStale || !this.order.alvina_amount) {
                    throw new Error("La compra cambió. Vuelva a aplicar o retire el beneficio.");
                }
                await this.pos.alvinaSync(this.order);
                await this.pos.data.call("pos.order", "alvina_validate", [this.order.uuid]);
            } catch (error) {
                this.pos.dialog.add(AlertDialog, {
                    title: "Beneficio pendiente de validación",
                    body: error.data?.message || error.message || "Reconecte antes de finalizar.",
                });
                return false;
            }
        }
        return await super.validateOrder(...arguments);
    },
    async shouldHideValidationBehindFeedbackScreen() {
        if (!this.order.alvina_operation) {
            return await super.shouldHideValidationBehindFeedbackScreen(...arguments);
        }
        this.pos.env.services.ui.block();
        try {
            // Never navigate/print on a ConnectionLostError, even with fast payment.
            await this.finalizeValidation();
            const status = await this.pos.data.call("pos.order", "alvina_status", [this.order.uuid]);
            if (!status.confirmed) { throw new Error("El servidor no confirmó el consumo."); }
            this.pos.navigate("ReceiptScreen", { orderUuid: this.order.uuid });
        } catch (error) {
            this.pos.dialog.add(AlertDialog, {
                title: "Venta sin confirmación",
                body: "No vuelva a cobrar. Reconecte y pulse Validar para consultar el estado. " +
                    (error.data?.message || error.message || ""),
            });
        } finally { this.pos.env.services.ui.unblock(); }
    },
    handleValidationError(error) {
        if (this.order.alvina_operation) {
            // Keep products and payments in IndexedDB immediately. The standard
            // offline handler persists too, but would print an unconfirmed sale.
            return Promise.resolve(this.pos.data.synchronizeLocalDataInIndexedDB()).then(() => { throw error; });
        }
        return super.handleValidationError(...arguments);
    },
});
