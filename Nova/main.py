"""Nova - personal AI desktop assistant.

    python main.py          # full assistant: animated window + voice
    python main.py --cli    # text-only terminal mode (no microphone, no window)
    python main.py --no-voice   # window only, typed input

The original single-file version is preserved as main_original.py.
"""
import argparse
import sys

from core.assistant import Assistant
from core.config import load_settings
from utils.logging_setup import setup_logging


def run_cli(assistant: Assistant) -> None:
    print(f"{assistant.settings.assistant_name} (text mode). Type 'quit' to exit.")
    assistant.ui_confirm = lambda desc, _level: input(f"{desc}\nGo ahead? (yes/no) ").strip().lower() in ("y", "yes")
    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quit", "exit"):
            break
        if text:
            reply = assistant.handle_text(text)
            print(f"{assistant.settings.assistant_name}: {reply}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova personal assistant")
    parser.add_argument("--cli", action="store_true", help="text-only terminal mode")
    parser.add_argument("--no-voice", action="store_true", help="disable microphone and speech")
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.log_dir)

    if args.cli:
        assistant = Assistant(settings, voice=False)
        assistant.network.start()
        assistant._apply_network(assistant.network.online)
        run_cli(assistant)
        assistant.shutdown()
        return 0

    try:
        from interface.ui import NovaUI
    except ImportError as exc:
        print(f"The window needs tkinter ({exc}). Re-run the Python installer with 'tcl/tk' selected, "
              "or use: python main.py --cli")
        return 1

    assistant = Assistant(settings, voice=not args.no_voice)
    ui = NovaUI(assistant)
    assistant.start()
    ui.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
