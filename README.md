# DM LEGO Arm — REST API for Raspberry Pi + SAP Digital Manufacturing

A small, **zero-dependency** HTTP service that drives a LEGO® robotic arm (via Raspberry Pi + Build HAT) and exposes a **stable, versioned REST API** designed for **SAP Digital Manufacturing (DM)** integration through ngrok.

This README documents the **final architecture** I shipped, why I chose it, how to run it, and where its limits are.

---

## At a glance

* **Single file server**: `lego_arm_master.py` (Python standard library only; no pip installs)
* **Endpoints**: `/v1/*` (health, state, move, pose, pick/place, stop, coast, async ops, production processes)
* **Auth**: API key in header `x-api-key`
* **Idempotency**: `X-Idempotency-Key` (in-memory cache; 5-minute TTL)
* **Async**: Background worker + `GET /v1/operations/{id}` (default for moves/poses/pickplace)
* **DM-ready**: Works with Service Registry + POD buttons via ngrok
* **Hardware toggle**: `USE_FAKE_MOTORS=1` to simulate without the Build HAT
* **Calibration**: Named points + rotation tuning persisted in `arm_calibration.json`

---

## Why this architecture (and what I didn’t do)

I intentionally **removed external web frameworks** (Flask/FastAPI) to avoid package management on constrained or locked-down devices. The server uses `http.server` + `threading`. That makes deployment trivial (copy one file, set env vars), and the API contract stays the same.

**Trade-offs (be critical):**

* Single-process, simple threading. **Not for high concurrency** or untrusted networks.
* **No TLS** on the Pi (terminate TLS at ngrok). Don’t expose the Pi port directly to the Internet.
* **In-memory** idempotency cache and operation store (clears on restart).
* Motions are **sequential per joint**; no coordinated multi-axis blend or kinematics.
* **Minimal safety**: software limits + stop endpoint. You still need hardware interlocks/E-stop.

If you need stronger guarantees (audit log, parallel motion, OpenAPI/Swagger, auth tokens, durable queues), the API shape is ready for it—swap the transport later.

---

## Hardware

* Raspberry Pi with **Build HAT** connected and powered
* LEGO motors connected to ports **A/B/C/D** (A = gripper, B = wrist, C = elbow, D = rotation)
* Optional: run **without** hardware via `USE_FAKE_MOTORS=1`

> Build HAT tip: on Raspberry Pi OS (Bookworm/bullseye), enable UART so `/dev/serial0` exists and allow your user access:

```bash
sudo raspi-config   # Interface Options → Serial Port → login shell: No, hardware: Yes
# or:
echo "enable_uart=1" | sudo tee -a /boot/firmware/config.txt
sudo usermod -aG dialout,gpio,i2c $USER
sudo reboot
```

### Motor self-test

Run a quick local check to verify that all four motors respond:

```bash
USE_FAKE_MOTORS=1 python3 motor_selftest.py  # remove USE_FAKE_MOTORS for real hardware
```

### Bluetooth gamepad control (optional)

You can steer the arm manually with a standard Bluetooth gamepad. Install the
`evdev` Python package once and start the server with `ENABLE_GAMEPAD=1`:

```bash
pip install evdev
ENABLE_GAMEPAD=1 python3 lego_arm_master.py  # GAMEPAD_DEVICE=/dev/input/eventX to select
```

Left stick drives rotation (D) and elbow (C); right stick drives wrist (B) and
gripper (A). The feature quietly disables itself if no controller is found.

---

## Software layout

```
repo/
├─ lego_arm_master.py        # REST server + arm controller
├─ processes/                # on-device production process modules
├─ web/
│  └─ index.html             # built-in on-device control UI
└─ systemd/
   └─ legoarm.service        # optional unit file (example below)
```

### Production processes

Reusable arm workflows live in [`processes/`](processes). Each module exposes a
`run(arm)` function and is registered in `processes/__init__.py`. Registered
names automatically become REST endpoints at `POST /v1/processes/<name>`.

The production processes shipped with this repo are:

