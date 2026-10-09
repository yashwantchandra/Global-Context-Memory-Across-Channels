"""python -m globalctx.voice.mic_check [seconds] -> live microphone level, to check the mic before a voice call."""
import array
import math
import sys
import time

import pyaudio

RATE, CHUNK = 16000, 1600  # 100 ms


def main(seconds=8):
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True, frames_per_buffer=CHUNK)
    peak = 0
    print(f"speak now for {seconds}s ...", flush=True)
    t0 = time.time()
    while time.time() - t0 < seconds:
        a = array.array("h", stream.read(CHUNK, exception_on_overflow=False))
        rms = math.sqrt(sum(x * x for x in a) / len(a))
        peak = max(peak, rms)
        print(f"{time.time() - t0:4.1f}s  level {int(rms):5d}  " + "#" * min(60, int(rms / 100)), flush=True)
    stream.close(); pa.terminate()
    verdict = "OK" if peak > 800 else "TOO QUIET (raise mic volume / check the right mic is selected)"
    print(f"peak level {int(peak)} -> {verdict}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
