# Realtime Audio Suite

A Python desktop suite for streaming live audio from one transmitter to multiple connected clients over WebSockets. It includes a server monitor, transmitter, and client player.

## Features

- 48 kHz mono PCM audio in 20 ms frames
- WebSocket-based broadcasting for up to 40 clients
- Tkinter GUIs for server monitoring, transmission, and playback
- Connection heartbeats, latency reporting, buffering, and level meters

## Project structure

- `common/` — shared audio framing, configuration, logging, and protocol utilities
- `server/` — WebSocket hub and server GUI
- `transmitter/` — local audio capture and transmission GUI
- `client/` — audio reception, buffering, playback, and headless client entry point

## Requirements

- Python 3.10+
- A working audio input/output device
- Dependencies in `requirements.txt`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or install as a package with console entry points:

```bash
pip install .
```

## Running

Start the server first, then the transmitter and one or more clients.

GUI launchers (Tkinter, work on macOS / Windows / Linux):

```bash
audio-server-gui          # or: python -m server.gui
audio-transmitter-gui     # or: python -m transmitter.gui
audio-client-gui          # or: python -m client.gui
```

Headless entry points:

```bash
audio-server
audio-transmitter
audio-client --no-playback   # receive/validate without opening an output device
```

## Cross-platform notes

- Signal handling uses `loop.add_signal_handler` on macOS/Linux and falls back
  to `signal.signal` on Windows, so Ctrl+C shutdown works everywhere.
- Audio I/O goes through PortAudio (`sounddevice`): install PortAudio via
  Homebrew on macOS, it ships with the Windows/Linux wheels.
- Configuration is read from `audio_suite.yaml` in the working directory and
  can be edited/saved from any of the GUIs.

## Status

This project is an active prototype. Completed: shared utilities, server core
and GUI, transmitter core and GUI, client core and GUI, packaging with console
entry points, cross-platform signal handling. Next milestones: latency/jitter
statistics in the server monitor, reconnection support in the client, and
packaged releases.
