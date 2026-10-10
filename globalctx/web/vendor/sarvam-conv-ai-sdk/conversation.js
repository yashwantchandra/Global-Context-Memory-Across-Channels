/**
 * Unified conversational AI agent using factory pattern.
 *
 * This module provides a single ConversationAgent class that internally
 * instantiates the appropriate agent type (VoiceAgent or TextAgent) based
 * on the interaction_type in the configuration.
 */
import { VoiceAgent } from "./modules/voice-agent";
import { TextAgent } from "./modules/text-agent";
import { InteractionType, } from "./types";
export class ConversationAgent {
    agent;
    constructor(options) {
        const interactionType = options.config.interaction_type;
        const baseUrl = options.baseUrl || "https://apps.sarvam.ai/api/app-runtime/";
        // Factory logic: decide which agent to instantiate based on interaction_type
        if (interactionType === InteractionType.CALL) {
            // Validate that audioInterface is provided for voice/call interactions
            if (!options.audioInterface) {
                throw new Error("audioInterface is required for CALL interactions. " +
                    "Please provide an audioInterface instance:\n" +
                    "  - For web/browser: new BrowserAudioInterface()\n" +
                    "  - For React Native: new RNAudioInterface()\n" +
                    "Example: new ConversationAgent({ audioInterface: new BrowserAudioInterface(), ... })");
            }
            // Create VoiceAgent for voice/call interactions
            this.agent = new VoiceAgent({
                apiKey: options.apiKey,
                config: options.config,
                audioInterface: options.audioInterface,
                audioCallback: options.audioCallback,
                audioLevelCallback: options.audioLevelCallback,
                eventCallback: options.eventCallback,
                startCallback: options.startCallback,
                endCallback: options.endCallback,
                textCallback: options.textCallback,
                transcriptCallback: options.transcriptCallback,
                stateCallback: options.stateCallback,
                telemetryCallback: options.telemetryCallback,
                platform: options.platform,
                baseUrl,
                customHeaders: options.customHeaders,
            });
        }
        else if (interactionType === InteractionType.CHAT) {
            // Create TextAgent for text/chat interactions
            this.agent = new TextAgent({
                apiKey: options.apiKey,
                config: options.config,
                eventCallback: options.eventCallback,
                startCallback: options.startCallback,
                endCallback: options.endCallback,
                textCallback: options.textCallback,
                transcriptCallback: options.transcriptCallback,
                stateCallback: options.stateCallback,
                telemetryCallback: options.telemetryCallback,
                platform: options.platform,
                baseUrl,
                customHeaders: options.customHeaders,
            });
        }
        else {
            throw new Error(`Unsupported interaction_type: ${interactionType}. ` +
                `Supported types are: ${InteractionType.CALL}, ${InteractionType.CHAT}`);
        }
    }
    /**
     * Get the reference ID (call SID)
     */
    get reference_id() {
        return this.agent.reference_id;
    }
    set reference_id(referenceId) {
        this.agent.reference_id = referenceId;
    }
    /**
     * Start the conversation session
     */
    async start() {
        await this.agent.start();
    }
    /**
     * Stop the conversation session
     */
    async stop() {
        await this.agent.stop();
    }
    /**
     * Wait until the WebSocket disconnects
     */
    async waitForDisconnect() {
        await this.agent.waitForDisconnect();
    }
    /**
     * Wait until the WebSocket connection is established
     */
    async waitForConnect(timeout) {
        return await this.agent.waitForConnect(timeout);
    }
    /**
     * Check if the WebSocket is currently connected
     */
    isConnected() {
        return this.agent.isConnected();
    }
    /**
     * Get the current interaction identifier
     */
    getInteractionId() {
        return this.agent.getInteractionId();
    }
    /**
     * Get the current agent state
     */
    getState() {
        return this.agent.getState();
    }
    /**
     * Send audio data (only available for voice/call interactions)
     */
    async sendAudio(audioData) {
        if (this.agent instanceof VoiceAgent) {
            await this.agent.sendAudio(audioData);
        }
        else {
            throw new Error("sendAudio() is only available for voice/call interactions");
        }
    }
    /**
     * Send text message (only available for text/chat interactions)
     */
    async sendText(text) {
        if (this.agent instanceof TextAgent) {
            await this.agent.sendText(text);
        }
        else {
            throw new Error("sendText() is only available for text/chat interactions");
        }
    }
    /**
     * Mute the microphone at the SDK level (only available for voice/call interactions).
     * When muted, real audio from the mic is dropped and continuous 10ms silence
     * chunks are sent to the server to keep the VAD stable.
     */
    mute() {
        if (this.agent instanceof VoiceAgent) {
            this.agent.mute();
        }
        else {
            throw new Error("mute() is only available for voice/call interactions");
        }
    }
    /**
     * Unmute the microphone at the SDK level (only available for voice/call interactions).
     * Real audio from the mic resumes being sent to the server.
     */
    unmute() {
        if (this.agent instanceof VoiceAgent) {
            this.agent.unmute();
        }
        else {
            throw new Error("unmute() is only available for voice/call interactions");
        }
    }
    /**
     * Check whether the microphone is currently muted.
     * Returns false for text/chat interactions.
     */
    isMuted() {
        if (this.agent instanceof VoiceAgent) {
            return this.agent.isMuted;
        }
        return false;
    }
    /**
     * Get the underlying agent instance type
     */
    getAgentType() {
        return this.agent instanceof VoiceAgent ? "voice" : "text";
    }
    /**
     * Check if this is a voice agent
     */
    isVoiceAgent() {
        return this.agent instanceof VoiceAgent;
    }
    /**
     * Check if this is a text agent
     */
    isTextAgent() {
        return this.agent instanceof TextAgent;
    }
}
export async function createConversation(config, apiKey, options) {
    const agent = new ConversationAgent({
        apiKey,
        config,
        audioInterface: options?.audioInterface,
        audioCallback: options?.audioCallback,
        eventCallback: options?.eventCallback,
        textCallback: options?.textCallback,
        transcriptCallback: options?.transcriptCallback,
        stateCallback: options?.stateCallback,
        telemetryCallback: options?.telemetryCallback,
        startCallback: options?.startCallback,
        endCallback: options?.endCallback,
        baseUrl: options?.baseUrl,
        platform: options?.platform,
        customHeaders: options?.customHeaders,
    });
    await agent.start();
    return agent;
}
//# sourceMappingURL=conversation.js.map