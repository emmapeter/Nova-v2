"""Runs in a child process: speaks text read from stdin, then exits.

A separate process makes interruption reliable: killing it stops the voice instantly
(pyttsx3's own stop() is unreliable across threads on Windows).
Usage: python tts_worker.py <rate> <volume> [voice_id]
"""
import sys


def main() -> int:
    rate = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    volume = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    voice_id = sys.argv[3] if len(sys.argv) > 3 else ""
    text = sys.stdin.read().strip()
    if not text:
        return 0
    import pyttsx3

    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    engine.setProperty("volume", volume)
    if voice_id:
        engine.setProperty("voice", voice_id)
    engine.say(text)
    engine.runAndWait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