| Name                               | URL Path                                              |
| ---------------------------------- | ----------------------------------------------------- |
| LEGO_ARM_PICK_ASSEMBLY_QUALITY     | `https://<host>/v1/processes/pick-assembly-quality`   |
| LEGO_ARM_PICK_QUALITY_ASSEMBLY     | `https://<host>/v1/processes/pick-quality-assembly`   |
| LEGO_ARM_SHUTDOWN                  | `https://<host>/v1/processes/shutdown`                |
| LEGO_ARM_TEST                      | `https://<host>/v1/processes/test`                    |

See [`processes/README.md`](processes/README.md) for details on creating new
workflows.

> Note: `POST /v1/processes/*`, `POST /v1/arm/pose`, and `POST /v1/arm/pickplace` require calibration to be finalized first; otherwise the API returns `NOT_CALIBRATED`.

**Key components inside `lego_arm_master.py`:**

* `ArmController` – clamps & executes moves, named poses, pick/place helper.
* HTTP handler – routes `/v1/*`, parses JSON, returns `{ ok, data }` or `{ ok:false, error{code,message} }`.
* Operation worker – runs async jobs; `GET /v1/operations/{id}` to poll.
* Auth & idempotency – `x-api-key` and `X-Idempotency-Key`.

---

## Configuration

Set through environment variables (systemd or shell):

| Variable                     | Required | Default      | What it does                                                                                 |
| ---------------------------- | -------- | ------------ | -------------------------------------------------------------------------------------------- |
| `API_KEY`                    | **Yes**  | `change-me`  | Shared secret for all control endpoints (`x-api-key` header). Set a real value in production.|
| `PORT`                       | No       | `8000`       | Local listen port.                                                                           |
| `HOST`                       | No       | `0.0.0.0`    | Bind address (supports IPv4/IPv6).                                                           |
| `USE_FAKE_MOTORS`            | No       | `0`          | `1` to simulate motors (dev/demo without Build HAT).                                         |
| `ALLOW_NO_AUTH_LOCAL`        | No       | `0`          | `1` to skip API key for localhost clients only (dev convenience).                            |
| `ENABLE_GAMEPAD`             | No       | `0`          | `1` to enable Bluetooth gamepad control (requires `evdev`).                                  |
| `GAMEPAD_DEVICE`             | No       | (auto)       | Override the input device path when multiple controllers are present.                        |
| `MOTOR_WATCHDOG_INTERVAL_S`  | No       | `2`          | Health polling interval for real motors (seconds).                                           |
| `MOTOR_WATCHDOG_GRACE_S`     | No       | `6`          | Time before watchdog restarts the process when motor health is failing (seconds).            |

Logs are written to `lego_arm_master.log` beside the script.

### Rotation calibration UI

Open the control page's **Admin** drawer to tune how many motor degrees produce one full joint rotation. Adjust the per-motor values and press **Save** to persist them via `POST /v1/arm/rotation`.

### Named-point calibration UI

Open the control page's **Calibration** drawer to capture named points for each motor (open/closed, min/pick/max, assembly/neutral/quality). Once all required points are recorded, press **Finalize** to derive limits and move to the computed home pose.

---

## Run it (quick start)

### A) Local, no systemd

**Fake motors (no hardware):**

```bash
API_KEY=$(python3 - <<'PY';import secrets;print(secrets.token_urlsafe(24));PY)
USE_FAKE_MOTORS=1 PORT=8000 \
python3 lego_arm_master.py
```

Test:

```bash
curl http://127.0.0.1:8000/v1/health
```

**Real motors:**

* Ensure `/dev/serial0` exists (see tip above), then:

```bash
API_KEY=<your-key> PORT=8000 USE_FAKE_MOTORS=0 \
python3 lego_arm_master.py
```

### B) As a systemd service (recommended)

Create `/etc/systemd/system/legoarm.service`:

