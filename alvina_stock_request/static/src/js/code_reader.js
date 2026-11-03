/** @odoo-module **/

import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_IMAGE_PIXELS = 40 * 1000 * 1000;
const DECODER_PATH = "/alvina_stock_request/static/lib/zxing_browser/zxing-browser.min.js";
let decoderPromise;

/** The pinned decoder is served by this Odoo instance; no CDN or image upload. */
export async function createCodeReader() {
    if (!decoderPromise) {
        decoderPromise = loadJS(DECODER_PATH).then(() => {
            const library = window.ZXingBrowser;
            if (!library?.BrowserMultiFormatReader || !library.BarcodeFormat) {
                throw new Error(_t("No se pudo cargar el lector de códigos. Intente nuevamente."));
            }
            return library;
        }).catch((error) => {
            decoderPromise = undefined;
            throw error;
        });
    }
    const library = await decoderPromise;
    // DecodeHintType.TRY_HARDER = 3 in the pinned ZXing core bundled by Browser.
    const reader = new library.BrowserMultiFormatReader(new Map([[3, true]]), {
        delayBetweenScanAttempts: 150,
        delayBetweenScanSuccess: 300,
        tryPlayVideoTimeout: 10000,
    });
    reader.possibleFormats = [library.BarcodeFormat.QR_CODE, library.BarcodeFormat.CODE_128];
    // A perfectly sharp generated QR can confuse the normal finder-pattern detector.
    // PURE_BARCODE (hint 1) retries the QR matrix directly, after normal detection.
    // It also serves camera frames containing just a code against a plain background.
    const pureQrReader = new library.BrowserQRCodeReader(new Map([[1, true]]));
    const decodeBitmap = reader.decodeBitmap.bind(reader);
    reader.decodeBitmap = (bitmap) => {
        try {
            return decodeBitmap(bitmap);
        } catch (error) {
            if (!isDecodeMiss(error)) {
                throw error;
            }
            return pureQrReader.decodeBitmap(bitmap);
        }
    };
    return reader;
}

export function cameraErrorMessage(error) {
    const messages = {
        NotAllowedError: _t("Permiso de cámara denegado. Habilítelo en el navegador o use un lector o una imagen."),
        SecurityError: _t("El navegador bloqueó la cámara. Abra Odoo mediante HTTPS y revise los permisos."),
        NotFoundError: _t("No se encontró una cámara. Use un lector o suba una imagen."),
        NotReadableError: _t("La cámara está ocupada o no puede iniciarse. Cierre otras aplicaciones que la utilicen."),
        OverconstrainedError: _t("No se pudo utilizar esa cámara. Seleccione otra e intente nuevamente."),
        AbortError: _t("Se interrumpió el inicio de la cámara. Intente nuevamente."),
    };
    return messages[error?.name] || _t("No se pudo iniciar el lector de cámara. Use un lector o suba una imagen.");
}

function stopStream(stream) {
    stream?.getTracks().forEach((track) => track.stop());
}

function isDecodeMiss(error) {
    return ["NotFoundException", "ChecksumException", "FormatException"].includes(
        error?.constructor?.kind || error?.name
    );
}

/** Own both the stream and ZXing controls, including asynchronous startup races. */
export class CameraSession {
    constructor({ video, onResult, onError, onReady, readerFactory = createCodeReader,
        mediaDevices = navigator.mediaDevices }) {
        Object.assign(this, { video, onResult, onError, onReady, readerFactory, mediaDevices });
        this.generation = 0;
        this.closed = false;
        this.stream = null;
        this.controls = null;
    }

    stop() {
        this.generation++;
        const controls = this.controls;
        const stream = this.stream;
        this.controls = this.stream = null;
        try {
            controls?.stop();
        } finally {
            stopStream(stream);
            this.video.pause();
            this.video.srcObject = null;
        }
    }

    close() {
        this.closed = true;
        this.stop();
    }

    async start(deviceId = "") {
        this.stop();
        if (this.closed) {
            return false;
        }
        const generation = this.generation;
        const isCurrent = () => !this.closed && generation === this.generation;
        let stream;
        try {
            const reader = await this.readerFactory();
            if (!isCurrent()) {
                return false;
            }
            stream = await this.mediaDevices.getUserMedia({
                audio: false,
                video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "environment" } },
            });
            if (!isCurrent()) {
                stopStream(stream);
                return false;
            }
            this.stream = stream;
            const controls = await reader.decodeFromStream(stream, this.video, (result, error, controls) => {
                if (!isCurrent()) {
                    controls?.stop();
                    return;
                }
                if (result) {
                    this.controls = controls;
                    const text = result.getText();
                    this.stop();
                    this.onResult(text);
                } else if (error && !isDecodeMiss(error)) {
                    this.controls = controls;
                    this.stop();
                    this.onError(error);
                }
            });
            if (!isCurrent()) {
                controls.stop();
                stopStream(stream);
                return false;
            }
            this.controls = controls;
            this.onReady?.(stream);
            return true;
        } catch (error) {
            stopStream(stream);
            if (isCurrent()) {
                this.stop();
                this.onError(error);
            }
            return false;
        }
    }
}

/** Reject unsupported or mislabeled images before allocating the full bitmap. */
export async function validateImageFile(file) {
    if (!file || !file.size || file.size > MAX_IMAGE_BYTES) {
        throw new Error(_t("Seleccione una imagen de hasta 5 MiB."));
    }
    const extensions = { "image/png": /\.png$/i, "image/jpeg": /\.jpe?g$/i, "image/webp": /\.webp$/i };
    if (!extensions[file.type]?.test(file.name)) {
        throw new Error(_t("Solo se admiten imágenes PNG, JPG, JPEG o WebP con el formato correcto."));
    }
    const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
    const png = [137, 80, 78, 71, 13, 10, 26, 10].every((byte, i) => bytes[i] === byte);
    const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    const webp = String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" &&
        String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
    if (!(file.type === "image/png" && png || file.type === "image/jpeg" && jpeg ||
        file.type === "image/webp" && webp)) {
        throw new Error(_t("La imagen no coincide con su formato o está dañada."));
    }
}

async function imageElement(file) {
    const url = URL.createObjectURL(file);
    try {
        const image = new Image();
        image.src = url;
        await image.decode();
        return image;
    } finally {
        URL.revokeObjectURL(url);
    }
}

/** Return only the decoded text. The File/bitmap/canvas never enter an Odoo record. */
export async function decodeImageFile(file) {
    await validateImageFile(file);
    let image;
    try {
        image = typeof createImageBitmap === "function" ? await createImageBitmap(file) : await imageElement(file);
        const width = image.width || image.naturalWidth;
        const height = image.height || image.naturalHeight;
        if (!width || !height || width * height > MAX_IMAGE_PIXELS) {
            throw new Error(_t("La imagen tiene dimensiones excesivas. Use una captura más pequeña."));
        }
        const canvas = document.createElement("canvas");
        const scale = Math.min(1, 4096 / Math.max(width, height));
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const reader = await createCodeReader();
        return reader.decodeFromCanvas(canvas).getText();
    } catch (error) {
        if (isDecodeMiss(error)) {
            throw new Error(_t("No se encontró un QR o un código Code 128 legible. Use una imagen nítida que muestre el código completo."));
        }
        throw new Error(_t("No se pudo leer la imagen. Verifique su formato, nitidez y tamaño."));
    } finally {
        image?.close?.();
    }
}
