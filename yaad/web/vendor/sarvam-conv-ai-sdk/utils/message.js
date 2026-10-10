import { ClientMsgType, MsgOrigin, ServerMsgType, } from "../types";
/**
 * Create an interaction start message from InteractionConfig
 */
export function createStartInteractionMessageFromConfig(config) {
    return {
        type: ClientMsgType.INTERACTION_START,
        origin: MsgOrigin.CLIENT,
        timestamp: Date.now() / 1000,
        agent_variables: config.agent_variables,
        initial_language_name: config.initial_language_name,
        initial_bot_message: config.initial_bot_message,
        initial_state_name: config.initial_state_name,
    };
}
/**
 * Parse a server message from a raw JSON-like object
 */
export function parseServerMessage(data) {
    if (!data || typeof data !== "object") {
        throw new Error("Invalid message: must be an object");
    }
    if (!data.type) {
        throw new Error("Invalid message: missing 'type' field");
    }
    // Validate the message structure based on type
    switch (data.type) {
        case ServerMsgType.TEXT_CHUNK:
        case ServerMsgType.TEXT:
        case ServerMsgType.AUDIO_CHUNK:
        case ServerMsgType.USER_INTERRUPT:
        case ServerMsgType.INTERACTION_END:
        case ServerMsgType.INTERACTION_CONNECTED:
        case ServerMsgType.PING:
            return data;
        default:
            // Instead of throwing, log a warning and return the message
            // This allows us to see what unknown message types are being sent
            console.warn(`[SDK] Unknown server message type: ${data.type}`);
            console.warn("[SDK] Full message:", JSON.stringify(data, null, 2));
            return data;
    }
}
/**
 * Parse a client message from a raw JSON-like object
 */
export function parseClientMessage(data) {
    if (!data || typeof data !== "object") {
        throw new Error("Invalid message: must be an object");
    }
    if (!data.type) {
        throw new Error("Invalid message: missing 'type' field");
    }
    return data;
}
//# sourceMappingURL=message.js.map