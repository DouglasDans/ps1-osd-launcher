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
│   ├── bios_intro.wav        # Its jingle, pre-rendered from the trace
│   ├── bios_textures/        # Optional HD logo textures (Real-ESRGAN)
│   ├── intro.mp4             # Fallback intro video
│   ├── gamecontrollerdb.txt  # SDL2 controller mappings
│   └── fonts/
│       └── PressStart2P.ttf  # Menu font
├── src/
│   ├── config.py             # apps.ini reader
│   ├── bios_intro.py         # Plays the BIOS intro (replayed video + pre-rendered audio)
│   ├── psx/                  # PS1 GPU/SPU software models + trace replay
│   ├── intro.py              # Intro video playback (fallback)
│   ├── controller.py         # Normalized controller input (SDL2)
│   └── menu.py               # OSD menu renderer
├── tools/
│   ├── psxtrace/             # Minimal PS1 emulator that records the BIOS intro
│   └── make_bios_intro.sh    # Builds psxtrace, writes assets/bios_intro.{trace,wav}
└── systemd/
    └── ps1-osd-launcher.service
```

## Sony Intro from the BIOS

The intro isn't a video inside the BIOS: it's code. The BIOS draws the logo
with GPU polygons and textured sprites and plays the jingle by driving the SPU
(ADPCM samples, ADSR envelopes, reverb). So the launcher recreates it in two
steps:

1. **Record** — `tools/psxtrace` is a small PS1 machine (R3000A CPU, DMA,
   timers, interrupts, a CD drive with no disc) that boots the BIOS and
   records every GPU command and SPU register/RAM write, with timestamps.
   The drive keeps "spinning up" for a while before reporting no disc, like a
   real one, so the BIOS itself holds the Sony screen; the recording ends
   where it cuts to its menu (plus the SPU tail: the shell keys the jingle
   off and lets the reverb fade).
2. **Replay** — `src/psx/gpu.py` keeps the PS1 GPU's VRAM (uploads, CLUT
   textures, 15-bit drawing) and `src/psx/hires.py` draws the same commands
   the way DuckStation's enhanced renderer does: at the screen resolution,
   progressive instead of 480i, true color without dithering, and textures
   filtered with JINC2 (pre-filtered before the intro starts) or replaced by
   HD versions (see below).
   `src/psx/spu.py` synthesizes the audio like the SPU (ADPCM, gaussian
   interpolation, ADSR, reverb with FIR resampling). The audio is
   pre-rendered to a WAV when the trace is recorded; at the cut the launcher
   switches to the purple BIOS background and then the menu.

Both files are derived from Sony's BIOS, so they aren't versioned. Generate
them from your own BIOS dump (a C compiler and `make` are needed):

```bash
tools/make_bios_intro.sh scph5501.bin   # writes assets/bios_intro.{trace,wav}
CD_SPINUP=2 tools/make_bios_intro.sh scph5501.bin   # shorter wait on the logo
```

Without the trace, the launcher falls back to `assets/intro.mp4`; without the
WAV, the intro plays silently.

**HD textures (optional).** Like DuckStation's texture replacement packs, the
logo textures can be swapped for upscaled versions, drawn with bilinear
filtering (without them, JINC2 filtering is used). They are made with
[Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/releases) (the
`realesrgan-ncnn-vulkan` release, which runs on any Vulkan GPU) and the
`realesrgan-x4plus-anime` model, which keeps lettering sharp:

```bash
REALESRGAN=/path/to/realesrgan-ncnn-vulkan tools/make_bios_intro.sh scph5501.bin
# also writes assets/bios_textures/<hash>.png
```

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

Generate `assets/bios_intro.trace` and `assets/bios_intro.wav` (see [Sony Intro from the BIOS](#sony-intro-from-the-bios)) on the Pi or on your machine and copy them:

```bash
# From your local machine:
scp -r assets/bios_intro.trace assets/bios_intro.wav assets/bios_textures <user>@<pi-ip>:~/ps1-osd-launcher/assets/
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
