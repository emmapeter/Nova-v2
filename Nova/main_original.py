
import sounddevice as sd
from scipy.io.wavfile import write
import speech_recognition as sr
import pyttsx3
import numpy as np
import ollama
import json
import os
import re

# =========================
# SETTINGS
# =========================

MEMORY_FILE = "memory.json"

MODEL = "llama3.2:1b"

SAMPLE_RATE = 44100
DEVICE = 1

CONVERSATION_LISTEN_SECONDS = 10

SILENCE_THRESHOLD = 300
SILENCE_DURATION = 0.8

WAKE_PHRASES = [
    "hey nova",
    "hey noah",
    "hey nora",
    "hanover"
]

SLEEP_PHRASES = [
    "go to sleep",
    "sleep nova",
    "stop listening",
    "end conversation"
]


# =========================
# MEMORY
# =========================

if os.path.exists(MEMORY_FILE):

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            conversation = json.load(file)

        if not isinstance(conversation, list):
            conversation = []

    except (json.JSONDecodeError, OSError):
        conversation = []

else:
    conversation = []


# =========================
# INITIALIZE VOICE ENGINE
# =========================

voice_engine = pyttsx3.init()

voice_engine.setProperty("rate", 180)
voice_engine.setProperty("volume", 1.0)

recognizer = sr.Recognizer()


# =========================
# LISTEN
# =========================

def listen(
    show_message=True,
    silence_threshold=SILENCE_THRESHOLD,
    silence_duration=SILENCE_DURATION,
    max_duration=5.0,
    show_errors=True
):

    if show_message:
        print("Nova is listening... Speak now!")

    chunk_duration = 0.1

    audio_chunks = []

    speech_started = False
    silent_chunks = 0

    max_chunks = int(max_duration / chunk_duration)

    calibration_chunks = 3
    noise_levels = []

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        device=DEVICE,
        channels=1,
        dtype="int16"
    ) as stream:

        for index in range(max_chunks):

            recording, overflowed = stream.read(
                int(chunk_duration * SAMPLE_RATE)
            )

            audio_chunks.append(recording.copy())

            audio_float = recording.astype(np.float32)

            volume = np.sqrt(
                np.mean(audio_float ** 2)
            )

            # Measure background noise.
            if index < calibration_chunks:

                noise_levels.append(volume)
                continue

            noise_floor = np.mean(noise_levels)

            active_threshold = max(
                silence_threshold,
                noise_floor * 2.5
            )

            if volume > active_threshold:

                speech_started = True
                silent_chunks = 0

            elif speech_started:

                silent_chunks += 1

                if silent_chunks >= int(
                    silence_duration / chunk_duration
                ):
                    break

    if not speech_started:

        if show_errors:
            print("Nova didn't hear you.")

        return ""

    recording = np.concatenate(audio_chunks)

    write(
        "voice.wav",
        SAMPLE_RATE,
        recording
    )

    with sr.AudioFile("voice.wav") as source:

        audio = recognizer.record(source)

    try:

        text = recognizer.recognize_google(audio)

        return text.strip()

    except sr.UnknownValueError:

        if show_errors:
            print("Nova couldn't understand what you said.")

        return ""

    except sr.RequestError:

        if show_errors:
            print("Nova couldn't connect to speech recognition.")

        return ""


# =========================
# THINK
# =========================

