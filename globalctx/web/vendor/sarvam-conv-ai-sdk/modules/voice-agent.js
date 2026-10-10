/**
 * Voice-based conversational AI agent implementation.
 *
 * This module provides the VoiceAgent class for managing real-time
 * voice conversations with audio input/output capabilities.
 *
 * For browser environments, you can use:
 * - PassthroughAudioInterface: Direct audio playback (auto-created by default)
 * - BufferedAudioInterface: Buffered playback for low internet connections
 */
import { SocketManager } from "./socket-manager";
import { AgentState, } from "../types";
import { AudioEncoding, ClientMsgType, MsgOrigin, MsgStatus, ServerMsgType, } from "../types/types";
import { base64ToUint8Array, uint8ArrayToBase64 } from "../utils/buffer";
import { AudioQueue } from "../utils/audio-queue";
export class VoiceAgent extends SocketManager {
    audioInterface;
    audioCallback;
    audioLevelCallback;
    audioQueue;
    audioWarningLogged = false;
    // Mute state
    _isMuted = false;
    silenceInterval = null;
    silenceChunk = null;
    // RMS threshold for detecting actual speech vs silence
    // Values below this are considered silence
    static SILENCE_RMS_THRESHOLD = 0.01;
    // Track whether server has signaled end of utterance (COMPLETED)
    utteranceCompleted = false;
    // Total audio duration (ms) pushed to the playback interface since SPEAKING started
    playbackAudioMs = 0;
    // Timestamp when we started pushing audio for this utterance
    playbackStartedAt = 0;
    // Timer that fires when estimated playback should be done
    playbackDrainTimer = null;
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
        this.audioCallback = options.audioCallback;
        this.audioInterface = options.audioInterface;
        this.audioLevelCallback = options.audioLevelCallback;
        this.audioQueue = new AudioQueue(async (msg) => {
            await this.handleAudio(msg);
        }, { sampleRate: options.config.output_sample_rate });
    }
    // Silence chunk: 30ms of audio data, sent every 25ms (~40 packets/sec).
    // Keeps the server-side VAD stable without flooding the WebSocket.
    static SILENCE_CHUNK_MS = 30;
    static SILENCE_INTERVAL_MS = 25;
    /**
     * Get or create a pre-computed 30ms silence chunk (all zeros).
     * At 16kHz: 30ms = 480 samples = 960 bytes of Int16 PCM zeros
     * At 8kHz: 30ms = 240 samples = 480 bytes of Int16 PCM zeros
     */
    getSilenceChunk() {
        if (!this.silenceChunk) {
            const samples = Math.floor(this.config.input_sample_rate * (VoiceAgent.SILENCE_CHUNK_MS / 1000));
            const byteLength = samples * 2; // 16-bit PCM = 2 bytes per sample
            this.silenceChunk = new Uint8Array(byteLength); // zero-filled by default
        }
        return this.silenceChunk;
    }
    /**
     * Start the silence-sending interval loop.
     * Sends a 30ms silence chunk every 25ms while muted (~40 packets/sec).
     */
    startSilenceLoop() {
        // Guard against duplicate intervals
        if (this.silenceInterval !== null) {
            return;
        }
        this.silenceInterval = setInterval(async () => {
            if (this._isMuted && !this.isWsClosed()) {
                try {
                    await this.sendAudio(this.getSilenceChunk());
                }
                catch {
                    // WebSocket may have closed between the check and send; ignore
                }
            }
        }, VoiceAgent.SILENCE_INTERVAL_MS);
    }
    /**
     * Stop the silence-sending interval loop.
     */
    stopSilenceLoop() {
        if (this.silenceInterval !== null) {
            clearInterval(this.silenceInterval);
            this.silenceInterval = null;
        }
    }
    /**
     * Mute the microphone at the SDK level.
     * Real audio from the mic is dropped and continuous 10ms silence chunks
     * are sent to the server to keep the VAD stable.
     */
    mute() {
        this._isMuted = true;
        this.startSilenceLoop();
        this.emitTelemetry("user_muted", {});
    }
    /**
     * Unmute the microphone at the SDK level.
     * Real audio from the mic resumes being sent to the server.
     */
    unmute() {
        this._isMuted = false;
        this.stopSilenceLoop();
        this.emitTelemetry("user_unmuted", {});
    }
    /**
     * Check whether the microphone is currently muted.
     */
    get isMuted() {
        return this._isMuted;
    }
    /**
     * Calculate RMS (Root Mean Square) amplitude of audio data.
     * Used to detect if audio contains actual speech vs silence.
     *
     * @param audioBase64 - Base64 encoded audio data (16-bit PCM)
     * @returns RMS value between 0 and 1
     */
    calculateRMS(audioBase64) {
        try {
            const audioBytes = base64ToUint8Array(audioBase64);
            // Convert to 16-bit samples
            const samples = new Int16Array(audioBytes.buffer, audioBytes.byteOffset, audioBytes.length / 2);
            if (samples.length === 0) {
                return 0;
            }
            // Calculate RMS
            let sumSquares = 0;
            for (let i = 0; i < samples.length; i++) {
                // Normalize to -1 to 1 range
                const normalized = samples[i] / 32768;
                sumSquares += normalized * normalized;
            }
            return Math.sqrt(sumSquares / samples.length);
        }
        catch (error) {
            console.error("[SDK] Error calculating RMS:", error);
            return 0;
        }
    }
    /**
     * Check if audio contains actual speech (not silence)
     */
    hasActualSpeech(audioBase64) {
        const rms = this.calculateRMS(audioBase64);
        return rms > VoiceAgent.SILENCE_RMS_THRESHOLD;
    }
    /**
     * Calculate RMS/peak and dBFS from 16-bit PCM audio bytes.
     */
    calculateAudioLevel(audioData) {
        const sampleCount = Math.floor(audioData.length / 2);
        if (sampleCount <= 0) {
            return { rms: 0, peak: 0, db: -120 };
        }
        const samples = new Int16Array(audioData.buffer, audioData.byteOffset, sampleCount);
        let sumSquares = 0;
        let peak = 0;
        for (let i = 0; i < samples.length; i++) {
            const normalized = samples[i] / 32768;
            const absValue = Math.abs(normalized);
            if (absValue > peak) {
                peak = absValue;
            }
            sumSquares += normalized * normalized;
        }
        const rms = Math.sqrt(sumSquares / samples.length);
        const db = 20 * Math.log10(Math.max(rms, 1e-8));
        return { rms, peak, db };
    }
    /**
     * Send an audio chunk to the agent.
     *
     * @param audioData - Raw 16-bit PCM mono audio bytes at the configured input_sample_rate
     * @throws Error if WebSocket is not connected
     */
    async sendAudio(audioData) {
        if (this.isWsClosed()) {
            throw new Error("WebSocket is not connected");
        }
        const audioBase64 = uint8ArrayToBase64(audioData);
        const message = {
            type: ClientMsgType.AUDIO_CHUNK,
            origin: MsgOrigin.CLIENT,
            timestamp: Date.now() / 1000,
            audio_base64: audioBase64,
            format: AudioEncoding.LINEAR16,
            sample_rate: this.config.input_sample_rate,
        };
        this.websocketSendQueue.push(message);
        await this.flushSendQueue();
        // Track first audio packet sent
        if (!this.firstAudioSent && this.sessionStartTime) {
            this.firstAudioSent = true;
            const latencyMs = Date.now() - this.sessionStartTime;
            this.emitTelemetry("first_audio_packet_sent", { latencyMs });
        }
    }
    /**
     * Override to modify the signed URL with input and output sample rates.
     * The server will accept audio at the input sample rate and return audio at the output sample rate.
     */
    modifySignedUrlString(url) {
        let modified = this.addQueryParam(url, "input_sample_rate", String(this.config.input_sample_rate));
        modified = this.addQueryParam(modified, "output_sample_rate", String(this.config.output_sample_rate));
        return modified;
    }
    /**
     * Override to set interaction type to "call"
     * Note: user_identifier params are already added in getSignedUrl(), don't add again
     */
    augmentWsUrlWithParams(wsUrl) {
        return this.addQueryParam(wsUrl, "interaction_type", "call");
    }
    /**
     * Override to stop audio interface, queue, and silence loop
     */
    async onStop() {
        this.stopSilenceLoop();
        this._isMuted = false;
        this.clearPlaybackDrainTimer();
        this.resetPlaybackTracking();
        this.utteranceCompleted = false;
        this.audioQueue.stop();
        if (this.audioInterface) {
            await this.audioInterface.stop();
            this.emitTelemetry("audio_interface_stopped", {});
        }
    }
    /**
     * Override to start audio interface and processing queue
     */
    async onWebSocketOpen() {
        const OPEN = 1;
        // Start audio interface if provided
        if (this.audioInterface) {
            this.emitTelemetry("audio_interface_started", {});
            // Set up output level callback for real-time playback levels
            // This provides responsive output levels calculated at playback time (~128 samples)
            // Only reports levels when agent is SPEAKING
            if ("setOutputLevelCallback" in this.audioInterface &&
                this.audioLevelCallback) {
                this.audioInterface.setOutputLevelCallback((level) => {
                    if (this.getState() !== AgentState.SPEAKING)
                        return;
                    this.audioLevelCallback({
                        direction: "output",
                        rms: level.rms,
                        peak: level.peak,
                        db: level.db,
                        sampleRate: this.config.output_sample_rate,
                    });
                });
            }
            await this.audioInterface.start(async (audioData, _frameCount) => {
                try {
                    // Only report input levels when not speaking (i.e., when listening)
                    if (this.audioLevelCallback &&
                        this.getState() !== AgentState.SPEAKING) {
                        const { rms, peak, db } = this.calculateAudioLevel(audioData);
                        this.audioLevelCallback({
                            direction: "input",
                            rms,
                            peak,
                            db,
                            sampleRate: this.config.input_sample_rate,
                        });
                    }
                    if (this.ws && this.ws.readyState === OPEN && !this._isMuted) {
                        await this.sendAudio(audioData);
                    }
                }
                catch (error) {
                    console.error("Error in audio input callback:", error);
                    this.emitTelemetry("error", {
                        type: "audio_capture",
                        message: error instanceof Error ? error.message : String(error),
                    });
                }
            });
        }
        // Start processing audio receive queue
        this.audioQueue.start();
    }
    /**
     * Override to handle audio chunks and user interrupts
     */
    async handleCustomMessage(message) {
        if (message.type === ServerMsgType.AUDIO_CHUNK) {
            const audioMsg = message;
            // Track first audio packet received from server
            if (!this.firstAudioReceived &&
                audioMsg.audio_base64 &&
                this.sessionStartTime) {
                this.firstAudioReceived = true;
                const latencyMs = Date.now() - this.sessionStartTime;
                this.emitTelemetry("first_audio_packet_received", { latencyMs });
            }
            // Handle state transitions based on audio status and content
            if (audioMsg.status === MsgStatus.COMPLETED) {
                this.utteranceCompleted = true;
                this.scheduleListeningTransition();
            }
            else if (audioMsg.status === MsgStatus.PENDING &&
                audioMsg.audio_base64) {
                // New audio arriving — cancel any pending transition
                this.utteranceCompleted = false;
                this.clearPlaybackDrainTimer();
                // Track audio duration for playback estimation
                const samples = base64ToUint8Array(audioMsg.audio_base64).length / 2;
                const durationMs = (samples / this.config.output_sample_rate) * 1000;
                if (this.playbackStartedAt === 0) {
                    this.playbackStartedAt = Date.now();
                }
                this.playbackAudioMs += durationMs;
                // Only transition to SPEAKING if audio contains actual speech (not silence)
                if (this.hasActualSpeech(audioMsg.audio_base64)) {
                    if (this.getState() !== AgentState.SPEAKING) {
                        this.setState(AgentState.SPEAKING);
                    }
                }
            }
            // Always enqueue audio for playback
            this.audioQueue.enqueue(audioMsg);
        }
        else if (message.type === ServerMsgType.USER_INTERRUPT) {
            // User interrupted - transition to LISTENING immediately (playback is cleared)
            this.utteranceCompleted = false;
            this.clearPlaybackDrainTimer();
            this.playbackAudioMs = 0;
            this.playbackStartedAt = 0;
            this.setState(AgentState.LISTENING);
            this.emitTelemetry("user_interrupted", {});
            this.audioQueue.clear();
            if (this.audioInterface) {
                this.audioInterface.interrupt();
            }
            await this.handleEvent(message);
        }
        else {
            await this.handleEvent(message);
        }
    }
    /**
     * Handle audio message from the agent
     */
    async handleAudio(audioMsg) {
        try {
            if (!this.audioInterface && !this.audioCallback) {
                if (!this.audioWarningLogged) {
                    console.warn("No audio interface or callback provided. Audio messages will be ignored.");
                    this.audioWarningLogged = true;
                }
                return;
            }
            if (audioMsg.audio_base64) {
                const audioBytes = base64ToUint8Array(audioMsg.audio_base64);
                // Report output audio level if interface doesn't provide its own level callback
                // (BrowserAudioInterface provides responsive worklet-based levels, others may not)
                // Only reports levels when agent is SPEAKING
                if (this.audioLevelCallback &&
                    this.audioInterface &&
                    !("setOutputLevelCallback" in this.audioInterface) &&
                    this.getState() === AgentState.SPEAKING) {
                    const { rms, peak, db } = this.calculateAudioLevel(audioBytes);
                    this.audioLevelCallback({
                        direction: "output",
                        rms,
                        peak,
                        db,
                        sampleRate: this.config.output_sample_rate,
                    });
                }
                if (this.audioInterface) {
                    await this.audioInterface.output(audioBytes, this.config.output_sample_rate);
                }
            }
            // Always invoke audioCallback if provided (even alongside audioInterface)
            // This allows diagnostic/monitoring hooks to capture raw server audio
            if (this.audioCallback) {
                await this.audioCallback(audioMsg);
            }
        }
        catch (error) {
            console.error("Error handling audio message:", error);
            this.emitTelemetry("error", {
                type: "audio_playback",
                message: error instanceof Error ? error.message : String(error),
            });
        }
    }
    clearPlaybackDrainTimer() {
        if (this.playbackDrainTimer) {
            clearTimeout(this.playbackDrainTimer);
            this.playbackDrainTimer = null;
        }
    }
    /**
     * Schedule LISTENING transition based on how much audio was pushed to playback.
     * Called when COMPLETED arrives. Calculates remaining playback time from the
     * total audio duration minus how long we've been playing, plus a buffer for
     * the ring buffer prebuffer delay.
     */
    scheduleListeningTransition() {
        this.clearPlaybackDrainTimer();
        if (this.getState() !== AgentState.SPEAKING) {
            // Not speaking (e.g. first connect greeting) — transition immediately
            this.resetPlaybackTracking();
            return;
        }
        const elapsed = this.playbackStartedAt > 0 ? Date.now() - this.playbackStartedAt : 0;
        // Remaining = total audio pushed minus wall-clock time since first chunk.
        // Small safety margin (200ms) for processing/scheduling jitter.
        const remainingMs = Math.max(0, this.playbackAudioMs - elapsed) + 200;
        this.playbackDrainTimer = setTimeout(() => {
            this.playbackDrainTimer = null;
            if (this.getState() === AgentState.SPEAKING && this.utteranceCompleted) {
                this.utteranceCompleted = false;
                this.resetPlaybackTracking();
                this.setState(AgentState.LISTENING);
            }
        }, remainingMs);
    }
    resetPlaybackTracking() {
        this.playbackAudioMs = 0;
        this.playbackStartedAt = 0;
    }
    /**
     * Override to reset audio warning flag
     */
    async start() {
        this.audioWarningLogged = false;
        await super.start();
    }
}
//# sourceMappingURL=voice-agent.js.map