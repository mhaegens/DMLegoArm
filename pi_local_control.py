#!/usr/bin/env python3
"""Simple on-device control panel for the LEGO arm.

Runs a local Tkinter UI on the Raspberry Pi screen with:
- Status indicators (internet, ngrok tunnel, Pi Connect)
- Manual nudge controls (rotations)
- Production process triggers
- Calibration point capture buttons
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
STATUS_INTERVAL_MS = 3000
REQUEST_TIMEOUT_S = 2.5

JOINTS = ["A", "B", "C", "D"]
CALIB_POINTS = {
    "A": [("open", "Open"), ("closed", "Closed")],
    "B": [("min", "Min"), ("pick", "Pick"), ("max", "Max")],
    "C": [("min", "Min"), ("pick", "Pick"), ("max", "Max")],
    "D": [("assembly", "Assembly"), ("quality", "Quality"), ("neutral", "Neutral")],
}


def _json_request(method: str, url: str, body: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
    req = Request(url, data=payload, method=method)
    req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    with urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _check_internet() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=1.0):
            return True
    except OSError:
        return False


def _check_ngrok() -> bool:
    try:
        data = _json_request("GET", "http://127.0.0.1:4040/api/tunnels")
        tunnels = data.get("tunnels") or []
        return bool(tunnels)
    except Exception:
        return False


def _check_service_active(name: str, user: bool = False) -> bool:
    command = ["systemctl"]
    if user:
        command.append("--user")
    command.extend(["is-active", name])
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "active"


def _check_pi_connect() -> bool:
    services = ("rpi-connect", "rpi-connect-lite", "raspberrypi-connect")
    for service in services:
        if _check_service_active(service) or _check_service_active(service, user=True):
            return True
    try:
        result = subprocess.run(
            ["pgrep", "-f", "rpi-connect|raspberrypi-connect"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return True
    except Exception:
        return False
    return False


def _check_api(base_url: str, headers: dict) -> bool:
    if not base_url:
        return False
    try:
        _json_request("GET", f"{base_url}/v1/health", headers=headers)
        return True
    except Exception:
        return False


class StatusIndicator(ttk.Frame):
    def __init__(self, master: tk.Misc, label: str) -> None:
        super().__init__(master)
        self._canvas = tk.Canvas(self, width=14, height=14, highlightthickness=0)
        self._circle = self._canvas.create_oval(2, 2, 12, 12, fill="gray")
        self._canvas.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(self, text=label).pack(side=tk.LEFT)

    def set_state(self, ok: bool) -> None:
        color = "#16a34a" if ok else "#dc2626"
        self._canvas.itemconfig(self._circle, fill=color)


class PiControlApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LEGO Arm - Local Control")
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.compact_layout = screen_w <= 1024 or screen_h <= 600
        width = screen_w if self.compact_layout else min(1024, screen_w)
        height = screen_h if self.compact_layout else min(600, screen_h)
        self.geometry(f"{width}x{height}")
        self.resizable(False, False)

        self.base_url_var = tk.StringVar(value=DEFAULT_BASE_URL)
        self.api_key_var = tk.StringVar(value="")
        self.nudge_amount_var = tk.StringVar(value="1")
        self.nudge_speed_var = tk.StringVar(value="40")
        self.status_text_var = tk.StringVar(value="Ready")
        self.processes: List[str] = []

        self._build_ui()
        self._schedule_status_check()
        self._refresh_processes()
        self._bind_settings_traces()

    def _headers(self) -> dict:
        headers = {}
        api_key = self.api_key_var.get().strip()
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        self.status_text_var.set(message)

    def _build_ui(self) -> None:
        padding = 8 if self.compact_layout else 12
        container = ttk.Frame(self, padding=padding)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X)

        if self.compact_layout:
            header.grid_columnconfigure(1, weight=1)
            ttk.Label(header, text="Base URL:").grid(row=0, column=0, sticky="w")
            ttk.Entry(header, textvariable=self.base_url_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
            ttk.Label(header, text="API Key:").grid(row=2, column=0, sticky="w")
            ttk.Entry(header, textvariable=self.api_key_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 6))
            ttk.Button(header, text="Refresh", command=self._refresh).grid(row=0, column=1, sticky="e")
        else:
            ttk.Label(header, text="Base URL:").pack(side=tk.LEFT)
            ttk.Entry(header, textvariable=self.base_url_var, width=36).pack(side=tk.LEFT, padx=(6, 12))
            ttk.Label(header, text="API Key:").pack(side=tk.LEFT)
            ttk.Entry(header, textvariable=self.api_key_var, width=28).pack(side=tk.LEFT, padx=(6, 12))
            ttk.Button(header, text="Refresh", command=self._refresh).pack(side=tk.LEFT)

        status_frame = ttk.LabelFrame(container, text="Status", padding=10)
        status_frame.pack(fill=tk.X, pady=(12, 8))

        self.ind_internet = StatusIndicator(status_frame, "Internet access")
        self.ind_internet.pack(side=tk.LEFT, padx=(0, 16))
        self.ind_ngrok = StatusIndicator(status_frame, "Ngrok tunnel")
        self.ind_ngrok.pack(side=tk.LEFT, padx=(0, 16))
        self.ind_connect = StatusIndicator(status_frame, "Pi Connect running")
        self.ind_connect.pack(side=tk.LEFT, padx=(0, 16))
        self.ind_api = StatusIndicator(status_frame, "Arm API reachable")
        self.ind_api.pack(side=tk.LEFT, padx=(0, 16))

        body = ttk.Frame(container)
        body.pack(fill=tk.BOTH, expand=True)

        if self.compact_layout:
            notebook = ttk.Notebook(body)
            notebook.pack(fill=tk.BOTH, expand=True)
            controls_tab = ttk.Frame(notebook, padding=6)
            calib_tab = ttk.Frame(notebook, padding=6)
            log_tab = ttk.Frame(notebook, padding=6)
            notebook.add(controls_tab, text="Controls")
            notebook.add(calib_tab, text="Calibration")
            notebook.add(log_tab, text="Log")
            left = controls_tab
            right = calib_tab
        else:
            left = ttk.Frame(body)
            left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 12))
            right = ttk.Frame(body)
            right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            log_tab = right

        nudge_frame = ttk.LabelFrame(left, text="Nudge controls (rotations)", padding=10)
        nudge_frame.pack(fill=tk.X)

        row = ttk.Frame(nudge_frame)
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="Rotations:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.nudge_amount_var, width=8).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(row, text="Speed:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.nudge_speed_var, width=8).pack(side=tk.LEFT, padx=(6, 12))

        for joint in JOINTS:
            row = ttk.Frame(nudge_frame)
            row.pack(fill=tk.X, pady=4)
            ttk.Label(row, text=f"Joint {joint}", width=10).pack(side=tk.LEFT)
            ttk.Button(row, text="-", width=6, command=lambda j=joint: self._nudge(j, -1)).pack(side=tk.LEFT, padx=(6, 4))
            ttk.Button(row, text="+", width=6, command=lambda j=joint: self._nudge(j, 1)).pack(side=tk.LEFT)

        proc_frame = ttk.LabelFrame(left, text="Production processes", padding=10)
        proc_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.process_container = ttk.Frame(proc_frame)
        self.process_container.pack(fill=tk.BOTH, expand=True)

        calib_frame = ttk.LabelFrame(right, text="Calibration points", padding=10)
        calib_frame.pack(fill=tk.BOTH, expand=True)

        for joint, points in CALIB_POINTS.items():
            joint_frame = ttk.LabelFrame(calib_frame, text=f"Joint {joint}")
            joint_frame.pack(fill=tk.X, pady=6)
            for name, label in points:
                ttk.Button(
                    joint_frame,
                    text=label,
                    command=lambda j=joint, n=name: self._set_calibration_point(j, n),
                ).pack(side=tk.LEFT, padx=4, pady=4)

        calib_actions = ttk.Frame(calib_frame)
        calib_actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(calib_actions, text="Reset calibration", command=self._reset_calibration).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(calib_actions, text="Finalize calibration", command=self._finalize_calibration).pack(side=tk.LEFT)

        log_frame_parent = log_tab if self.compact_layout else right
        log_frame = ttk.LabelFrame(log_frame_parent, text="Log", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.log_text = tk.Text(log_frame, height=12, state="disabled")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        status_bar = ttk.Label(container, textvariable=self.status_text_var, anchor="w")
        status_bar.pack(fill=tk.X, pady=(8, 0))

    def _nudge(self, joint: str, direction: int) -> None:
        try:
            rotations = float(self.nudge_amount_var.get())
        except ValueError:
            self._log("Invalid rotations value")
            return
        try:
            speed = int(float(self.nudge_speed_var.get()))
        except ValueError:
            self._log("Invalid speed value")
            return
        delta = rotations * direction
        payload = {
            "mode": "relative",
            "units": "rotations",
            "joints": {joint: delta},
            "speed": speed,
            "async_exec": False,
        }
        self._send_command(f"Nudge {joint} {delta} rotations", "/v1/arm/move", payload)

    def _set_calibration_point(self, joint: str, name: str) -> None:
        payload = {"joint": joint, "name": name}
        self._send_command(f"Set calibration {joint}:{name}", "/v1/arm/calibration", payload)

    def _reset_calibration(self) -> None:
        self._send_command("Reset calibration", "/v1/arm/calibration", {"reset": True})

    def _finalize_calibration(self) -> None:
        self._send_command("Finalize calibration", "/v1/arm/calibration", {"finalize": True})

    def _send_command(self, label: str, path: str, payload: dict) -> None:
        base_url = self.base_url_var.get().strip().rstrip("/")
        headers = self._headers()
        try:
            res = _json_request("POST", f"{base_url}{path}", payload, headers=headers)
            if not res.get("ok", True):
                err = res.get("error", {}).get("message") or res
                self._log(f"{label} failed: {err}")
            else:
                self._log(f"{label} ✓")
        except URLError as exc:
            self._log(f"{label} failed: {exc}")
        except Exception as exc:
            self._log(f"{label} error: {exc}")

    def _refresh_processes(self) -> None:
        base_url = self.base_url_var.get().strip().rstrip("/")
        headers = self._headers()

        def task() -> None:
            try:
                res = _json_request("GET", f"{base_url}/v1/inventory", headers=headers)
                data = res.get("data", res)
                processes = data.get("processes", []) if isinstance(data, dict) else []
                self.processes = list(processes)
                self.after(0, self._render_processes)
                self._log("Processes refreshed")
            except Exception as exc:
                self._log(f"Process refresh failed: {exc}")

        threading.Thread(target=task, daemon=True).start()

    def _refresh(self) -> None:
        self._update_status()
        self._refresh_processes()

    def _bind_settings_traces(self) -> None:
        def schedule_refresh(*_: object) -> None:
            self.after(200, self._update_status)

        self.base_url_var.trace_add("write", schedule_refresh)
        self.api_key_var.trace_add("write", schedule_refresh)

    def _render_processes(self) -> None:
        for child in self.process_container.winfo_children():
            child.destroy()
        if not self.processes:
            ttk.Label(self.process_container, text="No processes available").pack(anchor="w")
            return
        for name in self.processes:
            row = ttk.Frame(self.process_container)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=name).pack(side=tk.LEFT)
            ttk.Button(row, text="Run", command=lambda n=name: self._run_process(n)).pack(side=tk.RIGHT)

    def _run_process(self, name: str) -> None:
        self._send_command(f"Run process {name}", f"/v1/processes/{name}", {})

    def _schedule_status_check(self) -> None:
        self._update_status()
        self.after(STATUS_INTERVAL_MS, self._schedule_status_check)

    def _update_status(self) -> None:
        base_url = self.base_url_var.get().strip().rstrip("/")
        headers = self._headers()

        def task() -> Tuple[bool, bool, bool, bool]:
            return (
                _check_internet(),
                _check_ngrok(),
                _check_pi_connect(),
                _check_api(base_url, headers),
            )

        def apply(result: Tuple[bool, bool, bool, bool]) -> None:
            internet, ngrok, connect, api_ok = result
            self.ind_internet.set_state(internet)
            self.ind_ngrok.set_state(ngrok)
            self.ind_connect.set_state(connect)
            self.ind_api.set_state(api_ok)

        def runner() -> None:
            res = task()
            self.after(0, lambda: apply(res))

        threading.Thread(target=runner, daemon=True).start()


if __name__ == "__main__":
    app = PiControlApp()
    app.mainloop()