```ini
[Unit]
Description=LEGO Arm HTTP API (stdlib)
After=network-online.target
Wants=network-online.target

[Service]
User=michiel
Group=michiel
WorkingDirectory=/home/michiel/lego-arm
ExecStart=/usr/bin/python3 /home/michiel/lego-arm/lego_arm_master.py
Environment=PORT=8000
Environment=API_KEY=<GENERATE-STRONG-KEY>
Environment=USE_FAKE_MOTORS=1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable & check:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now legoarm
systemctl status legoarm --no-pager
curl http://127.0.0.1:8000/v1/health
```

### C) Public URL via ngrok

```bash
ngrok http 8000
# copy the HTTPS URL it prints, e.g.:
# https://<YOUR_NGROK_URL>  -> http://localhost:8000
```

Use **the HTTPS URL without a port** in browsers/DM:

```
https://<YOUR_NGROK_URL>/v1/health
```

---

## API (v1)

All responses are JSON and share this envelope:

```json
// success
{ "ok": true,  "data": { ... } }

// error
{ "ok": false, "error": { "code": "ERROR_CODE", "message": "human readable" } }
```

**Auth**: Send `x-api-key: <your key>` on every endpoint **except** `/v1/health`.
**Idempotency** (optional): Send `X-Idempotency-Key: <uuid>` to deduplicate retries.

### `GET /v1/health`

Liveness probe.

```bash
curl https://<ngrok>/v1/health
```

### `GET /v1/inventory`  *(auth)*

Lists available endpoints, poses, motors.

### `GET /v1/arm/state`  *(auth)*

Returns current absolute degrees, software limits, motor list, rotation
calibration, calibration status, and any named points captured during calibration.

```json
{
  "ok": true,
  "data": {
    "abs_degrees": {"A": 0, "B": 0, "C": 0, "D": 0},
    "limits": {"A": [-360,360], "B": [-180,180], "C": [-180,180], "D": [-90,90]},
    "motors": ["A","B","C","D"],
    "rotation": {"A":360,"B":360,"C":360,"D":360},
    "calibrated": true,
    "points": {"A": {"closed": 0, "open": 90}}
  }
}
```

### `GET /v1/arm/rotation`  *(auth)*

Returns the degrees required for one full joint rotation for each motor.

```json
{ "rotation": { "A": 360, "B": 360, "C": 360, "D": 360 } }
```

### `POST /v1/arm/rotation`  *(auth)*

Update per-motor rotation calibration; values are saved to `arm_calibration.json` and applied when specifying rotations. The payload can either be the raw map or wrapped under a `rotation` key.

```json
{ "rotation": { "A": 400 } }
```

### `GET /v1/arm/calibration`  *(auth)*

Returns calibration progress and whether the controller is ready.

```json
{ "points": {"A": {"open": 0}}, "calibrated": false }
```

### `POST /v1/arm/calibration`  *(auth)*

Record named points, reset calibration, or finalize. To store the current joint angle for a named point, send `{ "joint": "A", "name": "open" }`. To clear all points, send `{ "reset": true }`. After all required points are collected, finalize with `{ "finalize": true }` to derive joint limits and automatically move to the computed home pose.

```bash
curl -X POST https://<ngrok>/v1/arm/calibration \\
  -H "content-type: application/json" -H "x-api-key: $KEY" \\
  -d '{"joint":"A","name":"open"}'
```

### Calibrated point names

Finalization requires the following named points per joint:

| Motor | Names |
|-------|-------|
| A (gripper) | `open`, `closed` |
| B (wrist)   | `min`, `pick`, `max` |
| C (elbow)   | `min`, `pick`, `max` |
| D (base rotation) | `assembly`, `neutral`, `quality` |

You can also store additional names (e.g. `raised`) and reference them anywhere a joint angle is expected, e.g. `{ "A": "open" }`.

### `POST /v1/arm/pose`  *(auth)*

Go to a named pose. Built-in names: `home`, `pick_left`, `pick_right`, `place_left`, `place_right`.
By default the call is queued (`async_exec: true`) and returns an operation id; set `async_exec: false` for a synchronous response.

```bash
curl -X POST https://<ngrok>/v1/arm/pose \
  -H "content-type: application/json" -H "x-api-key: $KEY" \
  -d '{"name":"home","speed":60}'
```

### `POST /v1/arm/move`  *(auth)*

