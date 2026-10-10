/**
 * Audio queue for managing asynchronous audio message processing.
 *
 * Paces delivery of audio chunks to the playback interface at near-real-time
 * speed. After sending each chunk, the queue sleeps for approximately the
 * chunk's playback duration (minus a small headroom) before sending the next.
 * This prevents dumping all buffered audio at once while keeping the playback
 * buffer fed smoothly.
 */
import { base64ToUint8Array } from "./buffer";
export class AudioQueue {
    queue = [];
    shouldStop = false;
    processing = false;
    handler;
    resolveWaiting;
    sampleRate;
    headroomMs;
    sleepTimer;
    resolveSleep;
    constructor(handler, options) {
        this.handler = handler;
        this.sampleRate = options?.sampleRate ?? 16000;
        this.headroomMs = options?.headroomMs ?? 30;
    }
    /**
     * Add an audio message to the queue
     */
    enqueue(msg) {
        this.queue.push(msg);
        if (this.resolveWaiting) {
            this.resolveWaiting();
            this.resolveWaiting = undefined;
        }
    }
    /**
     * Start processing the queue
     */
    start() {
        if (this.processing) {
            return;
        }
        this.shouldStop = false;
        this.processing = true;
        this.processQueue();
    }
    /**
     * Stop processing the queue
     */
    stop() {
        this.shouldStop = true;
        this.processing = false;
        this.cancelSleep();
        if (this.resolveWaiting) {
            this.resolveWaiting();
            this.resolveWaiting = undefined;
        }
    }
    /**
     * Clear all pending messages from the queue
     */
    clear() {
        this.queue = [];
        this.cancelSleep();
    }
    /**
     * Get the current queue length
     */
    get length() {
        return this.queue.length;
    }
    /**
     * Calculate playback duration of a chunk in milliseconds from its PCM data.
     * 16-bit PCM = 2 bytes per sample.
     */
    chunkDurationMs(msg) {
        if (!msg.audio_base64)
            return 0;
        try {
            const bytes = base64ToUint8Array(msg.audio_base64);
            const samples = bytes.length / 2;
            const rate = msg.sample_rate ?? this.sampleRate;
            return (samples / rate) * 1000;
        }
        catch {
            return 0;
        }
    }
    cancelSleep() {
        if (this.sleepTimer != null) {
            clearTimeout(this.sleepTimer);
            this.sleepTimer = undefined;
        }
        if (this.resolveSleep) {
            this.resolveSleep();
            this.resolveSleep = undefined;
        }
    }
    sleep(ms) {
        if (ms <= 0)
            return Promise.resolve();
        return new Promise((resolve) => {
            this.resolveSleep = resolve;
            this.sleepTimer = setTimeout(() => {
                this.sleepTimer = undefined;
                this.resolveSleep = undefined;
                resolve();
            }, ms);
        });
    }
    /**
     * Process messages from the queue, pacing delivery to match playback speed.
     */
    async processQueue() {
        while (!this.shouldStop) {
            while (this.queue.length > 0 && !this.shouldStop) {
                const msg = this.queue.shift();
                const durationMs = this.chunkDurationMs(msg);
                try {
                    await this.handler(msg);
                }
                catch (error) {
                    console.error("Error processing audio message:", error);
                }
                if (this.shouldStop)
                    break;
                const paceMs = durationMs - this.headroomMs;
                if (paceMs > 0) {
                    await this.sleep(paceMs);
                }
            }
            if (this.shouldStop)
                break;
            await new Promise((resolve) => {
                this.resolveWaiting = resolve;
            });
        }
        this.processing = false;
    }
}
//# sourceMappingURL=audio-queue.js.map