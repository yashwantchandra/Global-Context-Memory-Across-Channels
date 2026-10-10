/**
 * Check if we're running in a React Native environment.
 */
function isReactNative() {
    return (typeof navigator !== "undefined" &&
        navigator.product === "ReactNative");
}
/**
 * Get the appropriate WebSocket implementation based on the environment.
 * Uses native WebSocket in browsers and React Native, ws library in Node.js.
 *
 * Note: The 'browser' field in package.json maps 'ws' to false,
 * ensuring bundlers exclude ws from browser builds.
 */
export function getWebSocket() {
    // React Native environment - use global WebSocket
    // RN provides a WebSocket polyfill that works with the native network layer
    if (isReactNative()) {
        if (typeof global !== "undefined" && global.WebSocket) {
            return global.WebSocket;
        }
        // Fallback to window.WebSocket in RN
        if (typeof window !== "undefined" && window.WebSocket) {
            return window.WebSocket;
        }
    }
    // Browser environment - use native WebSocket
    if (typeof window !== "undefined" &&
        typeof window.WebSocket !== "undefined") {
        return window.WebSocket;
    }
    // Node.js environment - use ws library
    if (typeof require !== "undefined" && typeof process !== "undefined") {
        try {
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const ws = require("ws");
            return ws;
        }
        catch (error) {
            throw new Error("WebSocket is not available. In Node.js, please install ws package: npm install ws");
        }
    }
    throw new Error("WebSocket is not available. Please ensure you're in a supported environment.");
}
//# sourceMappingURL=socket.js.map