Relative or absolute joint moves. Always specify the intended `"units"` so the backend can preserve physical meaning end-to-end. `"degrees"` is the default for backwards compatibility (a warning is logged if omitted) while `"rotations"` expresses full motor turns. Joint targets may also be provided as strings referencing calibrated point names, e.g. `{ "A": "closed - 10" }`.

```json
{
  "mode": "relative",                // or "absolute"
  "joints": { "A": 10, "B": -5 },    // at least one joint
  "units": "degrees",               // or "rotations"
  "speed": 60,                       // 1..100
  "timeout_s": 30,                   // optional generous guard
  "finalize": true,                  // corrective nudge based on encoder error
  "finalize_deadband_deg": 2.0,      // skip finalize if within tolerance
  "async_exec": true                 // default true -> returns operation_id
}
```

When `async_exec` is `true` (default) the response returns an `operation_id`; poll [`GET /v1/operations/{id}`](#get-v1operationsid--auth) for results. When `async_exec` is `false` the response echoes the final encoder delta, applied unit conversions, and whether a timeout occurred. Callers that need to inspect the most recent move can also poll [`GET /v1/arm/last_move`](#get-v1armlast_move--auth).

### `POST /v1/arm/pickplace`  *(auth)*

Simple helper sequence.
By default the call is queued (`async_exec: true`) and returns an operation id; set `async_exec: false` for a synchronous response.

```json
{ "location": "left|center|right", "action": "pick|place", "speed": 50, "async_exec": false }
```

### `POST /v1/arm/stop`  *(auth)*

Stops all motors immediately.

```json
{ "reason": "operator stop" }
```

### `POST /v1/arm/coast`  *(auth)*

Enable or disable coast mode on one or more motors for manual manipulation.

```json
{ "motors": ["A","B"], "enable": true }
```

Omit `motors` to affect all. Set `"enable": false` to restore braking.

### `POST /v1/arm/recover`  *(auth)*

Attempts to stop motion, read current encoder positions, and move back to a safe home pose (uses calibrated points when available).

```json
{ "speed": 30, "timeout_s": 90 }
```

### `GET /v1/arm/last_move`  *(auth)*

Returns telemetry for the most recent completed move command.

```json
{
  "units": "rotations",
  "commanded": {"A": 3},
  "converted_degrees": {"A": 1080},
  "new_abs": {"A": 1080},
  "speed": 60,
  "timeout_s": 30,
  "elapsed_s": 2.4,
  "final_error_deg": {"A": 1.1},
  "finalize_deadband_deg": 2,
  "finalize_corrections": {"A": 1.1},
  "finalized": true,
  "timeout": false
}
```

### `GET /v1/operations/{id}`  *(auth)*

Poll an async operation.

```json
{
  "ok": true,
  "data": {
    "id": "uuid",
    "type": "move|pose|pickplace",
    "status": "queued|running|succeeded|failed|canceled",
    "request": { ... },
    "result": { ... },               // if succeeded
    "error": {"code":"...","message":"..."}  // if failed
  }
}
```

---

## SAP Digital Manufacturing (DM) integration

### 1) Service Registry

* **Type**: HTTP, **Protocol**: REST
* **Base URL**: your ngrok HTTPS (no port)
* **Parameters (default headers)**:

  * `x-api-key` (Header, Required, Default = your key)
  * `ngrok-skip-browser-warning` (Header, Default `1`)
  * `content-type` (Header, Default `application/json`)

Use the built-in **Test** on `GET /v1/health` to validate connectivity.

### 2) POD (buttons with API Runner or your Lego Arm plugin)

Create buttons that call the service:

* **Health** – GET `/v1/health` (no body)
* **Home** – POST `/v1/arm/pose` body `{"name":"home","speed":60}`
* **Nudge** – POST `/v1/arm/move` body `{"mode":"relative","units":"rotations","joints":{"A":10},"speed":40}`
* **Pick Left** – POST `/v1/arm/pickplace` body `{"location":"left","action":"pick","speed":50}`
* **Stop** – POST `/v1/arm/stop` body `{"reason":"operator stop"}`
* **Coast** – POST `/v1/arm/coast` body `{"enable":true}`

Because headers are set in the Service Registry, you only supply **method/path/body** per action.

---

## On-device web UI

With the service running on the Raspberry Pi you can visit `http://<pi>:8000/` or your ngrok URL to access a small control panel.
It shows current joint positions and provides buttons for common poses, custom moves, and an emergency stop. Use the **Calibration**
drawer to capture named points, and the **Admin** drawer to set base URL/API key, enable tilt-to-nudge, and update rotation calibration.

---

## Safety, calibration & assumptions

* On startup the controller assumes the **current physical pose is absolute zero (0°)** for A/B/C/D. If that’s not true, first move to a safe “home” physically, then start the service.
* Software **limits** are unset until calibration is finalized. Finalization derives min/max limits from the named points you captured.
* Motions are executed **sequentially per joint**, blocking, at a simple “speed” scale (1–100).
* Add a **hardware E-stop** and current/limit protections. The `stop` endpoint is not a safety system.

---

## Troubleshooting

Server logs are written to `lego_arm_master.log` next to the script. Consult this file for detailed information when diagnosing issues.

**Health works locally but not via ngrok**

* You used `https://<ngrok>:8000/...` → **remove the `:8000`**.
* Or ngrok points to the wrong port. Start it with `ngrok http 8000`.

**401 Unauthorized**

* Missing/wrong `x-api-key`. Set default header in Service Registry.

**400 NOT_CALIBRATED**

* Run the calibration flow (`POST /v1/arm/calibration` with named points, then finalize) before using poses, pick/place, or processes.

**502 from ngrok**

* API not listening on that port. On the Pi:
  `sudo ss -ltnp | grep ':8000'` should show `python3`.

**`/dev/serial0` not found**

* Enable UART (see Hardware tip), or set `USE_FAKE_MOTORS=1` for demos.

**Service restarts / CHDIR errors**

* `WorkingDirectory` or `ExecStart` path in systemd is wrong. Fix paths, `daemon-reload`, restart.

---

## Design choices & future work

**Choices**

* **Stdlib HTTP**: no pip, fewer moving parts, easy to audit.
* **Header API key**: simple to pass from DM; adequate behind ngrok for demos.
* **In-memory ops & idempotency**: fast and simple for single-node usage.

**Known limitations / next steps**

* Concurrency: replace stdlib server with uvicorn/Flask when you need more throughput.
* **Swagger/OpenAPI**: can be added easily if you switch to FastAPI; DM doesn’t require it.
* **Zeroing endpoint**: add `/v1/arm/zero` to set current pose as absolute 0 without restarting.
* **Better motion control**: joint synchronization, jerk/accel limits, trajectory planner.
* **Persistent audit log**: append JSON lines to file or syslog for traceability.
* **Auth hardening**: rotate keys, per-client keys, or mTLS if you drop ngrok.

---

## License & contributions

* **License**: MIT (recommended; confirm for your repo).
* **Contributions**: PRs welcome. Please include:

  * A short description and rationale,
  * Tests or a reproducible snippet (e.g., `curl`),
  * Updated README if you change behavior.

---

## Quick reference (copy-paste)

```bash
# Start service quickly (fake motors)
API_KEY=<YOUR_KEY> USE_FAKE_MOTORS=1 PORT=8000 python3 lego_arm_master.py

# Health
curl https://<ngrok>/v1/health

# Inventory (auth)
curl https://<ngrok>/v1/inventory -H "x-api-key: <YOUR_KEY>" -H "ngrok-skip-browser-warning: 1"

# Go home
curl -X POST https://<ngrok>/v1/arm/pose \
  -H "content-type: application/json" -H "x-api-key: <YOUR_KEY>" -H "ngrok-skip-browser-warning: 1" \
  -d '{"name":"home","speed":60}'

# Run built-in process
curl -X POST https://<ngrok>/v1/processes/pick-assembly-quality \
  -H "x-api-key: <YOUR_KEY>" -H "ngrok-skip-browser-warning: 1"
```

---
