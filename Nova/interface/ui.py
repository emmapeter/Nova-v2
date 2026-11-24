"""Animated Tkinter interface. Reflects the assistant's real state via the event bus.

Rendering is intentionally light: a fixed set of canvas items updated in place at ~30 fps
(~15 fps when idle), no images, no per-frame allocation.
"""
from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from core.config import Settings, save_settings
from core.events import State
from core.permissions import Level

BG, PANEL, FG, MUTED = "#0b0f1a", "#121829", "#e6ecff", "#8b97b8"
STATE_COLORS = {
    State.IDLE: "#4d7cff", State.LISTENING: "#2ee6c5", State.THINKING: "#b07cff",
    State.SPEAKING: "#5ab0ff", State.EXECUTING: "#ffb454", State.SUCCESS: "#4ade80", State.ERROR: "#ff5d6c",
}
STATE_LABELS = {
    State.IDLE: "Idle", State.LISTENING: "Listening...", State.THINKING: "Thinking...",
    State.SPEAKING: "Speaking...", State.EXECUTING: "Working on it...", State.SUCCESS: "Done", State.ERROR: "Something went wrong",
}
BARS = 48
CANVAS = 340


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * t):02x}" for x, y in zip(a, b))


class NovaUI:
    def __init__(self, assistant) -> None:
        self.assistant = assistant
        self.settings: Settings = assistant.settings
        self.root = tk.Tk()
        self.root.title(f"{self.settings.assistant_name} - Personal Assistant")
        self.root.geometry("1000x640")
        self.root.minsize(820, 560)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.state = State.IDLE
        self.color = STATE_COLORS[State.IDLE]
        self.level = 0.0  # smoothed mic level 0..1
        self.phase = 0.0
        self.state_since = time.time()
        assistant.ui_confirm = self._confirm_from_worker

        self._build()
        self.assistant.bus.publish("mode", self.assistant.mode_info())
        self.root.after(33, self._tick)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        left = tk.Frame(self.root, bg=BG)
        left.pack(side="left", fill="y", padx=18, pady=18)
        self.canvas = tk.Canvas(left, width=CANVAS, height=CANVAS, bg=BG, highlightthickness=0)
        self.canvas.pack()
        c = CANVAS / 2
        self.glow = [self.canvas.create_oval(c, c, c, c, outline="", fill=BG) for _ in range(4)]
        self.core = self.canvas.create_oval(c, c, c, c, outline="", fill=BG)
        self.bars = [self.canvas.create_line(c, c, c, c, width=3, capstyle="round", fill=BG) for _ in range(BARS)]
        self.status = tk.Label(left, text="Idle", fg=FG, bg=BG, font=("Segoe UI", 16, "bold"))
        self.status.pack(pady=(6, 0))
        self.modes = tk.Label(left, text="", fg=MUTED, bg=BG, font=("Segoe UI", 10), justify="center")
        self.modes.pack(pady=(4, 10))
        row = tk.Frame(left, bg=BG)
        row.pack()
        self._button(row, "Mic", self.assistant.push_to_talk).pack(side="left", padx=4)
        self._button(row, "Stop", self.assistant.stop_speaking).pack(side="left", padx=4)
        self._button(row, "Settings", self.open_settings).pack(side="left", padx=4)

        right = tk.Frame(self.root, bg=BG)
        right.pack(side="right", fill="both", expand=True, padx=(0, 18), pady=18)
        tk.Label(right, text="Conversation", fg=MUTED, bg=BG, anchor="w").pack(fill="x")
        self.chat = tk.Text(right, height=14, bg=PANEL, fg=FG, bd=0, wrap="word", font=("Segoe UI", 11),
                            padx=10, pady=8, state="disabled")
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_config("you", foreground="#9ec1ff")
        self.chat.tag_config("nova", foreground="#7ef0d4")
        self.chat.tag_config("note", foreground="#ffb454")
        tk.Label(right, text="Activity", fg=MUTED, bg=BG, anchor="w").pack(fill="x", pady=(10, 0))
        self.activity = tk.Listbox(right, height=6, bg=PANEL, fg=FG, bd=0, highlightthickness=0,
                                   font=("Consolas", 9), activestyle="none")
        self.activity.pack(fill="x")
        for e in self.assistant.activity[-30:]:
            self._add_activity(e)
        entry_row = tk.Frame(right, bg=BG)
        entry_row.pack(fill="x", pady=(10, 0))
        self.entry = tk.Entry(entry_row, bg=PANEL, fg=FG, insertbackground=FG, bd=0, font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self._send())
        self._button(entry_row, "Send", self._send).pack(side="right")

    @staticmethod
    def _button(parent, text, command):
        return tk.Button(parent, text=text, command=command, bg="#1c2540", fg=FG, activebackground="#2a3763",
                         activeforeground=FG, bd=0, padx=14, pady=6, font=("Segoe UI", 10))

    # ------------------------------------------------------------------ events
    def _send(self) -> None:
        text = self.entry.get().strip()
        if text:
            self.entry.delete(0, "end")
            self.assistant.submit_text(text)

    def _append(self, who: str, text: str, tag: str) -> None:
        self.chat.config(state="normal")
        self.chat.insert("end", f"{who}: ", tag)
        self.chat.insert("end", text + "\n\n")
        self.chat.see("end")
        self.chat.config(state="disabled")

    def _add_activity(self, e: dict) -> None:
        self.activity.insert("end", f"{'OK ' if e['ok'] else 'ERR'} {e['time']}  {e['what']}: {e['result']}")
        self.activity.itemconfig("end", fg="#7ef0d4" if e["ok"] else "#ff5d6c")
        self.activity.see("end")

    def _handle_events(self) -> None:
        for kind, payload in self.assistant.bus.drain():
            if kind == "state":
                self.state = payload
                self.state_since = time.time()
                self.status.config(text=STATE_LABELS[payload])
            elif kind == "level":
                target = min(1.0, float(payload) / 4000.0)
                self.level = max(target, self.level * 0.8)
            elif kind == "user" or kind == "heard":
                if kind == "user":
                    self._append("You", payload, "you")
            elif kind == "assistant":
                self._append(self.settings.assistant_name, payload, "nova")
            elif kind == "notice":
                self._append("Note", payload, "note")
            elif kind == "activity":
                self._add_activity(payload)
            elif kind == "mode":
                m = payload
                net = "Online" if m["online"] else "Offline"
                ai = {"online": f"Online AI ({m['model']})", "local": f"Local AI ({m['model']})",
                      "none": "No language model"}[m["ai"]]
                self.modes.config(text=f"{net}  |  {ai}\nSpeech recognition: {m['stt']}  |  Memory: {'on' if m['memory'] else 'off'}")
            elif kind == "confirm":
                self._show_confirm(*payload)

    # ------------------------------------------------------------------ confirmation dialogs
    def _confirm_from_worker(self, description: str, level: Level) -> bool:
        done, holder = threading.Event(), {"answer": False}
        self.assistant.bus.publish("confirm", (description, level, done, holder))
        done.wait(timeout=90)
        return holder["answer"]

    def _show_confirm(self, description, level, done, holder) -> None:
        title = "Critical action" if level == Level.CRITICAL else "Please confirm"
        holder["answer"] = messagebox.askyesno(title, f"{description}\n\nDo you want me to go ahead?",
                                               icon="warning", parent=self.root)
        done.set()

    # ------------------------------------------------------------------ animation
    def _tick(self) -> None:
        try:
            self._handle_events()
            self._draw()
        finally:
            busy = self.state != State.IDLE or self.level > 0.02
            self.root.after(33 if busy else 66, self._tick)

    def _draw(self) -> None:
        self.phase += 0.09
        target = STATE_COLORS[self.state]
        self.color = _mix(self.color, target, 0.15)
        c, p, s = CANVAS / 2, self.phase, self.state
        age = time.time() - self.state_since
        base = 62.0
        if s == State.IDLE:
            amp = 3 * math.sin(p * 0.6)
            energy = 0.12
        elif s == State.LISTENING:
            amp, energy = 4 + 22 * self.level, 0.2 + self.level
        elif s == State.THINKING:
            amp, energy = 4 * math.sin(p * 2.5), 0.25
        elif s == State.SPEAKING:  # simulated envelope: the TTS engine exposes no audio samples
            env = 0.5 + 0.5 * math.sin(p * 3.1) * math.sin(p * 1.3 + 1)
            amp, energy = 14 * env, 0.3 + 0.7 * env
        elif s == State.EXECUTING:
            amp, energy = 5 * math.sin(p * 4), 0.5
        elif s == State.SUCCESS:
            amp, energy = 10 * max(0.0, 1 - age), 0.2
        else:
            amp, energy = 8 * math.sin(p * 9) * max(0.0, 1 - age / 1.5), 0.1
        self.level *= 0.9
        r = base + amp
        for i, g in enumerate(self.glow):
            gr = r + 14 * (i + 1) + 3 * math.sin(p + i)
            self.canvas.coords(g, c - gr, c - gr, c + gr, c + gr)
            self.canvas.itemconfig(g, fill=_mix(BG, self.color, 0.16 / (i + 1)))
        self.canvas.coords(self.core, c - r, c - r, c + r, c + r)
        self.canvas.itemconfig(self.core, fill=_mix(BG, self.color, 0.85))
        spin = p * (2.0 if s == State.THINKING else 0.3 if s == State.EXECUTING else 0.0)
        for i, bar in enumerate(self.bars):
            a = 2 * math.pi * i / BARS + (spin if s == State.EXECUTING else 0)
            wave = 0.5 + 0.5 * math.sin(a * 3 + p * (6 if s in (State.SPEAKING, State.LISTENING) else 2))
            if s == State.THINKING:
                wave = max(0.0, math.cos(a - spin)) ** 3
            length = 6 + 46 * energy * wave
            r0 = r + 52
            self.canvas.coords(bar, c + r0 * math.cos(a), c + r0 * math.sin(a),
                               c + (r0 + length) * math.cos(a), c + (r0 + length) * math.sin(a))
            self.canvas.itemconfig(bar, fill=_mix(BG, self.color, 0.35 + 0.65 * wave))

    # ------------------------------------------------------------------ settings
    def open_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("520x520")
        win.configure(bg=BG)
        tabs = ttk.Notebook(win)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        general, memory = tk.Frame(tabs, bg=BG), tk.Frame(tabs, bg=BG)
        tabs.add(general, text="General")
        tabs.add(memory, text="Memory")
        s = self.settings
        vars_ = {
            "wake": tk.StringVar(value=s.wake_phrases[0] if s.wake_phrases else "hey nova"),
            "rate": tk.IntVar(value=s.tts_rate),
            "mode": tk.StringVar(value=s.force_mode),
            "local": tk.StringVar(value=s.local_model),
            "online_model": tk.StringVar(value=s.online_model),
            "ptt": tk.BooleanVar(value=s.push_to_talk),
            "mem": tk.BooleanVar(value=s.memory_enabled),
            "voice": tk.StringVar(value=s.tts_voice_id),
            "vosk": tk.StringVar(value=s.vosk_model_path),
        }

        def row(parent, label, widget_factory, r):
            tk.Label(parent, text=label, fg=FG, bg=BG, anchor="w").grid(row=r, column=0, sticky="w", padx=10, pady=6)
            w = widget_factory(parent)
            w.grid(row=r, column=1, sticky="ew", padx=10, pady=6)
            return w

        general.columnconfigure(1, weight=1)
        row(general, "Wake phrase", lambda p: tk.Entry(p, textvariable=vars_["wake"]), 0)
        row(general, "Speaking speed", lambda p: tk.Scale(p, from_=100, to=260, orient="horizontal",
                                                          variable=vars_["rate"], bg=BG, fg=FG, highlightthickness=0), 1)
        voices = []
        try:
            from voice.speaker import list_voices
            voices = list_voices()
        except Exception:
            pass
        voice_box = row(general, "Voice", lambda p: ttk.Combobox(p, values=[f"{n}" for _i, n in voices] or ["(default)"],
                                                                 state="readonly"), 2)
        for idx, (vid, _n) in enumerate(voices):
            if vid == s.tts_voice_id:
                voice_box.current(idx)
        row(general, "AI mode", lambda p: ttk.Combobox(p, textvariable=vars_["mode"], values=["auto", "offline", "online"],
                                                       state="readonly"), 3)
        row(general, "Local model (Ollama)", lambda p: tk.Entry(p, textvariable=vars_["local"]), 4)
        row(general, "Online model", lambda p: tk.Entry(p, textvariable=vars_["online_model"]), 5)
        row(general, "Vosk model folder", lambda p: tk.Entry(p, textvariable=vars_["vosk"]), 6)
        row(general, "Push-to-talk only", lambda p: tk.Checkbutton(p, variable=vars_["ptt"], bg=BG, fg=FG,
                                                                    selectcolor=PANEL, activebackground=BG), 7)
        row(general, "Memory enabled", lambda p: tk.Checkbutton(p, variable=vars_["mem"], bg=BG, fg=FG,
                                                                 selectcolor=PANEL, activebackground=BG), 8)
        tk.Label(general, text="API keys are read from the .env file, never stored here.", fg=MUTED, bg=BG).grid(
            row=9, column=0, columnspan=2, pady=8)

        def save() -> None:
            wake = vars_["wake"].get().strip().lower()
            if wake:
                s.wake_phrases = [wake] + [p for p in s.wake_phrases if p != wake]
            s.tts_rate = int(vars_["rate"].get())
            s.force_mode = vars_["mode"].get()
            s.local_model = vars_["local"].get().strip() or s.local_model
            s.online_model = vars_["online_model"].get().strip() or s.online_model
            s.vosk_model_path = vars_["vosk"].get().strip()
            s.push_to_talk = bool(vars_["ptt"].get())
            s.memory_enabled = self.assistant.memory.enabled = bool(vars_["mem"].get())
            picked = voice_box.current()
            if 0 <= picked < len(voices):
                s.tts_voice_id = voices[picked][0]
            save_settings(s)
            self.assistant.brain.recheck_local()
            self.assistant.bus.publish("mode", self.assistant.mode_info())
            win.destroy()

        tk.Button(general, text="Save", command=save, bg="#2a3763", fg=FG, bd=0, padx=16, pady=6).grid(
            row=10, column=0, columnspan=2, pady=10)
        self._build_memory_tab(memory)

    def _build_memory_tab(self, parent) -> None:
        mem = self.assistant.memory
        box = tk.Listbox(parent, bg=PANEL, fg=FG, bd=0, highlightthickness=0, activestyle="none")
        box.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh() -> None:
            box.delete(0, "end")
            for f in mem.facts:
                box.insert("end", f"{f['created']}  {f['text']}")

        def add() -> None:
            text = simpledialog.askstring("Add memory", "What should I remember?", parent=parent)
            if text:
                ok, msg = mem.add_fact(text)
                if not ok:
                    messagebox.showinfo("Not saved", msg, parent=parent)
                refresh()

        def edit() -> None:
            sel = box.curselection()
            if sel:
                fact = mem.facts[sel[0]]
                text = simpledialog.askstring("Edit memory", "Update:", initialvalue=fact["text"], parent=parent)
                if text and not mem.edit_fact(fact["id"], text):
                    messagebox.showinfo("Not saved", "That looks sensitive, so I won't store it.", parent=parent)
                refresh()

        def delete() -> None:
            sel = box.curselection()
            if sel:
                mem.delete_fact(mem.facts[sel[0]]["id"])
                refresh()

        def clear_history() -> None:
            if messagebox.askyesno("Clear history", "Delete the saved conversation history?", parent=parent):
                mem.clear_conversation()

        row = tk.Frame(parent, bg=BG)
        row.pack(pady=(0, 10))
        for label, cmd in (("Add", add), ("Edit", edit), ("Delete", delete), ("Clear conversation history", clear_history)):
            self._button(row, label, cmd).pack(side="left", padx=4)
        refresh()

    def close(self) -> None:
        self.assistant.shutdown()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
