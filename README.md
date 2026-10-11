# PS1 OSD Launcher

A minimalist game launcher with a PlayStation 1 BIOS-style interface, designed to run on **Raspberry Pi OS Lite** — no desktop, no X11, no RetroArch as a frontend.

![PS1 OSD Launcher](assets/readme.png)

## Overview

PS1 OSD Launcher boots directly into a controller-navigable menu styled after the original PS1 BIOS screen. It recreates the Sony boot intro straight from a PS1 BIOS (or plays an intro video), then presents a configurable list of apps (emulators, scripts, system commands) that launch on selection and return to the menu when closed.

Built with Python and pygame, it runs over KMSDRM (direct framebuffer) on the Pi and opens as a regular window on Linux desktops or WSL for development.

## Features

- Sony PS1 boot intro rebuilt from the BIOS: GPU commands and SPU writes recorded from the real BIOS code, rasterized and synthesized in pygame (falls back to `assets/intro.mp4`)
- OSD menu visually faithful to the original PS1 BIOS
- 100% controller-driven navigation — no keyboard or mouse required
- Universal controller support via **SDL2 GameController API** + [SDL_GameControllerDB](https://github.com/mdqinc/SDL_GameControllerDB) (2200+ mappings)
- Apps configured via `apps.ini` — add any emulator, script, or shell command
- Auto-update via `git pull` on boot
- Launched automatically via **systemd** at startup

## How It Works

```
Boot
 └── systemd: git pull (auto-update)
      └── venv/bin/python3 launcher.py
            └── pygame: owns the display from boot to shutdown
                  ├── replays the BIOS intro (bios_intro.trace) or intro.mp4
                  ├── reads apps.ini
                  ├── controller navigation
                  └── launches selected app
                        ├── releases display (DRM)
                        ├── subprocess: runs app
                        └── returns to menu when app exits
```

## Requirements

### Hardware
- Raspberry Pi 5 (tested with 4GB RAM)
- USB or Bluetooth controller (PS, Xbox, 8BitDo, or any generic)

### OS
- Raspberry Pi OS Lite (Bookworm)
- No desktop environment, no X11

### Dependencies
```
python3-pygame   (apt)
python3-numpy    (apt)
python3-venv     (apt)
ffpyplayer       (pip, inside the project venv — bundles its own ffmpeg)
```

## Project Structure

```
ps1-osd-launcher/
├── launcher.py               # Entry point
├── apps.ini                  # App configuration (user-editable)
├── requirements.txt          # Python dependencies
├── install.sh                # Pi installation script
├── assets/
│   ├── ps1-bios.jpg          # Menu background
│   ├── bios_intro.trace      # Sony intro recorded from your BIOS — generate it (see below)
│   ├── intro.mp4             # Fallback intro video
│   ├── gamecontrollerdb.txt  # SDL2 controller mappings
│   └── fonts/
│       └── PressStart2P.ttf  # Menu font
├── src/
│   ├── config.py             # apps.ini reader
│   ├── bios_intro.py         # Plays the BIOS intro (video + streamed audio)
│   ├── psx/                  # PS1 GPU/SPU software models + trace replay
│   ├── intro.py              # Intro video playback (fallback)
│   ├── controller.py         # Normalized controller input (SDL2)
│   └── menu.py               # OSD menu renderer
├── tools/
│   ├── psxtrace/             # Minimal PS1 emulator that records the BIOS intro
│   └── make_intro_trace.sh   # Builds psxtrace and writes assets/bios_intro.trace
└── systemd/
    └── ps1-osd-launcher.service
```

## Sony Intro from the BIOS

The intro isn't a video inside the BIOS: it's code. The BIOS draws the logo
with GPU polygons and textured sprites and plays the jingle by driving the SPU
(ADPCM samples, ADSR envelopes, reverb). So the launcher recreates it in two
steps:

1. **Record** — `tools/psxtrace` is a small PS1 machine (R3000A CPU, DMA,
   timers, interrupts, an empty CD drive) that boots the BIOS with no disc and
   records every GPU command and SPU register/RAM write, with timestamps,
   until the shell first talks to the CD drive (the end of the Sony screen).
2. **Replay** — `src/psx/gpu.py` rasterizes the commands like the PS1 GPU
   (top-left fill rule, gouraud, CLUT textures, 4x4 dithering, 480i fields)
   and `src/psx/spu.py` synthesizes the audio like the SPU (ADPCM, gaussian
   interpolation, ADSR, reverb with FIR resampling). Audio is rendered in a
   separate process and streamed to the mixer in sync with the video.

The trace is derived from Sony's BIOS, so it isn't versioned. Generate it from
your own BIOS dump (a C compiler and `make` are needed):

```bash
tools/make_intro_trace.sh scph5501.bin   # writes assets/bios_intro.trace
```

Without the trace, the launcher falls back to `assets/intro.mp4`.

## Configuring Apps (`apps.ini`)

```ini
[apps]
RetroArch   = retroarch
Duckstation = duckstation-qt
PPSSPP      = ppsspp
Shutdown    = !sudo shutdown -h now
Reboot      = !sudo reboot
```

- **Key** = label shown in the menu
- **Value** = shell command to execute
- Prefix `!` for system commands (shutdown, reboot)
- Order in the file determines menu order
- Edit via SSH and restart the service to apply

## Installation on Raspberry Pi

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/ps1-osd-launcher.git ~/ps1-osd-launcher
cd ~/ps1-osd-launcher
```

### 2. Edit the install script

Open `install.sh` and update `INSTALL_DIR` to match your clone path:

```bash
INSTALL_DIR="/home/<your-username>/ps1-osd-launcher"
```

Also update the sudoers line with your Pi username.

### 3. Add the intro (optional)

Generate `assets/bios_intro.trace` (see [Sony Intro from the BIOS](#sony-intro-from-the-bios)) on the Pi or on your machine and copy it:

```bash
# From your local machine:
scp assets/bios_intro.trace <user>@<pi-ip>:~/ps1-osd-launcher/assets/
```

`intro.mp4` is used when the trace is absent; with neither, the launcher skips the intro.

### 4. Run the installer

```bash
bash install.sh
```

The installer:
- Installs `python3-pygame`, `python3-numpy` and `python3-venv` via apt
- Creates a venv (with system site packages) and installs `ffpyplayer` into it
- Copies the systemd service to `/etc/systemd/system/`
- Grants passwordless `shutdown`/`reboot`/launcher-restart permission
- Silences the boot for a console-like startup:
  - `disable_splash=1` in `/boot/firmware/config.txt` (skips the rainbow splash)
  - `quiet loglevel=3 logo.nologo vt.global_cursor_default=0` in `/boot/firmware/cmdline.txt` (hides kernel text and cursor; a backup is kept at `cmdline.txt.bak` — restore it to revert)
- Enables and starts the service

The launcher service starts right after filesystems and udev are up (before the rest of the boot). After the intro ends, the launcher holds on the last frame until `systemctl is-system-running` leaves `starting` (30s safety timeout), then shows the menu.

### 5. Disable any previous launcher (if applicable)

```bash
sudo systemctl disable retroarch.service
sudo systemctl stop retroarch.service
```

## Updating

Push from your machine — the Pi pulls automatically on the next boot. To apply immediately:

```bash
# On the Pi:
cd ~/ps1-osd-launcher && git pull
sudo systemctl restart ps1-osd-launcher
```

## Development (Linux / WSL)

```bash
# Install dependencies
sudo apt install python3-pygame
pip install ffpyplayer

# Run
python3 launcher.py
```

On WSL with WSLg, a normal pygame window opens. On the Pi, it uses KMSDRM directly. Controller detection is automatic — keyboard works as fallback (arrow keys + Enter + Escape).

## Controller Mapping

| Action        | PS           | Xbox       | Keyboard |
|---------------|--------------|------------|----------|
| Navigate      | D-pad ↑↓     | D-pad ↑↓   | ↑↓       |
| Confirm       | X (Cross)    | A          | Enter    |
| Back / Quit   | O (Circle)   | B          | Escape   |

Works with any controller in [SDL_GameControllerDB](https://github.com/mdqinc/SDL_GameControllerDB).

## Logs and Debugging

```bash
# Live logs
sudo journalctl -u ps1-osd-launcher -f

# Last 50 lines
sudo journalctl -u ps1-osd-launcher -n 50 --no-pager

# Restart manually
sudo systemctl restart ps1-osd-launcher
```

## License

MIT License — see [LICENSE](LICENSE) for details.
