"""Echo guard for laptop calls. The SDK's default interface has no echo cancellation, so on laptop speakers
the mic hears the bot, the bot hears itself, interrupts itself and repeats (seen in testing 9 Oct).
While bot audio is playing (plus a short tail), the mic sends silence instead. Half-duplex: no barge-in.
Disable with YAAD_ECHO_GUARD=0 when using earphones."""
import asyncio
import os
import time

from sarvam_conv_ai_sdk import AsyncDefaultAudioInterface

TAIL_S = float(os.environ.get("YAAD_ECHO_TAIL_S", "0.45"))


class EchoGuardAudioInterface(AsyncDefaultAudioInterface):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.playing_until = 0.0
        self.muted_frames = 0

    def _bot_audible(self):
        busy = self.output_queue is not None and not self.output_queue.empty()
        return busy or time.monotonic() < self.playing_until

    async def _output_task(self):
        try:
            while self.should_stop and not self.should_stop.is_set():
                audio_data, sample_rate = await self.output_queue.get()
                if sample_rate != self.current_output_sample_rate:
                    await self._recreate_output_stream(sample_rate)
                seconds = len(audio_data) / 2 / (sample_rate or self.output_sample_rate)
                self.playing_until = max(self.playing_until, time.monotonic()) + seconds + TAIL_S
                await asyncio.to_thread(self.output_stream.write, audio_data)
        except asyncio.CancelledError:
            pass

    def _pyaudio_input_callback(self, in_data, frame_count, time_info, status):
        if self._bot_audible():
            self.muted_frames += 1
            in_data = b"\x00" * len(in_data)  # keep the stream timing, send silence
        return super()._pyaudio_input_callback(in_data, frame_count, time_info, status)


def make_audio_interface():
    if os.environ.get("YAAD_ECHO_GUARD", "1") == "0":
        return AsyncDefaultAudioInterface()
    return EchoGuardAudioInterface()
