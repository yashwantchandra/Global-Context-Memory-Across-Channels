"""Half-duplex audio for laptop calls without earphones.

On laptop speakers the mic hears Mira's own voice, the agent takes it as the caller talking and replies to
itself (echo loop). The gate sends silence instead of mic audio while Mira's voice is playing, plus a short
tail, so only the caller's speech reaches Sarvam. Trade-off: the caller cannot interrupt Mira mid-sentence.
With earphones, run without the gate (--no-gate) to keep barge-in.
"""
import time

from sarvam_conv_ai_sdk import AsyncDefaultAudioInterface

TAIL_S = 0.6  # room echo + playback latency after the last agent audio


class Gate:
    """Pure timing logic (tested without audio devices)."""

    def __init__(self, tail_s=TAIL_S, clock=time.monotonic):
        self.tail_s, self.clock = tail_s, clock
        self.open_at = 0.0          # mic passes through once clock() >= open_at
        self.blocked = self.passed = 0

    def agent_audio(self, n_bytes, sample_rate):
        """Agent audio queued for playback: keep the mic closed until it has played out, plus the tail."""
        duration = n_bytes / 2 / (sample_rate or 8000)  # 16-bit mono
        start = max(self.clock(), self.open_at - self.tail_s)
        self.open_at = start + duration + self.tail_s

    def filter(self, chunk):
        if self.clock() < self.open_at:
            self.blocked += 1
            return b"\x00" * len(chunk)
        self.passed += 1
        return chunk

    def interrupt(self):
        self.open_at = 0.0


class GatedAudioInterface(AsyncDefaultAudioInterface):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.gate = Gate()

    async def start(self, input_callback):
        async def gated(data, *args, **kwargs):
            return await input_callback(self.gate.filter(data), *args, **kwargs)
        return await super().start(gated)

    async def output(self, audio, sample_rate=None):
        self.gate.agent_audio(len(audio), sample_rate or self.output_sample_rate)
        return await super().output(audio, sample_rate)

    def interrupt(self):
        self.gate.interrupt()
        return super().interrupt()
