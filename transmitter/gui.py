"""Tkinter GUI for the audio transmitter."""
from __future__ import annotations

import asyncio
import contextlib
import threading
from queue import Empty, Queue
from tkinter import BOTH, END, LEFT, RIGHT, Button, Entry, Frame, Label, Listbox, Scale, Scrollbar, Tk, HORIZONTAL
from tkinter import messagebox, simpledialog

from common.config import TransmitterConfig, load_config, save_config
from common.logging_utils import configure_logging
from .main import TransmitterClient

LOGGER = configure_logging(name="transmitter.gui")


class TransmitterApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Realtime Audio Transmitter")
        self.config_data = load_config()
        self.tx_config = TransmitterConfig(**self.config_data.get("transmitter", {}))

        self._status_queue: "Queue[dict]" = Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: TransmitterClient | None = None

        self._build_ui()
        self._poll_status_queue()

    def _build_ui(self) -> None:
        control_frame = Frame(self.root)
        control_frame.pack(fill=BOTH, expand=False, padx=10, pady=10)

        self.start_button = Button(control_frame, text="Start", command=self.start_stream)
        self.start_button.pack(side=LEFT, padx=5)

        self.stop_button = Button(control_frame, text="Stop", state="disabled", command=self.stop_stream)
        self.stop_button.pack(side=LEFT, padx=5)

        save_button = Button(control_frame, text="Save Config", command=self._save_config)
        save_button.pack(side=LEFT, padx=5)

        url_frame = Frame(self.root)
        url_frame.pack(fill=BOTH, padx=10)
        Label(url_frame, text="Server URL:").pack(side=LEFT)
        self.url_entry = Entry(url_frame, width=40)
        self.url_entry.insert(0, self.tx_config.server_url)
        self.url_entry.pack(side=LEFT, padx=5)

        level_frame = Frame(self.root)
        level_frame.pack(fill=BOTH, padx=10, pady=5)
        Label(level_frame, text="Input level:").pack(side=LEFT)
        self.level_scale = Scale(level_frame, from_=0, to=100, orient=HORIZONTAL, length=250, showvalue=False)
        self.level_scale.pack(side=LEFT, padx=5)

        status_frame = Frame(self.root)
        status_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

        scrollbar = Scrollbar(status_frame)
        scrollbar.pack(side=RIGHT, fill="y")

        self.status_list = Listbox(status_frame, yscrollcommand=scrollbar.set, height=12)
        self.status_list.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.config(command=self.status_list.yview)

        self.status_label = Label(self.root, text="Idle")
        self.status_label.pack(fill=BOTH, padx=10, pady=5)

    def _save_config(self) -> None:
        self.tx_config.server_url = self.url_entry.get().strip() or self.tx_config.server_url
        self.config_data["transmitter"] = {
            "server_url": self.tx_config.server_url,
            "input_device": self.tx_config.input_device,
        }
        save_config(self.config_data)
        messagebox.showinfo("Config Saved", "Transmitter configuration saved to audio_suite.yaml")

    def start_stream(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        url = self.url_entry.get().strip()
        if url:
            self.tx_config.server_url = url
        self.status_list.delete(0, END)
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.status_label.config(text="Connecting...")

    def stop_stream(self) -> None:
        if not self._loop or not self._client:
            return
        self._loop.call_soon_threadsafe(self._client.request_stop)
        self.status_label.config(text="Stopping...")
        if self._thread:
            self._thread.join(timeout=3)
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.level_scale.set(0)

    def _run_thread(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._tx_main())
        except Exception as exc:  # pragma: no cover - GUI diagnostics
            LOGGER.exception("Transmitter thread crashed: %s", exc)
            self._status_queue.put({"event": "error", "message": str(exc)})
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
            self._loop = None
            self._client = None
            self._status_queue.put({"event": "_gui_stopped"})

    async def _tx_main(self) -> None:
        self._client = TransmitterClient(self.tx_config)
        status_task = asyncio.create_task(self._drain_status())
        try:
            await self._client.run()
        finally:
            status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await status_task

    async def _drain_status(self) -> None:
        assert self._client is not None
        while True:
            event = await self._client.status_queue.get()
            self._status_queue.put(event)

    def _poll_status_queue(self) -> None:
        try:
            while True:
                event = self._status_queue.get_nowait()
                self._render_status(event)
        except Empty:
            pass
        self.root.after(150, self._poll_status_queue)

    def _render_status(self, event: dict) -> None:
        text = event.get("event", "event")
        if text == "_gui_stopped":
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.status_label.config(text="Stopped")
            return
        self.status_list.insert(END, f"{text}: {event}")
        self.status_list.yview_moveto(1)
        if text == "connected":
            self.status_label.config(text=f"Connected to {self.tx_config.server_url}")
        elif text == "capture_started":
            self.status_label.config(text="Streaming live audio...")
        elif text == "level":
            self.level_scale.set(int(float(event.get("value", 0)) * 100))
        elif text == "disconnected":
            self.status_label.config(text="Disconnected")
        elif text == "error":
            self.status_label.config(text=f"Error: {event.get('message', '')}")


def launch() -> None:
    root = Tk()
    app = TransmitterApp(root)
    root.protocol("WM_DELETE_WINDOW", app.stop_stream)
    root.mainloop()


if __name__ == "__main__":
    launch()
