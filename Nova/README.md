# Nova - personal AI desktop assistant

Nova is a voice-controlled assistant for Windows. It listens for "Hey Nova", understands spoken
instructions, opens applications, manages files, controls the system, and chats using a local
model (Ollama) or an online one. It has an animated window that shows what it is actually doing.

## Run it (exact steps)

1. Open **Windows PowerShell** (Start menu, type `PowerShell`).
2. Go to your Nova folder (adjust the path to where your Nova folder is):
   ```powershell
   cd C:\Users\<you>\path\to\Nova
   ```
3. Activate your existing virtual environment:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   If PowerShell refuses ("running scripts is disabled"), run once:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and try again.
4. Install the new packages into that environment (one time):
   ```powershell
   pip install -r requirements.txt
   ```
5. Make sure Ollama is running with your model (one time: `ollama pull llama3.2:1b`).
6. Start Nova:
   ```powershell
   python main.py
   ```
   Text-only (no microphone, no window): `python main.py --cli`. Window without voice: `python main.py --no-voice`.

There is no separate backend: Nova is one program; the window and the voice loop run together.

### Optional: online AI
Copy `.env.example` to `.env` and put your key after `ANTHROPIC_API_KEY=`. Without it Nova uses the local model only.

### Check it works (in this order)
1. `python -m pytest -q` - runs the automated tests (no microphone needed).
2. `python main.py --cli`, type `what is 12 times 8` (answers offline), then `open notepad`.
3. `python main.py`, click **Mic**, speak. Then try saying "Hey Nova, what time is it".
4. Wrong microphone? Run `python -c "import sounddevice; print(sounddevice.query_devices())"` and set
   `"input_device"` in `config.json` (created on first Save in Settings; default is 1, as in your original code).

## What you can say
- "Open Notepad / Calculator / Chrome / VS Code / File Explorer / my downloads folder / my music player"
- "Open Notepad and write down this note: remember to finish my assignment" (saves to Documents\Nova Notes and opens it)
- "Create a folder called Engineering Notes on my desktop, open Notepad, write down my fluid mechanics assignment, and save it inside that folder"
- "Increase / reduce the volume", "mute", "reduce my brightness", "take a screenshot", "battery", "storage", "what time is it"
- "Open Bluetooth settings", "lock my computer", "shut down / restart my computer" (asks first)
- "Search for Python tutorials", "Search for X and open the first result and summarize it" (needs internet)
- "Remember that my exam is on Friday", "What do you remember?", "Clear my conversation history"
- "Press ctrl s", "scroll down", "switch window", "click the Save button in Notepad"
- "Stop" while Nova is talking, "Go to sleep" to return to wake-word mode.

## How it works

```
mic -> voice/listener -> core/assistant -> core/planner -> automation/registry -> tools
                              |                 \-> intelligence/brain (local Ollama | online API)
                              +-> core/memory, core/permissions, core/network
                              +-> EventBus -> interface/ui (animated orb reads real state)
```
- `main_original.py` is your original file, untouched. `memory.json` keeps the same format.
- **Planner**: fast rules first (work offline); if a sentence looks like a command but the rules miss it, the
  model proposes tool calls, which are validated against the registry. Spoken text is never run as a shell command.
- **Permissions**: ordinary actions run directly. Sensitive (move/close/delete, clear history) need a yes.
  Critical (shutdown, restart, deleting 50+ items) need a clear yes. By voice Nova asks aloud; otherwise a dialog.
  If no confirmation channel exists the answer is "no". Deletes move files to `Nova\trash`. Files are never overwritten (a name like `todo (2).txt` is used).
- **Offline vs online**: a background check runs every 10 s. Fully offline: wake word is heard only if offline speech is
  set up (see below), speaking, apps, files, system controls, maths, local model chat. Internet-dependent: Google
  speech recognition, online AI, web search/reading. The window header shows the current mode.
- **Memory**: conversation in `memory.json`; facts you ask it to remember in `memories.json` (Settings > Memory: add/edit/delete).
  Passwords, keys and tokens are refused.

### Offline speech recognition (optional, recommended)
Without internet, voice input needs Vosk: `pip install vosk`, download a small English model
(e.g. `vosk-model-small-en-us`) from alphacephei.com/vosk/models, unzip it, and put its folder path in Settings > Vosk model folder.
Typing always works.

### Voice and wake word
Settings: wake phrase, speaking speed, voice (installed Windows voices), push-to-talk-only mode. Only offline pyttsx3 speech is implemented; there is no online TTS engine.

## Troubleshooting
- **"No module named ..."**: the venv isn't active, or `pip install -r requirements.txt` wasn't run.
- **Nova never wakes**: wrong `input_device`; speak closer; check the log in `logs\nova.log`; use the Mic button.
- **"No language model"**: start Ollama (`ollama serve`) and pull the model, or add an API key.
- **Nova hears herself / stop doesn't work**: use headphones. Interrupt detection on open speakers is best-effort.
- **Window doesn't open (tkinter)**: reinstall Python with "tcl/tk and IDLE" ticked, or use `--cli`.

## Known limitations
See `IMPLEMENTATION_REPORT.md`.
