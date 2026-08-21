"""Tkinter GUI for the audio client/player."""
from __future__ import annotations

import asyncio
import contextlib
import threading
from queue import Empty, Queue
from tkinter import BOTH, END, LEFT, RIGHT, Button, Entry, Frame, Label, Listbox, Scrollbar, Tk
from tkinter import messagebox

from common.config import ClientConfig, load_config, save_config
from common.logging_utils import configure_logging
from .main import AudioClient

LOGGER = configure_logging(name="client.gui")


class ClientApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Realtime Audio Client")
        self.config_data = load_config()
        self.client_config = ClientConfig(**self.config_data.get("client", {}))

        self._status_queue: "Queue[dict]" = Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: AudioClient | None = None

        self._build_ui()
        self._poll_status_queue()

    def _build_ui(self) -> None:
        control_frame = Frame(self.root)
        control_frame.pack(fill=BOTH, expand=False, padx=10, pady=10)

        self.connect_button = Button(control_frame, text="Connect", command=self.start_client)
        self.connect_button.pack(side=LEFT, padx=5)

        self.disconnect_button = Button(control_frame, text="Disconnect", state="disabled", command=self.stop_client)
        self.disconnect_button.pack(side=LEFT, padx=5)

        save_button = Button(control_frame, text="Save Config", command=self._save_config)
        save_button.pack(side=LEFT, padx=5)

        url_frame = Frame(self.root)
        url_frame.pack(fill=BOTH, padx=10)
        Label(url_frame, text="Server URL:").pack(side=LEFT)
        self.url_entry = Entry(url_frame, width=40)
        self.url_entry.insert(0, self.client_config.server_url)
        self.url_entry.pack(side=LEFT, padx=5)

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
        url = self.url_entry.get().strip()
        if url:
            self.client_config.server_url = url
        self.config_data["client"] = {
            "server_url": self.client_config.server_url,
            "output_device": self.client_config.output_device,
        }
        save_config(self.config_data)
        messagebox.showinfo("Config Saved", "Client configuration saved to audio_suite.yaml")

    def start_client(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        url = self.url_entry.get().strip()
        if url:
            self.client_config.server_url = url
        self.status_list.delete(0, END)
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()
        self.connect_button.config(state="disabled")
        self.disconnect_button.config(state="normal")
        self.status_label.config(text="Connecting...")

    def stop_client(self) -> None:
        if not self._loop or not self._client:
            return
        self._loop.call_soon_threadsafe(self._client.request_stop)
        self.status_label.config(text="Disconnecting...")
        if self._thread:
            self._thread.join(timeout=3)

    def _run_thread(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._client_main())
        except Exception as exc:  # pragma: no cover - GUI diagnostics
            LOGGER.exception("Client thread crashed: %s", exc)
            self._status_queue.put({"event": "error", "message": str(exc)})
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
            self._loop = None
            self._client = None
            self._status_queue.put({"event": "_gui_stopped"})

    async def _client_main(self) -> None:
        self._client = AudioClient(self.client_config)
        try:
            await self._client.run()
        finally:
            self._status_queue.put({
                "event": "stats",
                "received": self._client.received_frames,
                "invalid": self._client.invalid_frames,
            })

    def _poll_status_queue(self) -> None:
        try:
            while True:
                event = self._status_queue.get_nowait()
                self._render_status(event)
        except Empty:
            pass
        self.root.after(200, self._poll_status_queue)

    def _render_status(self, event: dict) -> None:
        text = event.get("event", "event")
        if text == "_gui_stopped":
            self.connect_button.config(state="normal")
            self.disconnect_button.config(state="disabled")
            self.status_label.config(text="Disconnected")
            return
        if text == "stats":
            self.status_list.insert(END, f"Session stats: {event['received']} frames received, {event['invalid']} invalid")
            self.status_list.yview_moveto(1)
            return
        self.status_list.insert(END, f"{text}: {event}")
        self.status_list.yview_moveto(1)
        if text == "_connected":
            self.status_label.config(text=f"Connected to {self.client_config.server_url}")
        elif text == "error":
            self.status_label.config(text=f"Error: {event.get('message', '')}")


def launch() -> None:
    root = Tk()
    app = ClientApp(root)
    root.protocol("WM_DELETE_WINDOW", app.stop_client)
    root.mainloop()


if __name__ == "__main__":
    launch()