def think(user_text):

    conversation.append({
        "role": "user",
        "content": user_text
    })

    system_message = {
        "role": "system",
        "content": """
You are Nova, a fast and friendly personal AI assistant.

Speak naturally and conversationally.

Give short, direct answers for simple questions.

Avoid unnecessary explanations.

Usually answer in 1 to 3 sentences.

Be helpful, intelligent and friendly.

Speak as if you are having a real conversation with the user.
"""
    }

    # Use recent messages for faster processing.
    # Keep the complete conversation in memory.json.

    messages = [
        system_message
    ]

    messages.extend(conversation[-8:])

    response = ollama.chat(
        model=MODEL,
        messages=messages,
        keep_alive="10m",
        options={
            "num_predict": 80,
            "num_ctx": 2048,
            "temperature": 0.5
        }
    )

    answer = response["message"]["content"]

    answer = answer.replace("*", "")
    answer = answer.replace("#", "")
    answer = answer.replace("\n", " ").strip()

    conversation.append({
        "role": "assistant",
        "content": answer
    })

    # Save the complete conversation.

    with open(MEMORY_FILE, "w", encoding="utf-8") as file:

        json.dump(
            conversation,
            file,
            indent=4,
            ensure_ascii=False
        )

    return answer


# =========================
# SPEAK
# =========================

def speak(text):

    if not text or not text.strip():
        return

    print("Nova is speaking...")

    try:

        voice_engine.say(text)
        voice_engine.runAndWait()

    except Exception as error:

        print("Voice error:", error)

    print("Nova finished speaking.")


# =========================
# WAKE WORD
# =========================

def wake_word():

    print("\nNova is waiting for 'Hey Nova'...")

    while True:

        user_text = listen(
            show_message=False,
            silence_threshold=250,
            silence_duration=0.7,
            max_duration=4.0,
            show_errors=False
        )

        if not user_text:
            continue

        user_text = re.sub(
            r"[^\w\s]",
            "",
            user_text.lower()
        ).strip()

        print("Wake word heard:", user_text)

        for phrase in WAKE_PHRASES:

            if user_text.startswith(phrase):

                remaining_text = user_text[len(phrase):].strip()

                print("Nova activated!")

                # The user has already given a command.
                if remaining_text:
                    return remaining_text

                # Wake word only.
                speak("Yes?")
                return None


# =========================
# PROCESS COMMAND
# =========================

def process_command(user_text):

    if not user_text:
        return

    print("\nYou said:", user_text)

    try:

        response = think(user_text)

        if response:

            print("\nNova:", response)

            # Always speak the answer.
            speak(response)

        else:

            speak("Sorry, I couldn't generate an answer.")

    except Exception as error:

        print("AI error:", error)

        speak("Sorry, I encountered a problem while processing that.")


# =========================
# CONTINUOUS CONVERSATION
# =========================

def conversation_mode(first_command=None):

    print("\nNova is now in conversation mode.")
    print("You can keep talking without repeating Hey Nova.")
    print("Say 'Go to sleep' when you want to finish.")

    while True:

        # Process the command that followed the wake word.
        if first_command:

            user_text = first_command
            first_command = None

        else:

            # Keep listening even when there is a pause.
            user_text = listen(
                show_message=False,
                silence_threshold=SILENCE_THRESHOLD,
                silence_duration=SILENCE_DURATION,
                max_duration=CONVERSATION_LISTEN_SECONDS,
                show_errors=False
            )

        # Silence does not end the conversation.
        if not user_text:
            continue

        cleaned_text = re.sub(
            r"[^\w\s]",
            "",
            user_text.lower()
        ).strip()

        print("\nYou said:", user_text)

        # Check for a sleep command.
        if any(
            phrase in cleaned_text
            for phrase in SLEEP_PHRASES
        ):

            speak(
                "Alright. I'll go to sleep now. "
                "Say Hey Nova when you need me."
            )

            print("\nReturning to wake-word mode.")

            return

        # Process the user's question and speak the answer.
        process_command(user_text)


# =========================
# MAIN PROGRAM
# =========================

def main():

    print("Starting Nova...")

    try:

        while True:

            # Wait for the wake word.
            first_command = wake_word()

            # Enter continuous conversation mode.
            conversation_mode(first_command)

    except KeyboardInterrupt:

        print("\nNova has been stopped.")

        speak("Goodbye!")


# =========================
# START NOVA
# =========================

if __name__ == "__main__":
    main()
