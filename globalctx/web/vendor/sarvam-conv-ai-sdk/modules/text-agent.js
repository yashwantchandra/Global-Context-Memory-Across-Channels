/**
 * Text-based conversational AI agent implementation.
 *
 * This module provides the TextAgent class for managing real-time
 * text conversations using WebSocket connections.
 */
import { SocketManager } from "./socket-manager";
import { ClientMsgType, MsgOrigin } from "../types/types";
export class TextAgent extends SocketManager {
    constructor(options) {
        super({
            apiKey: options.apiKey,
            config: options.config,
            eventCallback: options.eventCallback,
            startCallback: options.startCallback,
            endCallback: options.endCallback,
            textCallback: options.textCallback,
            transcriptCallback: options.transcriptCallback,
            stateCallback: options.stateCallback,
            telemetryCallback: options.telemetryCallback,
            baseUrl: options.baseUrl,
            platform: options.platform,
            customHeaders: options.customHeaders,
        });
    }
    /**
     * Send a text message to the agent.
     *
     * @param text - The text message to send
     * @throws Error if WebSocket is not connected
     */
    async sendText(text) {
        if (this.isWsClosed()) {
            throw new Error("WebSocket is not connected");
        }
        const message = {
            type: ClientMsgType.TEXT,
            origin: MsgOrigin.CLIENT,
            timestamp: Date.now() / 1000,
            text: text,
        };
        this.websocketSendQueue.push(message);
        await this.flushSendQueue();
    }
}
//# sourceMappingURL=text-agent.js.map