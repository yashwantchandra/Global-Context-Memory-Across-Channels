/**
 * Decode base64 string to Uint8Array
 * Works in both browser, Node.js, and React Native
 */
export function base64ToUint8Array(base64) {
    if (typeof Buffer !== "undefined" && Buffer.from) {
        // Node.js / React Native environment
        // IMPORTANT: Buffer uses a shared pool, so we must copy to a fresh ArrayBuffer
        // Otherwise, the .buffer property will point to the entire shared pool
        const buffer = Buffer.from(base64, "base64");
        // Create a fresh Uint8Array with its own ArrayBuffer
        const freshArray = new Uint8Array(buffer.length);
        for (let i = 0; i < buffer.length; i++) {
            freshArray[i] = buffer[i];
        }
        return freshArray;
    }
    else {
        // Browser environment - use atob
        const binaryString = atob(base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        return bytes;
    }
}
/**
 * Encode Uint8Array to base64 string
 * Works in both browser and Node.js
 */
export function uint8ArrayToBase64(bytes) {
    if (typeof Buffer !== "undefined" &&
        Buffer.isBuffer &&
        Buffer.isBuffer(bytes)) {
        // Node.js environment with Buffer
        return bytes.toString("base64");
    }
    // Convert to Uint8Array if needed
    let uint8Array;
    if (bytes instanceof ArrayBuffer) {
        uint8Array = new Uint8Array(bytes);
    }
    else if (bytes instanceof Uint8Array) {
        uint8Array = bytes;
    }
    else {
        // Fallback: try to convert
        uint8Array = new Uint8Array(bytes);
    }
    if (typeof Buffer !== "undefined" && Buffer.from) {
        // Node.js environment - use Buffer
        return Buffer.from(uint8Array).toString("base64");
    }
    else {
        // Browser environment - use btoa
        let binary = "";
        for (let i = 0; i < uint8Array.length; i++) {
            binary += String.fromCharCode(uint8Array[i]);
        }
        return btoa(binary);
    }
}
//# sourceMappingURL=buffer.js.map