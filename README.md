# Tri-Camera IMX708 Multi-Monitor (Zero-Install Architecture)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi%20%7C%20Linux%20%7C%20macOS-blue.svg)]()

A high-performance central monitoring and recording system for **three Raspberry Pi Camera Module 3 (Sony IMX708)** units running on three distinct Raspberry Pis.

Built with a **Zero-Install Edge Architecture**: no custom daemons, third-party packages, or pip libraries need to be installed on any of the Raspberry Pis. The central host manages video streaming, controls, and lossless MP4 recording entirely over SSH.

---

## Architecture Overview

```
[ Raspberry Pi 1 (IMX708) ] ──(Raw H.264 stream via SSH)──┐
[ Raspberry Pi 2 (IMX708) ] ──(Raw H.264 stream via SSH)──┼──► [ Central Monitoring Host ]
[ Raspberry Pi 3 (IMX708) ] ──(Raw H.264 stream via SSH)──┘    ├── 2x2 Low-Latency Grid UI
                                                                ├── Double-Click Maximize / Restore
                                                                ├── Per-Cam Exposure / Gain / Focus (cm)
                                                                └── Lossless MP4 Central Remuxing
```

* **Pi Edge Nodes:** Pure stock Raspberry Pi OS (Bookworm). The host launches the pre-installed `rpicam-vid` binary on-demand through an SSH pipe.
* **Central Host:** Decodes preview frames in real-time with FFmpeg into a PyQt5/PyQt6 2x2 grid.
* **Central Recording:** When recording is triggered, raw H.264 frames are remuxed directly into timestamped `.mp4` containers via `ffmpeg -c copy` (virtually 0% CPU re-encoding overhead on the host).

---

## Key Features

* **3 Simultaneous Feeds in 2x2 Grid:** Displays three camera streams alongside a dedicated Master Hub tile.
* **Double-Click to Maximize:** Double-click any camera viewport (or click *Maximize*) to expand that feed to the entire window; double-click again to return to the 2x2 grid.
* **Calibrated Manual Focus (in Centimeters):** The IMX708's voice-coil motor (VCM) is precisely controlled in optical dioptres ($Dioptre = 100 / \text{distance in cm}$), mapped to an intuitive centimeter slider (10 cm to 200+ cm / Infinity).
* **Exposure & Gain Sliders:** Manually adjust shutter speed (1 ms to 66 ms) and sensor analogue/digital gain (1.0x to 16.0x).
* **Software Orientation:** Per-camera Horizontal Flip (H-Flip) and Vertical Flip (V-Flip) toggles.
* **Independent & Master Recording:**
  * **● REC** button on each camera tile for independent recording.
  * **🔴 RECORD ALL CAMERAS** in the Master Hub to synchronize recording across all three cameras simultaneously.
  * All MP4 files are saved directly to the central host (default: `~/camera_recordings/`).
* **Zero-Touch Pi Setup:** Requires zero modifications to the Pis' SD cards.
* **Built-in Simulation / Test Mode:** Automatically falls back to synthetic video generation for immediate offline UI testing and demonstration.

---

## Prerequisites (Central Host)

On your central computer (Raspberry Pi 5, Linux PC, Mac, or WSL):

1. **Python 3 & Qt:**
   ```bash
   # Debian / Ubuntu / Raspberry Pi OS:
   sudo apt update
   sudo apt install -y python3-pyqt5 ffmpeg
   ```
   *(Alternatively, `pip install PyQt6` in a virtual environment)*

2. **Passwordless SSH to the 3 Pis:**
   To allow the host to launch `rpicam-vid` automatically without password prompts:
   ```bash
   ssh-copy-id pi@192.168.1.101
   ssh-copy-id pi@192.168.1.102
   ssh-copy-id pi@192.168.1.103
   ```

---

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/wdhsieh99-mactone/rpi-imx708-multicam-monitor.git
   cd rpi-imx708-multicam-monitor
   ```

2. **Run in Simulation / Test Mode:**
   ```bash
   chmod +x run.sh
   ./run.sh
   ```
   *The app boots with synthetic test patterns, allowing you to test grid toggles, sliders, and recording buttons.*

3. **Connect to Live Pis:**
   * Enter each Pi's IP address directly into the camera tile and click **"Set IP"** (saved automatically to `config.json`).
   * Or launch directly in live mode:
     ```bash
     ./run.sh --live
     ```

---

## Configuration (`config.json`)

You can edit `config.json` to pre-configure camera IP addresses and recording directories:

```json
{
  "recordings_dir": "~/camera_recordings",
  "cameras": [
    {"id": "cam1", "name": "Camera 1", "host": "192.168.1.101", "user": "pi"},
    {"id": "cam2", "name": "Camera 2", "host": "192.168.1.102", "user": "pi"},
    {"id": "cam3", "name": "Camera 3", "host": "192.168.1.103", "user": "pi"}
  ]
}
```

---

## License

This project is licensed under the [MIT License](LICENSE).
