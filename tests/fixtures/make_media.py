"""
Generate deterministic test media with KNOWN ground truth (no files to commit):

    speech.mp4    ~20s of TTS speech with "um"/"uh" fillers and two long pauses (Windows SAPI or espeak)
    click120.wav  30s click track at exactly 120 BPM, first beat at 0.5s
    moving.mp4    6s 16:9 video, a white box sliding left→right (for smart reframing)

    python tests/fixtures/make_media.py <out_dir>
"""
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SPEECH_SSML = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
    "Welcome back to the channel. Today we are going hiking in the mountains.<break time=\"2000ms\"/>"
    "Um, so the trail starts right here, uh, next to the lake.<break time=\"1800ms\"/>"
    "The view from the top is absolutely amazing. Let us go!</speak>"
)
SPEECH_TEXT = ("Welcome back to the channel. Today we are going hiking in the mountains. [[slnc 2000]] "
               "Um, so the trail starts right here, uh, next to the lake. [[slnc 1800]] "
               "The view from the top is absolutely amazing. Let us go!")


def ffmpeg() -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.core.real_ops import FFMPEG
    return FFMPEG


def make_click(path: Path, bpm: float = 120, offset: float = 0.5, sec: int = 30, sr: int = 22050) -> None:
    y = 0.02 * np.random.default_rng(0).standard_normal(sec * sr)
    t = np.arange(int(0.03 * sr)) / sr
    click = 0.8 * np.sin(2 * np.pi * 1000 * t) * np.exp(-t * 80)
    k = 0
    while (i := int((offset + k * 60 / bpm) * sr)) + click.size < y.size:
        y[i:i + click.size] += click * (1.4 if k % 4 == 0 else 1.0)
        k += 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


def make_speech_wav(path: Path) -> bool:
    if sys.platform == "win32":
        ssml = SPEECH_SSML.replace("'", "''")
        ps = (f"Add-Type -AssemblyName System.Speech; $t = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$t.SetOutputToWaveFile('{path}'); $t.SpeakSsml('{ssml}'); $t.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=120)
    elif shutil.which("espeak-ng") or shutil.which("espeak"):
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        subprocess.run([exe, "-w", str(path), SPEECH_TEXT.replace("[[slnc 2000]]", "...").replace("[[slnc 1800]]", "...")],
                       capture_output=True, timeout=120)
    return path.exists() and path.stat().st_size > 10_000


def make_all(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg()
    made = {}
    click = out / "click120.wav"
    if not click.exists():
        make_click(click)
    made["click"] = click
    moving = out / "moving.mp4"
    if not moving.exists():
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x202020:s=1920x1080:d=6:r=30",
                        "-f", "lavfi", "-i", "color=c=white:s=260x260:d=6:r=30", "-filter_complex",
                        "[0:v]drawgrid=w=160:h=160:t=1:c=0x505050[bg];[bg][1:v]overlay=x='100+t*250':y=400",
                        "-c:v", "libx264", "-preset", "veryfast", str(moving)], capture_output=True, timeout=120)
    made["moving"] = moving
    speech = out / "speech.mp4"
    if not speech.exists():
        wav = out / "speech.wav"
        if make_speech_wav(wav):
            subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x334455:s=1280x720:r=30",
                            "-i", str(wav), "-map", "0:v", "-map", "1:a", "-shortest", "-c:v", "libx264",
                            "-preset", "veryfast", "-c:a", "aac", str(speech)], capture_output=True, timeout=120)
    made["speech"] = speech if speech.exists() else None
    return made


if __name__ == "__main__":
    print(make_all(Path(sys.argv[1] if len(sys.argv) > 1 else "test_clips/generated")))
