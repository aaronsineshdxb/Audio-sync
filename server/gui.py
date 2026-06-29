"""Tkinter-based GUI wrapper for the realtime audio server."""
from __future__ import annotations

import asyncio
import contextlib
import threading
from queue import Empty, Queue
from tkinter import BOTH, END, LEFT, RIGHT, Button, Frame, Label, Listbox, Scrollbar, Tk
from tkinter import messagebox

from common.config import ServerConfig, load_config, save_config
from common.logging_utils import configure_logging
from .main import AudioServer

LOGGER = configure_logging(name="server.gui")


class ServerApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Realtime Audio Server")
        self.config_data = load_config()
        self.server_config = ServerConfig(**self.config_data.get("server", {}))

        self._status_queue: "Queue[dict]" = Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: AudioServer | None = None

        self._build_ui()
        self._poll_status_queue()

    def _build_ui(self) -> None:
        control_frame = Frame(self.root)
        control_frame.pack(fill=BOTH, expand=False, padx=10, pady=10)

        self.start_button = Button(control_frame, text="Start Server", command=self.start_server)
        self.start_button.pack(side=LEFT, padx=5)

        self.stop_button = Button(control_frame, text="Stop Server", state="disabled", command=self.stop_server)
        self.stop_button.pack(side=LEFT, padx=5)

        save_button = Button(control_frame, text="Save Config", command=self._save_config)
        save_button.pack(side=LEFT, padx=5)

        status_frame = Frame(self.root)
        status_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

        scrollbar = Scrollbar(status_frame)
        scrollbar.pack(side=RIGHT, fill="y")

        self.status_list = Listbox(status_frame, yscrollcommand=scrollbar.set, height=15)
        self.status_list.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.config(command=self.status_list.yview)

        self.status_label = Label(self.root, text="Server idle")
        self.status_label.pack(fill=BOTH, padx=10, pady=5)

    def _save_config(self) -> None:
        self.config_data["server"] = self.server_config.__dict__
        save_config(self.config_data)
        messagebox.showinfo("Config Saved", "Server configuration saved to audio_suite.yaml")

    def start_server(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.status_list.delete(0, END)
        self._thread = threading.Thread(target=self._run_server_thread, daemon=True)
        self._thread.start()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.status_label.config(text="Server starting...")

    def stop_server(self) -> None:
        if not self._loop:
            return
        if self._server:
            self._loop.call_soon_threadsafe(self._server.request_stop)
        self.status_label.config(text="Server stopping...")
        if self._thread:
            self._thread.join(timeout=3)
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

    def _run_server_thread(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._server_main())
        except Exception as exc:  # pragma: no cover - GUI diagnostics
            LOGGER.exception("Server thread crashed: %s", exc)
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
            self._loop = None
            self._server = None

    async def _server_main(self) -> None:
        self._server = AudioServer(self.server_config)
        status_task = asyncio.create_task(self._drain_status())
        try:
            await self._server.start()
        finally:
            status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await status_task

    async def _drain_status(self) -> None:
        assert self._server is not None
        while True:
            event = await self._server.status_queue.get()
            self._status_queue.put(event)

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
        self.status_list.insert(END, f"{text}: {event}")
        self.status_list.yview_moveto(1)
        if text == "server_started":
            self.status_label.config(text="Server running")
        elif text == "server_stopped":
            self.status_label.config(text="Server stopped")
        elif text == "client_joined":
            self.status_label.config(text=f"Clients connected: {event.get('connections')}")


def launch() -> None:
    root = Tk()
    app = ServerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.stop_server)
    root.mainloop()


if __name__ == "__main__":
    launch()
