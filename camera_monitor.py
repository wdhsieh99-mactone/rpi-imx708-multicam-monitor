#!/usr/bin/env python3
"""
Tri-Camera IMX708 Multi-Monitor (Zero-Install Edition)
------------------------------------------------------
Monitors 3 Raspberry Pi Camera Module 3 (Sony IMX708) feeds concurrently
with per-camera exposure, gain, manual focus (cm), and flip controls.
Features 2x2 grid layout, double-click maximization, independent recording,
and master 'Record All' functionality with lossless central remuxing.
"""

import sys
import os
import json
import signal
import subprocess
import threading
from datetime import datetime

# Automatic PyQt6 / PyQt5 Compatibility Layer
try:
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt6.QtGui import QImage, QPixmap
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout,
        QHBoxLayout, QLabel, QSlider, QCheckBox, QPushButton, QGroupBox,
        QLineEdit, QFileDialog, QFrame
    )
    QT_VERSION = 6
    Orientation_Horizontal = Qt.Orientation.Horizontal
    AlignCenter = Qt.AlignmentFlag.AlignCenter
    KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    SmoothTransformation = Qt.TransformationMode.SmoothTransformation
    Format_RGB888 = QImage.Format.Format_RGB888
    def exec_app(app): return app.exec()
except ImportError:
    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt5.QtGui import QImage, QPixmap
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout,
        QHBoxLayout, QLabel, QSlider, QCheckBox, QPushButton, QGroupBox,
        QLineEdit, QFileDialog, QFrame
    )
    QT_VERSION = 5
    Orientation_Horizontal = Qt.Horizontal
    AlignCenter = Qt.AlignCenter
    KeepAspectRatio = Qt.KeepAspectRatio
    SmoothTransformation = Qt.SmoothTransformation
    Format_RGB888 = QImage.Format_RGB888
    def exec_app(app): return app.exec_()

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    default_config = {
        "recordings_dir": "~/camera_recordings",
        "cameras": [
            {"id": "cam1", "name": "Camera 1", "host": "192.168.1.101", "user": "pi"},
            {"id": "cam2", "name": "Camera 2", "host": "192.168.1.102", "user": "pi"},
            {"id": "cam3", "name": "Camera 3", "host": "192.168.1.103", "user": "pi"},
        ]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                return data
        except Exception as e:
            print(f"Warning: Failed to parse {CONFIG_FILE}: {e}")
    return default_config

def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        print(f"Failed to save {CONFIG_FILE}: {e}")


def cm_to_dioptres(cm: float) -> float:
    """
    Converts focus distance in cm to Sony IMX708 LensPosition dioptres:
    dioptre = 1 / distance_in_meters = 100 / distance_in_cm.
    Min focus (~10cm) = 10.0, infinity (>=200cm) = 0.0.
    """
    if cm >= 200 or cm <= 0:
        return 0.0
    return round(100.0 / max(10.0, cm), 2)


class CameraStreamWorker(QThread):
    """
    Spawns rpicam-vid on the Raspberry Pi via SSH, pipes H.264 into host FFmpeg
    for zero-latency RGB24 decoding, and branches a copy to MP4 when recording.
    Also supports a synthetic test mode when host is set to 'test' or 'simulate'.
    """
    frame_ready = pyqtSignal(QImage)
    status_changed = pyqtSignal(str)

    def __init__(self, cam_id, name, host, user, recordings_dir):
        super().__init__()
        self.cam_id = cam_id
        self.name = name
        self.host = host
        self.user = user
        self.recordings_dir = os.path.expanduser(recordings_dir)
        os.makedirs(self.recordings_dir, exist_ok=True)

        self.running = True
        self.recording = False
        
        # Camera parameters
        self.focus_cm = 50.0
        self.exposure_ms = 20.0
        self.gain = 2.0
        self.hflip = False
        self.vflip = False

        self.stream_proc = None
        self.decode_proc = None
        self.record_proc = None

    def update_connection(self, host, user):
        self.host = host.strip()
        self.user = user.strip()
        self.restart_pipeline()

    def update_params(self, focus_cm, exposure_ms, gain, hflip, vflip):
        self.focus_cm = focus_cm
        self.exposure_ms = exposure_ms
        self.gain = gain
        self.hflip = hflip
        self.vflip = vflip
        self.restart_pipeline()

    def start_recording(self):
        if self.recording:
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(self.recordings_dir, f"{self.cam_id}_{timestamp}.mp4")
        
        # Lossless stream remuxing: mux raw H.264 packets straight to MP4 container
        rec_cmd = [
            "ffmpeg", "-y", "-f", "h264", "-i", "-",
            "-c", "copy", filename
        ]
        self.record_proc = subprocess.Popen(
            rec_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.recording = True

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        if self.record_proc:
            if self.record_proc.stdin:
                try:
                    self.record_proc.stdin.close()
                except Exception:
                    pass
            self.record_proc.wait()
            self.record_proc = None

    def restart_pipeline(self):
        """Terminates active pipeline so the run loop restarts it with updated settings."""
        if self.stream_proc:
            try:
                self.stream_proc.terminate()
            except Exception:
                pass

    def run(self):
        width, height = 640, 360  # Preview resolution for low latency

        while self.running:
            is_test_mode = (self.host.lower() in ["test", "simulate", "mock", "127.0.0.1", "localhost", "demo"])
            dioptre = cm_to_dioptres(self.focus_cm)
            shutter_us = int(self.exposure_ms * 1000)

            flip_args = ""
            if self.hflip: flip_args += " --hflip"
            if self.vflip: flip_args += " --vflip"

            if is_test_mode:
                self.status_changed.emit("Test Mode (Simulated)")
                brightness = max(-0.8, min(0.8, (self.exposure_ms - 20.0) / 40.0))
                vf_filters = [f"eq=brightness={brightness:.2f}:contrast={min(2.0, self.gain):.2f}"]
                if self.hflip: vf_filters.append("hflip")
                if self.vflip: vf_filters.append("vflip")
                vf_str = ",".join(vf_filters)

                stream_cmd = [
                    "ffmpeg", "-re", "-f", "lavfi",
                    "-i", f"testsrc=size=640x360:rate=30",
                    "-vf", vf_str,
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                    "-f", "h264", "-"
                ]
            else:
                self.status_changed.emit(f"Connecting to {self.host}...")
                remote_cmd = (
                    f"rpicam-vid -t 0 --inline --width 1280 --height 720 --framerate 30 "
                    f"--autofocus-mode manual --lens-position {dioptre} "
                    f"--shutter {shutter_us} --gain {self.gain:.1f}{flip_args} -o -"
                )
                stream_cmd = [
                    "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=3",
                    f"{self.user}@{self.host}",
                    remote_cmd
                ]

            decode_cmd = [
                "ffmpeg", "-fflags", "nobuffer", "-flags", "low_delay",
                "-f", "h264", "-i", "-",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-"
            ]

            try:
                self.stream_proc = subprocess.Popen(
                    stream_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
                self.decode_proc = subprocess.Popen(
                    decode_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
                
                if not is_test_mode:
                    self.status_changed.emit(f"Online ({self.host})")

                frame_size = width * height * 3

                def forward_stream():
                    while self.running and self.stream_proc and self.stream_proc.poll() is None:
                        chunk = self.stream_proc.stdout.read(4096)
                        if not chunk:
                            break
                        if self.decode_proc and self.decode_proc.stdin:
                            try:
                                self.decode_proc.stdin.write(chunk)
                                self.decode_proc.stdin.flush()
                            except BrokenPipeError:
                                break
                        if self.recording and self.record_proc and self.record_proc.stdin:
                            try:
                                self.record_proc.stdin.write(chunk)
                            except BrokenPipeError:
                                pass

                forward_thread = threading.Thread(target=forward_stream, daemon=True)
                forward_thread.start()

                while self.running and self.decode_proc and self.decode_proc.poll() is None:
                    raw_frame = self.decode_proc.stdout.read(frame_size)
                    if len(raw_frame) != frame_size:
                        break
                    img = QImage(raw_frame, width, height, width * 3, Format_RGB888)
                    self.frame_ready.emit(img.copy())

            except Exception as e:
                self.status_changed.emit(f"Error: {e}")
            finally:
                self.cleanup_procs()
                self.msleep(1500)

    def cleanup_procs(self):
        for proc in [self.decode_proc, self.stream_proc]:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    pass
        self.stop_recording()

    def stop(self):
        self.running = False
        self.cleanup_procs()
        self.wait()


class CameraWidget(QWidget):
    """Camera viewport tile with focus, exposure, gain, flip, and record controls."""
    toggle_maximize_signal = pyqtSignal(QWidget)
    host_changed_signal = pyqtSignal(str, str)  # cam_id, new_host

    def __init__(self, config, recordings_dir):
        super().__init__()
        self.config = config
        self.is_recording = False

        self.worker = CameraStreamWorker(
            config["id"], config["name"], config["host"], config["user"], recordings_dir
        )
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.status_changed.connect(self.update_status)

        self.init_ui()
        self.worker.start()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top Bar: Name, IP input, Set button, Status, Maximize
        top_bar = QHBoxLayout()
        self.title_lbl = QLabel(f"<b>{self.config['name']}</b>")
        self.title_lbl.setStyleSheet("font-size: 13px; color: #eee;")

        self.host_input = QLineEdit(self.config["host"])
        self.host_input.setPlaceholderText("IP or 'test'")
        self.host_input.setFixedWidth(110)
        self.host_input.setToolTip("Enter Pi IP address, or 'test' for simulation")

        self.connect_btn = QPushButton("Set IP")
        self.connect_btn.setFixedHeight(24)
        self.connect_btn.clicked.connect(self.apply_host)

        self.status_lbl = QLabel("Initializing...")
        self.status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        
        self.max_btn = QPushButton("Maximize")
        self.max_btn.setFixedHeight(24)
        self.max_btn.clicked.connect(lambda: self.toggle_maximize_signal.emit(self))

        top_bar.addWidget(self.title_lbl)
        top_bar.addWidget(self.host_input)
        top_bar.addWidget(self.connect_btn)
        top_bar.addWidget(self.status_lbl)
        top_bar.addStretch()
        top_bar.addWidget(self.max_btn)
        layout.addLayout(top_bar)

        # Video Preview Viewport
        self.video_lbl = QLabel("Waiting for stream...")
        self.video_lbl.setStyleSheet("background-color: #111; color: #555; border: 1px solid #333; border-radius: 4px;")
        self.video_lbl.setAlignment(AlignCenter)
        self.video_lbl.setMinimumSize(320, 180)
        self.video_lbl.mouseDoubleClickEvent = lambda e: self.toggle_maximize_signal.emit(self)
        layout.addWidget(self.video_lbl, stretch=1)

        # Per-Camera Controls Group
        ctrl_group = QGroupBox("Camera Settings")
        ctrl_group.setStyleSheet("QGroupBox { font-size: 11px; font-weight: bold; }")
        ctrl_layout = QGridLayout(ctrl_group)
        ctrl_layout.setContentsMargins(6, 4, 6, 4)
        ctrl_layout.setSpacing(4)

        # 1. Focus (cm)
        ctrl_layout.addWidget(QLabel("Focus (cm):"), 0, 0)
        self.focus_slider = QSlider(Orientation_Horizontal)
        self.focus_slider.setRange(10, 200)
        self.focus_slider.setValue(50)
        self.focus_val_lbl = QLabel("50 cm")
        self.focus_val_lbl.setFixedWidth(45)
        self.focus_slider.valueChanged.connect(lambda v: self.focus_val_lbl.setText("Inf" if v >= 200 else f"{v} cm"))
        ctrl_layout.addWidget(self.focus_slider, 0, 1)
        ctrl_layout.addWidget(self.focus_val_lbl, 0, 2)

        # 2. Exposure (ms)
        ctrl_layout.addWidget(QLabel("Exposure:"), 1, 0)
        self.exp_slider = QSlider(Orientation_Horizontal)
        self.exp_slider.setRange(1, 66)
        self.exp_slider.setValue(20)
        self.exp_val_lbl = QLabel("20 ms")
        self.exp_val_lbl.setFixedWidth(45)
        self.exp_slider.valueChanged.connect(lambda v: self.exp_val_lbl.setText(f"{v} ms"))
        ctrl_layout.addWidget(self.exp_slider, 1, 1)
        ctrl_layout.addWidget(self.exp_val_lbl, 1, 2)

        # 3. Gain
        ctrl_layout.addWidget(QLabel("Gain:"), 2, 0)
        self.gain_slider = QSlider(Orientation_Horizontal)
        self.gain_slider.setRange(10, 160)  # 1.0 to 16.0
        self.gain_slider.setValue(20)
        self.gain_val_lbl = QLabel("2.0x")
        self.gain_val_lbl.setFixedWidth(45)
        self.gain_slider.valueChanged.connect(lambda v: self.gain_val_lbl.setText(f"{v/10:.1f}x"))
        ctrl_layout.addWidget(self.gain_slider, 2, 1)
        ctrl_layout.addWidget(self.gain_val_lbl, 2, 2)

        # Direction Flips & Apply & Record Button
        bot_bar = QHBoxLayout()
        self.hflip_cb = QCheckBox("H-Flip")
        self.vflip_cb = QCheckBox("V-Flip")
        
        self.apply_btn = QPushButton("Apply Settings")
        self.apply_btn.clicked.connect(self.apply_settings)

        self.rec_btn = QPushButton("● REC")
        self.rec_btn.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; padding: 4px 10px;")
        self.rec_btn.clicked.connect(self.toggle_recording)

        bot_bar.addWidget(self.hflip_cb)
        bot_bar.addWidget(self.vflip_cb)
        bot_bar.addWidget(self.apply_btn)
        bot_bar.addStretch()
        bot_bar.addWidget(self.rec_btn)

        ctrl_layout.addLayout(bot_bar, 3, 0, 1, 3)
        layout.addWidget(ctrl_group)

    def apply_host(self):
        new_host = self.host_input.text().strip()
        self.worker.update_connection(new_host, self.config["user"])
        self.host_changed_signal.emit(self.config["id"], new_host)

    def update_frame(self, img):
        pix = QPixmap.fromImage(img)
        self.video_lbl.setPixmap(pix.scaled(
            self.video_lbl.size(),
            KeepAspectRatio,
            SmoothTransformation
        ))

    def update_status(self, status):
        self.status_lbl.setText(status)
        if "Online" in status or "Test Mode" in status:
            self.status_lbl.setStyleSheet("color: #4caf50; font-weight: bold; font-size: 11px;")
        else:
            self.status_lbl.setStyleSheet("color: #ffa000; font-size: 11px;")

    def apply_settings(self):
        focus = self.focus_slider.value()
        exp = self.exp_slider.value()
        gain = self.gain_slider.value() / 10.0
        hflip = self.hflip_cb.isChecked()
        vflip = self.vflip_cb.isChecked()
        self.worker.update_params(focus, exp, gain, hflip, vflip)

    def toggle_recording(self):
        self.set_recording(not self.is_recording)

    def set_recording(self, record: bool):
        self.is_recording = record
        if self.is_recording:
            self.worker.start_recording()
            self.rec_btn.setText("■ STOP")
            self.rec_btn.setStyleSheet("background-color: #424242; color: white; font-weight: bold; padding: 4px 10px;")
        else:
            self.worker.stop_recording()
            self.rec_btn.setText("● REC")
            self.rec_btn.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; padding: 4px 10px;")

    def closeEvent(self, event):
        self.worker.stop()
        event.accept()


class MultiCamMainWindow(QMainWindow):
    """Main window with 2x2 grid layout and global telemetry & master controls."""
    def __init__(self, start_in_test_mode=True):
        super().__init__()
        self.setWindowTitle("Tri-Camera IMX708 Central Monitor (2x2 Grid View)")
        self.resize(1280, 800)
        self.maximized_widget = None

        self.config_data = load_config()
        self.recordings_dir = self.config_data.get("recordings_dir", "~/camera_recordings")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.grid_layout = QGridLayout(central_widget)
        self.grid_layout.setContentsMargins(8, 8, 8, 8)
        self.grid_layout.setSpacing(8)

        self.cam_widgets = []
        positions = [(0, 0), (0, 1), (1, 0)]

        # Spawn 3 Camera Tiles
        for idx, cfg in enumerate(self.config_data.get("cameras", [])):
            cfg_copy = dict(cfg)
            if start_in_test_mode:
                cfg_copy["host"] = "test"
            w = CameraWidget(cfg_copy, self.recordings_dir)
            w.toggle_maximize_signal.connect(self.toggle_maximize)
            w.host_changed_signal.connect(self.on_host_changed)
            self.cam_widgets.append(w)
            row, col = positions[idx]
            self.grid_layout.addWidget(w, row, col)

        # 4th Slot: Master Control & Hub Panel
        self.master_panel = self.create_master_panel()
        self.grid_layout.addWidget(self.master_panel, 1, 1)

    def on_host_changed(self, cam_id, new_host):
        for c in self.config_data.get("cameras", []):
            if c["id"] == cam_id:
                c["host"] = new_host
        save_config(self.config_data)

    def create_master_panel(self):
        panel = QGroupBox("Master Control & Hub")
        panel.setStyleSheet("QGroupBox { font-size: 13px; font-weight: bold; }")
        vbox = QVBoxLayout(panel)
        vbox.setSpacing(10)

        info = QLabel(
            "<b>Architecture & Status:</b><br>"
            "• Mode: Zero-install on Pis (pure stock <code>rpicam-vid</code> over SSH)<br>"
            "• IMX708 Focus: Dioptre conversion (10 cm to 200+ cm)<br>"
            "• Recording: Lossless stream remux (0% CPU re-encode)<br>"
            f"• Storage: <code>{self.recordings_dir}</code><br><br>"
            "<b>Quick Tip:</b><br>"
            "• Cameras start in <b>Simulation Test Mode</b>.<br>"
            "• Type your Pi's real IP into the box and click <b>'Set IP'</b> to connect live."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #ccc; font-size: 12px;")
        vbox.addWidget(info)

        vbox.addStretch()

        mode_btn_bar = QHBoxLayout()
        test_all_btn = QPushButton("Switch All to Simulation")
        test_all_btn.clicked.connect(self.switch_all_to_test)
        mode_btn_bar.addWidget(test_all_btn)
        vbox.addLayout(mode_btn_bar)

        # Global RECORD ALL / STOP ALL Buttons
        self.rec_all_btn = QPushButton("🔴 RECORD ALL CAMERAS")
        self.rec_all_btn.setFixedHeight(50)
        self.rec_all_btn.setStyleSheet(
            "background-color: #b71c1c; color: white; font-size: 14px; font-weight: bold; border-radius: 6px;"
        )
        self.rec_all_btn.clicked.connect(self.record_all)
        vbox.addWidget(self.rec_all_btn)

        self.stop_all_btn = QPushButton("⏹ STOP ALL RECORDINGS")
        self.stop_all_btn.setFixedHeight(38)
        self.stop_all_btn.setStyleSheet(
            "background-color: #37474f; color: white; font-size: 13px; font-weight: bold; border-radius: 6px;"
        )
        self.stop_all_btn.clicked.connect(self.stop_all)
        vbox.addWidget(self.stop_all_btn)

        vbox.addStretch()
        hint = QLabel("<i>Tip: Double-click any camera tile to toggle Fullscreen / Restore Grid.</i>")
        hint.setStyleSheet("color: #777; font-size: 11px;")
        vbox.addWidget(hint)

        return panel

    def switch_all_to_test(self):
        for w in self.cam_widgets:
            w.host_input.setText("test")
            w.apply_host()

    def toggle_maximize(self, target_widget):
        """Toggles between the 2x2 grid and a single maximized camera."""
        if self.maximized_widget is None:
            for w in self.cam_widgets + [self.master_panel]:
                if w != target_widget:
                    w.hide()
            self.maximized_widget = target_widget
            target_widget.max_btn.setText("Restore Grid")
        else:
            for w in self.cam_widgets + [self.master_panel]:
                w.show()
            self.maximized_widget.max_btn.setText("Maximize")
            self.maximized_widget = None

    def record_all(self):
        for w in self.cam_widgets:
            w.set_recording(True)

    def stop_all(self):
        for w in self.cam_widgets:
            w.set_recording(False)

    def closeEvent(self, event):
        for w in self.cam_widgets:
            w.worker.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    start_test = True
    if "--live" in sys.argv:
        start_test = False

    window = MultiCamMainWindow(start_in_test_mode=start_test)
    window.show()
    sys.exit(exec_app(app))
