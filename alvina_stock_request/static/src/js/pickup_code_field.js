/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useOwnedDialogs } from "@web/core/utils/hooks";
import { CharField, charField } from "@web/views/fields/char/char_field";
import { CameraSession, cameraErrorMessage, decodeImageFile } from "./code_reader";

export class PickupCameraDialog extends Component {
    static template = "alvina_stock_request.PickupCameraDialog";
    static components = { Dialog };
    static props = { close: Function, onResult: Function };

    setup() {
        this.video = useRef("video");
        this.state = useState({ cameras: [], cameraId: "", starting: false, error: "" });
        this.destroyed = false;
        onMounted(() => {
            this.session = new CameraSession({
                video: this.video.el,
                onResult: (text) => this.onResult(text),
                onError: (error) => {
                    if (!this.destroyed) {
                        this.state.starting = false;
                        this.state.error = cameraErrorMessage(error);
                    }
                },
                onReady: (stream) => this.onReady(stream),
            });
            this.startCamera();
        });
        onWillUnmount(() => {
            this.destroyed = true;
            this.session?.close();
        });
    }

    get title() {
        return _t("Escanear con cámara");
    }

    async startCamera() {
        this.state.error = "";
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
            this.state.error = _t("La cámara requiere HTTPS y un navegador compatible. También puede usar un lector o subir una imagen.");
            return;
        }
        this.state.starting = true;
        await this.session.start(this.state.cameraId);
        if (!this.destroyed) {
            this.state.starting = false;
        }
    }

    async onReady(stream) {
        if (this.destroyed) {
            return;
        }
        this.state.starting = false;
        this.state.cameraId = stream.getVideoTracks()[0]?.getSettings?.().deviceId || "";
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            if (!this.destroyed) {
                this.state.cameras = devices.filter((device) => device.kind === "videoinput")
                    .map((device, i) => ({ id: device.deviceId, label: device.label || _t("Cámara %s", i + 1) }));
            }
        } catch {
            // The current camera can still scan when enumeration is unavailable.
        }
    }

    async onCameraChange(event) {
        this.state.cameraId = event.target.value;
        await this.startCamera();
    }

    async onResult(text) {
        if (this.destroyed) {
            return;
        }
        try {
            await this.props.onResult(text);
            if (!this.destroyed) {
                this.props.close();
            }
        } catch {
            if (!this.destroyed) {
                this.state.error = _t("No se pudo completar el campo del código. Cierre el lector e intente nuevamente.");
            }
        }
    }

    close() {
        this.session?.close();
        this.props.close();
    }
}

export class PickupCodeField extends Component {
    static template = "alvina_stock_request.PickupCodeField";
    static components = { CharField };
    static props = CharField.props;

    setup() {
        this.addDialog = useOwnedDialogs();
        this.fileInput = useRef("imageInput");
        this.state = useState({ busy: false, error: "", message: "" });
        this.destroyed = false;
        onWillUnmount(() => { this.destroyed = true; });
    }

    /** Only the ordinary editable Char field changes; authorization stays server-side. */
    async setRecognizedCode(text) {
        if (this.destroyed || this.props.readonly) {
            return;
        }
        await this.props.record.update({ [this.props.name]: text });
        if (!this.destroyed) {
            this.state.error = "";
            this.state.message = _t("Código leído. Pulse «Revisar solicitud» para verificar la solicitud.");
        }
    }

    openCamera() {
        this.state.error = this.state.message = "";
        this.addDialog(PickupCameraDialog, { onResult: (text) => this.setRecognizedCode(text) });
    }

    selectImage() {
        this.fileInput.el.click();
    }

    async onImageSelected(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file || this.state.busy || this.props.readonly) {
            return;
        }
        this.state.busy = true;
        this.state.error = this.state.message = "";
        try {
            await this.setRecognizedCode(await decodeImageFile(file));
        } catch (error) {
            if (!this.destroyed) {
                this.state.error = error.message || _t("No se pudo leer la imagen.");
            }
        } finally {
            if (!this.destroyed) {
                this.state.busy = false;
            }
        }
    }
}

registry.category("fields").add("alvina_pickup_code", {
    ...charField,
    component: PickupCodeField,
    supportedTypes: ["char"],
});
