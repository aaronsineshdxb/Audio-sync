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
- `client/` — audio reception, buffering, playback, and client GUI

## Requirements

- Python 3.10+
- A working audio input/output device
- Dependencies in `requirements.txt`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the server first, then launch the transmitter and one or more clients using the entry points in their respective directories.

## Status

This project is an active prototype. The next milestones are completing the shared utilities, server controls, and end-to-end GUI workflows.
