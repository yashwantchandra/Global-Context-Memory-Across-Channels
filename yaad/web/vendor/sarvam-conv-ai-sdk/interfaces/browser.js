/**
 * Zero-latency browser audio interface.
 *
 * Plays each audio chunk immediately via AudioBufferSourceNode scheduling
 * on the Web Audio timeline. No ring buffer, no prebuffering, no state
 * machine — audio is heard as soon as the browser can render it.
 *
 * Trade-off: on very high jitter connections, short silence gaps may be
 * audible between chunks. Use BrowserAudioInterface for jitter-resilient
 * playback, or this class when minimal latency is the priority.
 */
export class BrowserAudioInterface {
    // Input
    audioContext;
    mediaStream;
    inputWorklet;
    sourceNode;
    inputCallback;
    sampleRate;
    isRecording = false;
    inputGainNode;
    // Input resampling + packetization
    inputResampleIdx = 0;
    inputResamplePrev = 0;
    inputChunkMs = 30;
    inputChunkBytes = 0;
    inputByteRing;
    inputByteRingSize = 0;
    inputByteWrite = 0;
    inputByteRead = 0;
    inputByteCount = 0;
    // Output — simple scheduled playback
    playbackContext;
    nextPlayTime = 0;
    activeSources = new Set();
    outputLevelCallback;
    // Output gain node for volume control (mobile devices often need boost)
    outputGainNode;
    outputGain;
    removeUnlockListeners;
    constructor(sampleRate = 16000, options = {}) {
        this.sampleRate = sampleRate;
        this.outputLevelCallback = options.outputLevelCallback;
        this.outputGain = options.outputGain ?? 1.0;
    }
    setOutputLevelCallback(callback) {
        this.outputLevelCallback = callback;
    }
    /**
     * Set the output volume gain. Values > 1.0 amplify the audio.
     * Mobile devices often need 1.5-2.0 for adequate volume.
     * @param gain - Volume multiplier (1.0 = normal, 2.0 = double volume)
     */
    setOutputGain(gain) {
        this.outputGain = gain;
        if (this.outputGainNode) {
            this.outputGainNode.gain.value = gain;
        }
    }
    /**
     * Get the current output volume gain.
     */
    getOutputGain() {
        return this.outputGain;
    }
    async start(inputCallback) {
        this.inputCallback = inputCallback;
        this.audioContext = new AudioContext();
        this.playbackContext = new AudioContext();
        // Create output gain node for volume control (helps with low mobile volume)
        this.outputGainNode = this.playbackContext.createGain();
        this.outputGainNode.gain.value = this.outputGain;
        this.outputGainNode.connect(this.playbackContext.destination);
        this.armUnlockListeners();
        await this.tryResumeContext(this.audioContext);
        await this.tryResumeContext(this.playbackContext);
        this.inputChunkBytes =
            Math.floor((this.sampleRate * this.inputChunkMs) / 1000) * 2;
        this.inputByteRingSize = Math.max(this.inputChunkBytes * 50, this.inputChunkBytes * 2);
        this.inputByteRing = new Uint8Array(this.inputByteRingSize);
        this.inputByteWrite = 0;
        this.inputByteRead = 0;
        this.inputByteCount = 0;
        this.inputResampleIdx = 0;
        this.inputResamplePrev = 0;
        this.nextPlayTime = 0;
        this.mediaStream = await this.getMicStreamWithFallback();
        this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
        await this.setupInputWorklet();
        this.isRecording = true;
    }
    async stop() {
        this.isRecording = false;
        this.removeUnlockListeners?.();
        this.removeUnlockListeners = undefined;
        this.mediaStream?.getTracks().forEach((t) => t.stop());
        this.mediaStream = undefined;
        this.inputWorklet?.disconnect();
        this.inputWorklet = undefined;
        this.inputGainNode?.disconnect();
        this.inputGainNode = undefined;
        this.sourceNode?.disconnect();
        this.sourceNode = undefined;
        this.interrupt();
        this.outputGainNode?.disconnect();
        this.outputGainNode = undefined;
        if (this.audioContext) {
            await this.audioContext.close();
            this.audioContext = undefined;
        }
        if (this.playbackContext) {
            await this.playbackContext.close();
            this.playbackContext = undefined;
        }
        this.inputByteRing = undefined;
        this.inputByteRingSize = 0;
        this.inputByteWrite = 0;
        this.inputByteRead = 0;
        this.inputByteCount = 0;
    }
    async output(audio, sampleRate = 16000) {
        if (!this.playbackContext)
            return;
        if (this.playbackContext.state === "suspended") {
            await this.playbackContext.resume();
        }
        const float32 = this.decodeInt16PCM(audio);
        if (float32.length === 0)
            return;
        // Resample to output context rate if needed
        const outRate = this.playbackContext.sampleRate;
        const samples = sampleRate === outRate
            ? float32
            : this.resampleForOutput(float32, sampleRate, outRate);
        const buffer = this.playbackContext.createBuffer(1, samples.length, outRate);
        buffer.getChannelData(0).set(samples);
        const source = this.playbackContext.createBufferSource();
        source.buffer = buffer;
        // Route through gain node for volume control
        if (this.outputGainNode) {
            source.connect(this.outputGainNode);
        }
        else {
            source.connect(this.playbackContext.destination);
        }
        this.activeSources.add(source);
        source.onended = () => this.activeSources.delete(source);
        // Schedule seamlessly: if we're behind, play now; otherwise append
        const now = this.playbackContext.currentTime;
        if (this.nextPlayTime <= now) {
            this.nextPlayTime = now;
        }
        source.start(this.nextPlayTime);
        this.nextPlayTime += buffer.duration;
        // Report audio level
        if (this.outputLevelCallback) {
            let sumSquares = 0;
            let peak = 0;
            for (let i = 0; i < samples.length; i++) {
                const absVal = Math.abs(samples[i]);
                if (absVal > peak)
                    peak = absVal;
                sumSquares += samples[i] * samples[i];
            }
            const rms = Math.sqrt(sumSquares / samples.length);
            const db = 20 * Math.log10(Math.max(rms, 1e-8));
            this.outputLevelCallback({ rms, peak, db });
        }
    }
    interrupt() {
        for (const src of this.activeSources) {
            try {
                src.stop();
            }
            catch {
                // already stopped
            }
        }
        this.activeSources.clear();
        this.nextPlayTime = 0;
    }
    // ==================== Input ====================
    async setupInputWorklet() {
        if (!this.audioContext || !this.sourceNode)
            return;
        const code = `
      class Capture extends AudioWorkletProcessor {
        process(inputs) {
          const ch = inputs[0]?.[0];
          if (ch) this.port.postMessage(ch.slice(0));
          return true;
        }
      }
      registerProcessor('capture', Capture);
    `;
        await this.loadWorklet(this.audioContext, code);
        this.inputWorklet = new AudioWorkletNode(this.audioContext, "capture");
        this.inputWorklet.port.onmessage = (e) => {
            if (!this.isRecording || !this.inputCallback)
                return;
            const samples = e.data;
            this.handleCapturedSamples(samples).catch(() => { });
        };
        this.sourceNode.connect(this.inputWorklet);
        this.inputGainNode = this.audioContext.createGain();
        this.inputGainNode.gain.value = 0;
        this.inputWorklet.connect(this.inputGainNode);
        this.inputGainNode.connect(this.audioContext.destination);
    }
    // ==================== Utilities ====================
    resampleForOutput(input, srcRate, dstRate) {
        if (input.length === 0 || srcRate === dstRate)
            return input;
        const ratio = srcRate / dstRate;
        const outLen = Math.ceil(input.length / ratio);
        const out = new Float32Array(outLen);
        for (let i = 0; i < outLen; i++) {
            const srcIdx = i * ratio;
            const idx = Math.floor(srcIdx);
            const frac = srcIdx - idx;
            const s0 = input[Math.min(idx, input.length - 1)];
            const s1 = input[Math.min(idx + 1, input.length - 1)];
            out[i] = s0 + (s1 - s0) * frac;
        }
        return out;
    }
    async loadWorklet(ctx, code) {
        const blob = new Blob([code], { type: "application/javascript" });
        const blobUrl = URL.createObjectURL(blob);
        try {
            await ctx.audioWorklet.addModule(blobUrl);
            return;
        }
        catch {
            // fall through
        }
        finally {
            URL.revokeObjectURL(blobUrl);
        }
        const dataUrl = this.toDataUrl(code);
        await ctx.audioWorklet.addModule(dataUrl);
    }
    async getMicStreamWithFallback() {
        const base = {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        };
        try {
            return await navigator.mediaDevices.getUserMedia({
                audio: { ...base, sampleRate: this.sampleRate },
            });
        }
        catch {
            try {
                return await navigator.mediaDevices.getUserMedia({
                    audio: { ...base },
                });
            }
            catch {
                return await navigator.mediaDevices.getUserMedia({ audio: true });
            }
        }
    }
    async handleCapturedSamples(samples) {
        if (this.audioContext && this.audioContext.state === "suspended") {
            await this.tryResumeContext(this.audioContext);
        }
        const srcRate = this.audioContext?.sampleRate || this.sampleRate;
        const float32 = srcRate === this.sampleRate
            ? samples
            : this.resampleFloat32(samples, srcRate, this.sampleRate);
        this.enqueuePcmBytes(float32);
        await this.flushInputChunks();
    }
    enqueuePcmBytes(float32) {
        if (!this.inputByteRing || this.inputByteRingSize <= 0)
            return;
        for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            const val = Math.round(s < 0 ? s * 32768 : s * 32767);
            this.writeInputByte(val & 0xff);
            this.writeInputByte((val >> 8) & 0xff);
        }
    }
    writeInputByte(b) {
        if (!this.inputByteRing)
            return;
        this.inputByteRing[this.inputByteWrite] = b;
        this.inputByteWrite = (this.inputByteWrite + 1) % this.inputByteRingSize;
        if (this.inputByteCount < this.inputByteRingSize) {
            this.inputByteCount++;
        }
        else {
            this.inputByteRead = (this.inputByteRead + 1) % this.inputByteRingSize;
        }
    }
    async flushInputChunks() {
        if (!this.inputCallback || !this.inputByteRing || this.inputChunkBytes <= 0)
            return;
        while (this.inputByteCount >= this.inputChunkBytes) {
            const out = new Uint8Array(this.inputChunkBytes);
            for (let i = 0; i < out.length; i++) {
                out[i] = this.inputByteRing[this.inputByteRead];
                this.inputByteRead = (this.inputByteRead + 1) % this.inputByteRingSize;
            }
            this.inputByteCount -= this.inputChunkBytes;
            await this.inputCallback(out, out.length / 2);
        }
    }
    resampleFloat32(input, srcRate, dstRate) {
        if (input.length === 0 || srcRate === dstRate)
            return input;
        const step = srcRate / dstRate;
        const est = Math.max(0, Math.floor((input.length - this.inputResampleIdx) / step) + 2);
        const out = new Float32Array(est);
        let outLen = 0;
        let idx = this.inputResampleIdx;
        while (idx < input.length) {
            const i0 = Math.floor(idx);
            const frac = idx - i0;
            const s0 = i0 === 0 ? this.inputResamplePrev : input[i0 - 1];
            const s1 = input[i0];
            out[outLen++] = s0 + (s1 - s0) * frac;
            idx += step;
        }
        this.inputResampleIdx = idx - input.length;
        this.inputResamplePrev = input[input.length - 1];
        return out.subarray(0, outLen);
    }
    armUnlockListeners() {
        if (typeof document === "undefined")
            return;
        if (this.removeUnlockListeners)
            return;
        const handler = () => {
            void this.tryResumeContext(this.audioContext);
            void this.tryResumeContext(this.playbackContext);
            const inReady = !this.audioContext || this.audioContext.state === "running";
            const outReady = !this.playbackContext || this.playbackContext.state === "running";
            if (inReady && outReady) {
                this.removeUnlockListeners?.();
                this.removeUnlockListeners = undefined;
            }
        };
        const opts = { capture: true, passive: true };
        document.addEventListener("pointerdown", handler, opts);
        document.addEventListener("keydown", handler, opts);
        document.addEventListener("touchstart", handler, opts);
        this.removeUnlockListeners = () => {
            document.removeEventListener("pointerdown", handler, opts);
            document.removeEventListener("keydown", handler, opts);
            document.removeEventListener("touchstart", handler, opts);
        };
    }
    async tryResumeContext(ctx) {
        if (!ctx || ctx.state === "running")
            return;
        try {
            await ctx.resume();
        }
        catch {
            // Autoplay policy may block; gesture listener will retry.
        }
    }
    toDataUrl(code) {
        const bytes = new TextEncoder().encode(code);
        let binary = "";
        const chunkSize = 0x8000;
        for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
        }
        return `data:application/javascript;base64,${btoa(binary)}`;
    }
    decodeInt16PCM(audio) {
        let data = audio;
        if (audio.length >= 44 &&
            audio[0] === 0x52 &&
            audio[1] === 0x49 &&
            audio[2] === 0x46 &&
            audio[3] === 0x46) {
            let i = 12;
            while (i + 8 <= audio.length) {
                const id = String.fromCharCode(audio[i], audio[i + 1], audio[i + 2], audio[i + 3]);
                const size = audio[i + 4] |
                    (audio[i + 5] << 8) |
                    (audio[i + 6] << 16) |
                    (audio[i + 7] << 24);
                if (id === "data") {
                    data = audio.slice(i + 8, Math.min(i + 8 + size, audio.length));
                    break;
                }
                i += 8 + size + (size % 2);
            }
        }
        if (data.length % 2 !== 0) {
            data = data.slice(0, -1);
        }
        const numSamples = data.length / 2;
        const float32 = new Float32Array(numSamples);
        for (let i = 0; i < numSamples; i++) {
            const lo = data[i * 2];
            const hi = data[i * 2 + 1];
            let val = lo | (hi << 8);
            if (val >= 0x8000)
                val -= 0x10000;
            float32[i] = val / 32768;
        }
        return float32;
    }
}
//# sourceMappingURL=browser.js.map