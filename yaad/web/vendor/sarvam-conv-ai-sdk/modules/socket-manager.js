/**
 * Base WebSocket manager for conversational AI agents.
 *
 * This module provides the base SocketManager class with common WebSocket
 * connection management, message routing, and event handling logic.
 */
import { AgentState, RateLimitError, AuthenticationError, ForbiddenError, NotFoundError, ServerError, } from "../types";
import { createStartInteractionMessageFromConfig, parseServerMessage, } from "../utils/message";
import { ClientMsgType, MsgOrigin, ServerMsgType } from "../types/types";
import { getWebSocket } from "../utils/socket";
export class SocketManager {
    apiKey;
    config;
    baseUrl;
    interactionId;
    referenceId;
    customHeaders;
    // Callbacks
    eventCallback;
    textCallback;
    transcriptCallback;
    startCallback;
    endCallback;
    stateCallback;
    telemetryCallback;
    // Internal state
    ws; // WebSocket instance (type varies by environment)
    shouldStop = false;
    websocketSendQueue = [];
    disconnectedEvent;
    connectedEvent;
    // Agent state tracking
    currentState = AgentState.IDLE;
    // Telemetry timing
    sessionStartTime;
    wsConnectStartTime;
    signedUrlStartTime;
    firstAudioSent = false;
    firstAudioReceived = false;
    // Track who initiated session end: "user" | "agent" | "network" | "error"
    sessionEndInitiator;
    // Network monitoring
    offlineStartTime;
    boundOfflineHandler;
    boundOnlineHandler;
    constructor(options) {
        if (!options.config) {
            throw new Error("config must be provided");
        }
        this.apiKey = options.apiKey;
        this.config = options.config;
        this.baseUrl = options.baseUrl || "https://apps.sarvam.ai/api/app-runtime/";
        this.eventCallback = options.eventCallback;
        this.textCallback = options.textCallback;
        this.transcriptCallback = options.transcriptCallback;
        this.startCallback = options.startCallback;
        this.endCallback = options.endCallback;
        this.stateCallback = options.stateCallback;
        this.telemetryCallback = options.telemetryCallback;
        this.customHeaders = options.customHeaders;
    }
    /**
     * Emit a telemetry event to the consumer's callback.
     * No-op if telemetryCallback is not provided.
     */
    emitTelemetry(name, properties) {
        if (!this.telemetryCallback)
            return;
        const event = {
            name,
            timestamp: Date.now(),
            sessionId: this.referenceId,
            interactionId: this.interactionId,
            properties,
        };
        try {
            this.telemetryCallback(event);
        }
        catch (error) {
            console.error("[SDK] Error in telemetryCallback:", error);
        }
    }
    /**
     * Get the reference ID (call SID)
     */
    get reference_id() {
        if (!this.referenceId) {
            throw new Error("Reference ID is not set");
        }
        return this.referenceId;
    }
    set reference_id(referenceId) {
        this.referenceId = referenceId;
    }
    /**
     * Check if the WebSocket connection is closed
     */
    isWsClosed() {
        // WebSocket.CLOSED = 3 (standard for both browser and Node.js)
        const CLOSED = 3;
        return !this.ws || this.ws.readyState === CLOSED;
    }
    /**
     * Start the conversation session.
     *
     * This method:
     * 1. Gets a signed WebSocket URL via HTTP GET
     * 2. Connects to the WebSocket
     * 3. Sends the interaction_start message
     * 4. Starts the message receive loop
     */
    async start() {
        // Reset telemetry state for new session
        this.sessionStartTime = Date.now();
        this.firstAudioSent = false;
        this.firstAudioReceived = false;
        // Start monitoring network status
        this.startNetworkMonitoring();
        // Transition to CONNECTING state
        this.setState(AgentState.CONNECTING);
        this.emitTelemetry("session_started", {});
        // Get signed URL for secure WebSocket connection
        const { signedUrl, referenceId } = await this.getSignedUrl();
        this.reference_id = referenceId;
        console.log("[SDK] Got signed URL:", signedUrl);
        this.shouldStop = false;
        this.disconnectedEvent = this.createEvent();
        this.connectedEvent = this.createEvent();
        const wsUrl = this.augmentWsUrlWithParams(signedUrl);
        console.log("[SDK] Final WebSocket URL:", wsUrl);
        this.emitTelemetry("ws_connecting", {});
        this.wsConnectStartTime = Date.now();
        // Start WebSocket loop in background (don't await - it only resolves when connection closes)
        this.runWebSocketLoop(wsUrl);
        if (this.startCallback) {
            await this.startCallback();
        }
    }
    /**
     * Stop the conversation session.
     *
     * This method:
     * - Closes the WebSocket connection
     * - Cancels background tasks
     * - Cleans up resources
     */
    async stop() {
        // Mark as user-initiated stop (unless already set by agent/error)
        if (!this.sessionEndInitiator) {
            this.sessionEndInitiator = "user";
        }
        this.shouldStop = true;
        // Stop network monitoring
        this.stopNetworkMonitoring();
        // Allow subclasses to perform cleanup
        await this.onStop();
        if (!this.isWsClosed() && this.ws) {
            this.ws.close();
        }
        // Signal disconnected
        if (this.disconnectedEvent && !this.disconnectedEvent.promise) {
            this.disconnectedEvent.resolve();
        }
        // Transition to IDLE state
        this.setState(AgentState.IDLE);
        if (this.endCallback) {
            await this.endCallback();
        }
    }
    /**
     * Hook for subclasses to perform cleanup on stop
     */
    async onStop() {
        // Override in subclasses
    }
    /**
     * Wait until the WebSocket disconnects or the agent is stopped
     */
    async waitForDisconnect() {
        if (!this.disconnectedEvent) {
            return;
        }
        await this.disconnectedEvent.promise;
    }
    /**
     * Wait until the WebSocket connection is established.
     *
     * @param timeout - Maximum seconds to wait. If null, wait indefinitely.
     * @returns True if the connection is established within the timeout, false otherwise.
     */
    async waitForConnect(timeout) {
        // If already connected, return immediately
        if (this.isConnected()) {
            return true;
        }
        if (!this.connectedEvent) {
            this.connectedEvent = this.createEvent();
        }
        try {
            if (timeout !== undefined) {
                await Promise.race([
                    this.connectedEvent.promise,
                    new Promise((_, reject) => setTimeout(() => reject(new Error("Timeout")), timeout * 1000)),
                ]);
            }
            else {
                await this.connectedEvent.promise;
            }
        }
        catch {
            return this.isConnected();
        }
        return this.isConnected();
    }
    /**
     * Check if the WebSocket is currently connected.
     *
     * @returns True if connected, false otherwise
     */
    isConnected() {
        return !this.isWsClosed();
    }
    /**
     * Get the current interaction identifier for clients.
     *
     * In this SDK, the server associates the interaction with a server-side
     * call session identifier. We expose the same value for client reference.
     */
    getInteractionId() {
        return this.interactionId;
    }
    /**
     * Get the current agent state
     */
    getState() {
        return this.currentState;
    }
    /**
     * Set the agent state and notify callback if changed
     */
    setState(newState) {
        if (this.currentState === newState) {
            return;
        }
        const previousState = this.currentState;
        this.currentState = newState;
        this.emitTelemetry("state_changed", {
            from: previousState,
            to: newState,
        });
        if (this.stateCallback) {
            this.stateCallback(newState, previousState);
        }
    }
    /**
     * Create a promise-based event
     */
    createEvent() {
        let resolve;
        const promise = new Promise((res) => {
            resolve = res;
        });
        return { resolve, promise };
    }
    /**
     * Construct the URL for getting signed WebSocket URL
     */
    constructUrl() {
        return `${this.baseUrl}orgs/${this.config.org_id}/workspaces/${this.config.workspace_id}/apps/${this.config.app_id}/url`;
    }
    /**
     * Helper to add query parameters to a URL string (React Native compatible)
     */
    addQueryParam(urlStr, key, value) {
        const separator = urlStr.includes("?") ? "&" : "?";
        return `${urlStr}${separator}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
    }
    /**
     * Add common user identifier params to URL string
     */
    addUserIdentifierParamsToString(urlStr) {
        let result = this.addQueryParam(urlStr, "user_identifier", this.config.user_identifier);
        result = this.addQueryParam(result, "user_identifier_type", this.config.user_identifier_type);
        return result;
    }
    /**
     * Append required query params for the WebSocket connect URL
     * Note: user_identifier params are already added in getSignedUrl()
     */
    augmentWsUrlWithParams(wsUrl) {
        return this.addQueryParam(wsUrl, "interaction_type", this.config.interaction_type);
    }
    /**
     * Get authenticated WebSocket URL and reference id from the API.
     *
     * Makes an HTTP GET request to get a time-limited signed URL for
     * secure WebSocket connections without exposing the API key.
     *
     * @returns A tuple of (signed_websocket_url, reference_id)
     * @throws Error if the HTTP request fails
     */
    async getSignedUrl() {
        this.signedUrlStartTime = Date.now();
        this.emitTelemetry("signed_url_requested", {});
        let requestUrl = this.constructUrl();
        if (this.config.interaction_type) {
            requestUrl = this.addQueryParam(requestUrl, "interaction_type", this.config.interaction_type);
        }
        if (this.config.version) {
            requestUrl = this.addQueryParam(requestUrl, "version", String(this.config.version));
        }
        console.log("[SDK] Fetching signed URL from:", requestUrl);
        // Build headers with CORS bypass for localhost when proxy is enabled
        const headers = {
            "X-API-Key": this.apiKey,
        };
        // Allow subclasses to add custom headers
        this.addCustomHeaders(headers);
        const fetchOptions = {
            method: "GET",
            headers,
        };
        const response = await fetch(requestUrl, fetchOptions);
        if (!response.ok) {
            const errorText = await response.text().catch(() => "");
            const durationMs = Date.now() - (this.signedUrlStartTime || Date.now());
            // Emit telemetry for failed signed URL request
            this.emitTelemetry("signed_url_failed", {
                error: `${response.status} ${errorText}`,
                durationMs,
            });
            // Handle specific HTTP status codes with typed errors
            switch (response.status) {
                case 429: {
                    // Rate limited - parse Retry-After header if available
                    const retryAfterHeader = response.headers.get("x-retry-after");
                    const retryAfter = retryAfterHeader
                        ? parseInt(retryAfterHeader, 10)
                        : 60;
                    throw new RateLimitError(`Rate limit exceeded. ${errorText || "Please try again later."}`, isNaN(retryAfter) ? 60 : retryAfter);
                }
                case 401:
                    throw new AuthenticationError(`Authentication failed: ${errorText || "Invalid or missing API key"}`);
                case 403:
                    throw new ForbiddenError(`Access forbidden: ${errorText || "Insufficient permissions"}`);
                case 404:
                    throw new NotFoundError(`Resource not found: ${errorText || "The specified app, workspace, or organization was not found"}`);
                case 500:
                case 502:
                case 503:
                case 504:
                    throw new ServerError(`Server error: ${errorText || "Internal server error"}`, response.status);
                default:
                    throw new Error(`Failed to get signed URL: ${response.status} ${errorText}`);
            }
        }
        let data;
        try {
            const responseText = await response.text();
            data = responseText
                ? JSON.parse(responseText)
                : null;
        }
        catch (error) {
            throw new Error(`Failed to parse JSON response: ${error instanceof Error ? error.message : String(error)}`);
        }
        if (!data || !data.url || !data.reference_id) {
            const versionHint = this.config.version
                ? `Version ${this.config.version} is specified.`
                : `No version specified. Using latest committed version.`;
            throw new Error(`Invalid response from API: ${JSON.stringify(data)}. ` +
                `Expected object with 'url' and 'reference_id' properties. ` +
                `${versionHint} ` +
                `This may indicate the app has no committed version or the app_id/org_id/workspace_id is incorrect.`);
        }
        const signedUrl = data.url;
        const referenceId = data.reference_id;
        // Emit telemetry for successful signed URL request
        const durationMs = Date.now() - (this.signedUrlStartTime || Date.now());
        this.emitTelemetry("signed_url_received", { durationMs });
        // Add user identifier params to the signed URL
        let modifiedUrl = this.addUserIdentifierParamsToString(signedUrl);
        // Allow subclasses to modify the URL
        modifiedUrl = this.modifySignedUrlString(modifiedUrl);
        return {
            signedUrl: modifiedUrl,
            referenceId,
        };
    }
    /**
     * Add custom headers to API requests.
     * Merges any custom headers provided in constructor options.
     */
    addCustomHeaders(headers) {
        if (this.customHeaders) {
            Object.assign(headers, this.customHeaders);
        }
    }
    /**
     * Hook for subclasses to modify the signed URL
     */
    modifySignedUrlString(url) {
        // Override in subclasses
        return url;
    }
    /**
     * Flush send queue to WebSocket
     */
    async flushSendQueue() {
        // WebSocket.OPEN = 1 (standard for both browser and Node.js)
        const OPEN = 1;
        if (this.isWsClosed() || !this.ws || this.ws.readyState !== OPEN) {
            return;
        }
        while (this.websocketSendQueue.length > 0) {
            const message = this.websocketSendQueue.shift();
            const messageJson = JSON.stringify(message);
            this.ws.send(messageJson);
        }
    }
    /**
     * Check if we're running in a React Native environment.
     */
    isReactNative() {
        return (typeof navigator !== "undefined" && navigator.product === "ReactNative");
    }
    /**
     * Start listening to network online/offline events.
     * Works in browser environments only (React Native should use NetInfo).
     */
    startNetworkMonitoring() {
        // Only monitor in browser environments with proper window.addEventListener support
        if (typeof window === "undefined" ||
            typeof window.addEventListener !== "function") {
            return;
        }
        this.boundOfflineHandler = () => {
            this.offlineStartTime = Date.now();
            this.sessionEndInitiator = "network";
            this.emitTelemetry("network_offline", {});
        };
        this.boundOnlineHandler = () => {
            const offlineDurationMs = this.offlineStartTime
                ? Date.now() - this.offlineStartTime
                : undefined;
            this.offlineStartTime = undefined;
            this.emitTelemetry("network_online", { offlineDurationMs });
        };
        window.addEventListener("offline", this.boundOfflineHandler);
        window.addEventListener("online", this.boundOnlineHandler);
    }
    /**
     * Stop listening to network events and clean up.
     */
    stopNetworkMonitoring() {
        if (typeof window === "undefined" ||
            typeof window.removeEventListener !== "function") {
            return;
        }
        if (this.boundOfflineHandler) {
            window.removeEventListener("offline", this.boundOfflineHandler);
            this.boundOfflineHandler = undefined;
        }
        if (this.boundOnlineHandler) {
            window.removeEventListener("online", this.boundOnlineHandler);
            this.boundOnlineHandler = undefined;
        }
        this.offlineStartTime = undefined;
    }
    /**
     * Main WebSocket loop for receiving and processing messages
     */
    async runWebSocketLoop(wsUrl) {
        return new Promise((resolve, reject) => {
            const WebSocketClass = getWebSocket();
            // Detect environment
            const isRN = this.isReactNative();
            const isBrowser = !isRN &&
                typeof window !== "undefined" &&
                typeof window.WebSocket !== "undefined";
            const wsOptions = {};
            // In browser/RN, don't pass empty wsOptions as it causes WebSocket subprotocol error
            // Only pass wsOptions if it has content (Node.js environment)
            const ws = Object.keys(wsOptions).length > 0
                ? new WebSocketClass(wsUrl, wsOptions)
                : new WebSocketClass(wsUrl);
            this.ws = ws;
            // WebSocket.OPEN = 1 (standard for both browser and Node.js)
            const OPEN = 1;
            if (isBrowser || isRN) {
                // Browser and React Native WebSocket API
                ws.addEventListener("open", async () => {
                    try {
                        // Emit ws_connected telemetry
                        const connectDurationMs = Date.now() - (this.wsConnectStartTime || Date.now());
                        this.emitTelemetry("ws_connected", {
                            durationMs: connectDurationMs,
                        });
                        // Send interaction start
                        await this.sendInteractionStart();
                        // Allow subclasses to perform post-connection setup
                        await this.onWebSocketOpen();
                        // Start sending messages from queue
                        this.flushSendQueue();
                        setInterval(() => this.flushSendQueue(), 100);
                        // Note: connectedEvent will be resolved when we receive INTERACTION_CONNECTED
                    }
                    catch (error) {
                        reject(error);
                    }
                });
                ws.addEventListener("message", async (event) => {
                    try {
                        const messageStr = typeof event.data === "string"
                            ? event.data
                            : event.data.toString();
                        const message = JSON.parse(messageStr);
                        await this.routeMessage(message);
                    }
                    catch (error) {
                        console.error("Error processing WebSocket message:", error);
                        this.emitTelemetry("error", {
                            type: "message_processing",
                            message: error instanceof Error ? error.message : String(error),
                        });
                    }
                });
                ws.addEventListener("error", (error) => {
                    console.error("WebSocket error:", error);
                    // In React Native, the error event may contain more details
                    const errorMessage = isRN && error.message
                        ? error.message
                        : "WebSocket connection error";
                    if (isRN && error.message) {
                        console.error("WebSocket error message from React Native:", error.message);
                    }
                    this.sessionEndInitiator = "error";
                    this.emitTelemetry("ws_error", { error: errorMessage });
                    this.setState(AgentState.ERROR);
                    reject(new Error("WebSocket connection error"));
                });
                ws.addEventListener("close", (event) => {
                    const sessionDurationMs = this.sessionStartTime
                        ? Date.now() - this.sessionStartTime
                        : 0;
                    // Determine end initiator if not already set
                    const initiator = this.sessionEndInitiator || "network";
                    this.emitTelemetry("ws_disconnected", {
                        code: event.code,
                        reason: event.reason || undefined,
                        wasClean: event.wasClean,
                    });
                    this.emitTelemetry("session_ended", {
                        durationMs: sessionDurationMs,
                        initiatedBy: initiator,
                        reason: event.reason ||
                            (event.wasClean ? "clean_close" : "connection_lost"),
                    });
                    // Cleanup
                    this.stopNetworkMonitoring();
                    this.sessionEndInitiator = undefined;
                    this.ws = undefined;
                    this.shouldStop = true;
                    if (this.disconnectedEvent) {
                        this.disconnectedEvent.resolve();
                    }
                    resolve();
                });
            }
            else {
                // Node.js ws library
                ws.on("open", async () => {
                    try {
                        // Emit ws_connected telemetry
                        const connectDurationMs = Date.now() - (this.wsConnectStartTime || Date.now());
                        this.emitTelemetry("ws_connected", {
                            durationMs: connectDurationMs,
                        });
                        // Send interaction start
                        await this.sendInteractionStart();
                        // Allow subclasses to perform post-connection setup
                        await this.onWebSocketOpen();
                        // Start sending messages from queue
                        this.flushSendQueue();
                        setInterval(() => this.flushSendQueue(), 100);
                        // Note: connectedEvent will be resolved when we receive INTERACTION_CONNECTED
                    }
                    catch (error) {
                        reject(error);
                    }
                });
                ws.on("message", async (data) => {
                    try {
                        const messageStr = data.toString();
                        const message = JSON.parse(messageStr);
                        await this.routeMessage(message);
                    }
                    catch (error) {
                        console.error("Error processing WebSocket message:", error);
                        this.emitTelemetry("error", {
                            type: "message_processing",
                            message: error instanceof Error ? error.message : String(error),
                        });
                    }
                });
                ws.on("error", (error) => {
                    console.error("WebSocket error:", error);
                    this.sessionEndInitiator = "error";
                    this.emitTelemetry("ws_error", { error: error.message });
                    this.setState(AgentState.ERROR);
                    reject(error);
                });
                ws.on("close", (code, reason) => {
                    const sessionDurationMs = this.sessionStartTime
                        ? Date.now() - this.sessionStartTime
                        : 0;
                    // Determine end initiator if not already set
                    const initiator = this.sessionEndInitiator || "network";
                    const reasonStr = reason?.toString() || undefined;
                    this.emitTelemetry("ws_disconnected", {
                        code,
                        reason: reasonStr,
                    });
                    this.emitTelemetry("session_ended", {
                        durationMs: sessionDurationMs,
                        initiatedBy: initiator,
                        reason: reasonStr || "connection_closed",
                    });
                    // Cleanup
                    this.stopNetworkMonitoring();
                    this.sessionEndInitiator = undefined;
                    this.ws = undefined;
                    this.shouldStop = true;
                    if (this.disconnectedEvent) {
                        this.disconnectedEvent.resolve();
                    }
                    resolve();
                });
            }
        });
    }
    /**
     * Hook for subclasses to perform actions after WebSocket opens
     */
    async onWebSocketOpen() {
        // Override in subclasses
    }
    /**
     * Send the interaction start message
     */
    async sendInteractionStart() {
        if (!this.ws) {
            throw new Error("WebSocket not connected");
        }
        const message = createStartInteractionMessageFromConfig(this.config);
        if (!this.isWsClosed()) {
            const messageJson = JSON.stringify(message);
            this.ws.send(messageJson);
        }
    }
    /**
     * Route incoming messages to appropriate handlers
     */
    async routeMessage(message) {
        try {
            const parsed = parseServerMessage(message);
            // Debug: Log all incoming message types
            if (parsed.type === ServerMsgType.TEXT) {
                await this.handleText(parsed);
            }
            else if (parsed.type === ServerMsgType.TRANSCRIPTION) {
                await this.handleTranscript(parsed);
            }
            else if (parsed.type === ServerMsgType.INTERACTION_END) {
                await this.handleInteractionEnd(parsed);
            }
            else if (parsed.type === ServerMsgType.PING) {
                await this.handlePing(parsed);
            }
            else if (parsed.type === ServerMsgType.INTERACTION_CONNECTED) {
                await this.handleInteractionStartAcknowledgement(parsed);
            }
            else {
                // Allow subclasses to handle additional message types
                await this.handleCustomMessage(parsed);
            }
        }
        catch (error) {
            console.error("Failed to parse server message:", error);
            console.error("Raw message:", message);
            this.emitTelemetry("error", {
                type: "message_parse",
                message: error instanceof Error ? error.message : String(error),
            });
        }
    }
    /**
     * Hook for subclasses to handle custom message types
     */
    async handleCustomMessage(message) {
        await this.handleEvent(message);
    }
    /**
     * Handle text message from the agent
     */
    async handleText(textMsg) {
        try {
            if (this.textCallback) {
                await this.textCallback(textMsg);
            }
        }
        catch (error) {
            console.error("Error handling text message:", error);
        }
    }
    /**
     * Handle transcript message from the agent
     */
    async handleTranscript(transcriptMsg) {
        try {
            if (this.transcriptCallback) {
                await this.transcriptCallback(transcriptMsg);
            }
        }
        catch (error) {
            console.error("Error handling transcript message:", error);
        }
    }
    /**
     * Handle event message from the agent
     */
    async handleEvent(event) {
        if (this.eventCallback) {
            await this.eventCallback(event);
        }
    }
    /**
     * Handle interaction connected event from server
     */
    async handleInteractionStartAcknowledgement(event) {
        this.interactionId = event.interaction_id;
        this.emitTelemetry("interaction_connected", {
            interactionId: event.interaction_id,
        });
        // Transition to CONNECTED, then LISTENING (agent starts by listening)
        this.setState(AgentState.CONNECTED);
        this.setState(AgentState.LISTENING);
        // Consider the session ready; resolve connected event
        if (this.connectedEvent) {
            this.connectedEvent.resolve();
        }
        else {
            console.warn("[SDK] connectedEvent was not set!");
        }
    }
    /**
     * Handle interaction end message
     */
    async handleInteractionEnd(_endMsg) {
        // Mark as agent/server-initiated end
        this.sessionEndInitiator = "agent";
        // Transition to IDLE state
        this.setState(AgentState.IDLE);
        if (this.endCallback) {
            await this.endCallback();
        }
    }
    /**
     * Handle ping message and send pong response
     */
    async handlePing(pingMsg) {
        try {
            if (!this.isWsClosed() && this.ws) {
                const pong = {
                    type: ClientMsgType.PONG,
                    origin: MsgOrigin.CLIENT,
                    timestamp: Date.now() / 1000,
                    event_id: pingMsg.event_id,
                };
                const pongJson = JSON.stringify(pong);
                this.ws.send(pongJson);
            }
        }
        catch (error) {
            console.error("Error handling ping:", error);
        }
    }
}
//# sourceMappingURL=socket-manager.js.map