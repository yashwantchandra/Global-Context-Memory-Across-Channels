/**
 * Audio encoding types
 */
export var AudioEncoding;
(function (AudioEncoding) {
    AudioEncoding["LINEAR16"] = "audio/wav";
})(AudioEncoding || (AudioEncoding = {}));
/**
 * Supported input sample rates for audio sent to the server
 */
export const SUPPORTED_INPUT_SAMPLE_RATES = [8000, 16000];
/**
 * Default input sample rate
 */
export const DEFAULT_INPUT_SAMPLE_RATE = 16000;
/**
 * @deprecated Use DEFAULT_INPUT_SAMPLE_RATE instead
 */
export const INPUT_SAMPLE_RATE = DEFAULT_INPUT_SAMPLE_RATE;
/**
 * Supported output sample rates for audio received from the server
 */
export const SUPPORTED_OUTPUT_SAMPLE_RATES = [16000, 22050];
/**
 * Supported sample rates (alias for output sample rates for backward compatibility)
 * @deprecated Use SUPPORTED_OUTPUT_SAMPLE_RATES or SUPPORTED_INPUT_SAMPLE_RATES instead
 */
export const SUPPORTED_SAMPLE_RATES = SUPPORTED_OUTPUT_SAMPLE_RATES;
/**
 * Validate input sample rate
 */
export function validateInputSampleRate(value) {
    if (!SUPPORTED_INPUT_SAMPLE_RATES.includes(value)) {
        throw new Error(`Input sample rate ${value} not supported. Supported input rates: ${SUPPORTED_INPUT_SAMPLE_RATES.join(", ")}`);
    }
    return value;
}
/**
 * Validate output sample rate
 */
export function validateOutputSampleRate(value) {
    if (!SUPPORTED_OUTPUT_SAMPLE_RATES.includes(value)) {
        throw new Error(`Output sample rate ${value} not supported. Supported output rates: ${SUPPORTED_OUTPUT_SAMPLE_RATES.join(", ")}`);
    }
    return value;
}
/**
 * @deprecated Use validateInputSampleRate or validateOutputSampleRate instead
 */
export function validateSampleRate(value) {
    if (!SUPPORTED_INPUT_SAMPLE_RATES.includes(value) &&
        !SUPPORTED_OUTPUT_SAMPLE_RATES.includes(value)) {
        throw new Error(`Sample rate ${value} not supported. Supported rates: ${[...SUPPORTED_INPUT_SAMPLE_RATES, ...SUPPORTED_OUTPUT_SAMPLE_RATES].join(", ")}`);
    }
    return value;
}
/**
 * Server message categories
 */
export var ServerMsgCategory;
(function (ServerMsgCategory) {
    ServerMsgCategory["MEDIA"] = "media";
    ServerMsgCategory["ACTION"] = "action";
    ServerMsgCategory["SYSTEM"] = "system";
    ServerMsgCategory["EVENT"] = "event";
})(ServerMsgCategory || (ServerMsgCategory = {}));
/**
 * Message status
 */
export var MsgStatus;
(function (MsgStatus) {
    MsgStatus["PENDING"] = "pending";
    MsgStatus["COMPLETED"] = "completed";
    MsgStatus["FAILED"] = "failed";
})(MsgStatus || (MsgStatus = {}));
/**
 * Client message categories
 */
export var ClientMsgCategory;
(function (ClientMsgCategory) {
    ClientMsgCategory["MEDIA"] = "media";
    ClientMsgCategory["ACTION"] = "action";
    ClientMsgCategory["SYSTEM"] = "system";
})(ClientMsgCategory || (ClientMsgCategory = {}));
/**
 * Message origin
 */
export var MsgOrigin;
(function (MsgOrigin) {
    MsgOrigin["SERVER"] = "server";
    MsgOrigin["CLIENT"] = "client";
})(MsgOrigin || (MsgOrigin = {}));
/**
 * Server message types
 */
export var ServerMsgType;
(function (ServerMsgType) {
    // Media messages
    ServerMsgType["AUDIO_CHUNK"] = "server.media.audio_chunk";
    ServerMsgType["AUDIO"] = "server.media.audio";
    ServerMsgType["TEXT"] = "server.media.text";
    ServerMsgType["TEXT_CHUNK"] = "server.media.text_chunk";
    // System messages
    ServerMsgType["PING"] = "server.system.ping";
    // Action messages
    ServerMsgType["INTERACTION_END"] = "server.action.interaction_end";
    ServerMsgType["INTERACTION_CONNECTED"] = "server.action.interaction_connected";
    // Event messages
    ServerMsgType["USER_SPEECH_START"] = "server.event.user_speech_start";
    ServerMsgType["USER_SPEECH_END"] = "server.event.user_speech_end";
    ServerMsgType["USER_INTERRUPT"] = "server.event.user_interrupt";
    ServerMsgType["VARIABLE_UPDATE"] = "server.event.variable_update";
    ServerMsgType["LANGUAGE_CHANGE"] = "server.event.language_change";
    ServerMsgType["STATE_TRANSITION"] = "server.event.state_transition";
    ServerMsgType["TRANSCRIPTION"] = "server.event.transcription";
    ServerMsgType["KB_QUERY"] = "server.event.kb_query";
    ServerMsgType["TOOL_CALL"] = "server.event.tool_call";
})(ServerMsgType || (ServerMsgType = {}));
/**
 * Client message types
 */
export var ClientMsgType;
(function (ClientMsgType) {
    // Media messages
    ClientMsgType["AUDIO_CHUNK"] = "client.media.audio_chunk";
    ClientMsgType["TEXT"] = "client.media.text";
    ClientMsgType["TEXT_CHUNK"] = "client.media.text_chunk";
    ClientMsgType["AUDIO"] = "client.media.audio";
    // Action messages
    ClientMsgType["INTERACTION_START"] = "client.action.interaction_start";
    ClientMsgType["INTERACTION_END"] = "client.action.interaction_end";
    ClientMsgType["VARIABLE_UPDATE"] = "client.action.variable_update";
    ClientMsgType["LANGUAGE_CHANGE"] = "client.action.language_change";
    ClientMsgType["STATE_TRANSITION"] = "client.action.state_transition";
    // System messages
    ClientMsgType["PONG"] = "client.system.pong";
})(ClientMsgType || (ClientMsgType = {}));
/**
 * Interaction type
 */
export var InteractionType;
(function (InteractionType) {
    InteractionType["CHAT"] = "chat";
    InteractionType["CALL"] = "call";
})(InteractionType || (InteractionType = {}));
/**
 * User identifier type
 */
export var UserIdentifierType;
(function (UserIdentifierType) {
    UserIdentifierType["PHONE_NUMBER"] = "phone_number";
    UserIdentifierType["EMAIL"] = "email";
    UserIdentifierType["CUSTOM"] = "custom";
    UserIdentifierType["UNKNOWN"] = "unknown";
})(UserIdentifierType || (UserIdentifierType = {}));
/**
 * Role of the speaker in a transcript
 */
export var Role;
(function (Role) {
    Role["USER"] = "user";
    Role["BOT"] = "bot";
})(Role || (Role = {}));
//# sourceMappingURL=types.js.map