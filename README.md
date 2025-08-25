# DM LEGO Arm — REST API for Raspberry Pi + SAP Digital Manufacturing

A small, **zero-dependency** HTTP service that drives a LEGO® robotic arm (via Raspberry Pi + Build HAT) and exposes a **stable, versioned REST API** designed for **SAP Digital Manufacturing (DM)** integration through ngrok.

This README documents the **final architecture** we shipped, why we chose it, how to run it, and where its limits are.

---

## At a glance

* **Single file server**: `lego_arm_master.py` (Python standard library only; no pip installs)
* **Endpoints**: `/v1/*` (health, state, move, pose, pick/place, stop, async ops, production processes)
* **Auth**: API key in header `x-api-key`
* **Idempotency**: `X-Idempotency-Key` (in-memory cache; 5-minute TTL)
* **Async**: Background worker + `GET /v1/operations/{id}`
* **DM-ready**: Works with Service Registry + POD buttons via ngrok
* **Hardware toggle**: `USE_FAKE_MOTORS=1` to simulate without the Build HAT

---

## Why this architecture (and what we didn’t do)

We intentionally **removed external web frameworks** (Flask/FastAPI) to avoid package management on constrained or locked-down devices. The server uses `http.server` + `threading`. That makes deployment trivial (copy one file, set env vars), and the API contract stays the same.

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
* LEGO motors connected to ports **A/B/C/D** (you can remap in code)
* Optional: run **without** hardware via `USE_FAKE_MOTORS=1`

> Build HAT tip: on Raspberry Pi OS (Bookworm/bullseye), enable UART so `/dev/serial0` exists and allow your user access:

```bash
sudo raspi-config   # Interface Options → Serial Port → login shell: No, hardware: Yes
# or:
echo "enable_uart=1" | sudo tee -a /boot/firmware/config.txt
sudo usermod -aG dialout,gpio,i2c $USER
sudo reboot
```

---

## Software layout

```
repo/
├─ lego_arm_master.py        # REST server + arm controller
├─ processes/                # on-device production process modules
├─ web/
│  └─ index.html             # built-in on-device control UI
├─ examples/
│  └─ tester.html            # tiny browser client for manual testing
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

See [`processes/README.md`](processes/README.md) for details on creating new
workflows.

**Key components inside `lego_arm_master.py`:**

* `ArmController` – clamps & executes moves, named poses, pick/place helper.
* HTTP handler – routes `/v1/*`, parses JSON, returns `{ ok, data }` or `{ ok:false, error{code,message} }`.
* Operation worker – runs async jobs; `GET /v1/operations/{id}` to poll.
* Auth & idempotency – `x-api-key` and `X-Idempotency-Key`.

---

## Configuration

Set through environment variables (systemd or shell):

| Variable              | Required | Default | What it does                                                      |
| --------------------- | -------- | ------- | ----------------------------------------------------------------- |
| `API_KEY`             | **Yes**  | (none)  | Shared secret for all control endpoints (`x-api-key` header).     |
| `PORT`                | No       | `8000`  | Local listen port.                                                |
| `USE_FAKE_MOTORS`     | No       | `0`     | `1` to simulate motors (dev/demo without Build HAT).              |
| `ALLOW_NO_AUTH_LOCAL` | No       | `0`     | `1` to skip API key for localhost clients only (dev convenience). |

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

Returns current absolute degrees and software limits.

```json
{
  "ok": true,
  "data": {
    "abs_degrees": {"A": 0, "B": 0, "C": 0, "D": 0},
    "limits": {"A": [-360,360], "B": [-180,180], "C": [-180,180], "D": [-90,90]},
    "motors": ["A","B","C","D"]
  }
}
```

### `POST /v1/arm/pose`  *(auth)*

Go to a named pose. Built-in names: `home`, `pick_left`, `pick_right`, `place_left`, `place_right`.

```bash
curl -X POST https://<ngrok>/v1/arm/pose \
  -H "content-type: application/json" -H "x-api-key: $KEY" \
  -d '{"name":"home","speed":60}'
```

### `POST /v1/arm/move`  *(auth)*

Relative or absolute joint moves (`A/B/C/D` in **degrees**).

```json
{
  "mode": "relative",                // or "absolute"
  "joints": { "A": 10, "B": -5 },    // at least one joint
  "speed": 60,                       // 1..100
  "timeout_s": 30,                   // optional
  "async_exec": false                // true -> returns operation_id
}
```

### `POST /v1/arm/pickplace`  *(auth)*

Simple helper sequence.

```json
{ "location": "left|center|right", "action": "pick|place", "speed": 50, "async_exec": false }
```

### `POST /v1/arm/stop`  *(auth)*

Stops all motors immediately.

```json
{ "reason": "operator stop" }
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
* **Nudge** – POST `/v1/arm/move` body `{"mode":"relative","joints":{"A":10},"speed":40}`
* **Pick Left** – POST `/v1/arm/pickplace` body `{"location":"left","action":"pick","speed":50}`
* **Stop** – POST `/v1/arm/stop` body `{"reason":"operator stop"}`

Because headers are set in the Service Registry, you only supply **method/path/body** per action.

---

## On-device web UI

With the service running on the Raspberry Pi you can visit `http://<pi>:8000/` or your ngrok URL to access a small control panel.
It shows current joint positions and provides buttons for common poses, custom moves, and an emergency stop. Enter the API key
and base URL at the top if authentication is enabled.

## Tester page (manual browser testing)

Drop this into `examples/tester.html`, open it, set **Base URL** to your ngrok, and paste your **API key**. Click buttons.

```html
<!doctype html><meta charset="utf-8"><title>LEGO Arm API Tester</title>
<style>body{font-family:sans-serif;max-width:700px;margin:32px auto}
input,button,textarea{margin:6px 0;width:100%}</style>
<h1>LEGO Arm API Tester</h1>
<label>Base URL: <input id="base" value="https://YOUR-NGROK.ngrok-free.app"></label>
<label>API Key: <input id="key" placeholder="paste API key"></label>
<button onclick="call('GET','/v1/health')">GET /v1/health</button>
<button onclick="call('GET','/v1/inventory',null,true)">GET /v1/inventory</button>
<textarea id="move" rows="6">{ "mode":"relative","joints":{"A":10,"B":-5},"speed":40 }</textarea>
<button onclick="post('/v1/arm/move', move.value)">POST /v1/arm/move</button>
<pre id="out" style="background:#111;color:#0f0;padding:12px;white-space:pre-wrap"></pre>
<script>
async function call(m,p,b=null,auth=false){
  const u=document.getElementById('base').value.trim()+p;
  const k=document.getElementById('key').value.trim();
  const h={"content-type":"application/json","ngrok-skip-browser-warning":"1"};
  if(auth && k) h["x-api-key"]=k;
  const r=await fetch(u,{method:m,headers:h,body:b}); 
  document.getElementById('out').textContent = r.status+" "+r.statusText+"\n\n"+await r.text();
}
function post(p,b){return call('POST',p,b,true)}
</script>
```

---

## Safety, calibration & assumptions

* On startup the controller assumes the **current physical pose is absolute zero (0°)** for A/B/C/D. If that’s not true, first move to a safe “home” physically, then start the service.
* Software **limits** (deg): A: ±360, B/C: ±180, D (gripper): ±90. Adjust for your build.
* Motions are executed **sequentially per joint**, blocking, at a simple “speed” scale (1–100).
* Add a **hardware E-stop** and current/limit protections. The `stop` endpoint is not a safety system.

---

## Troubleshooting

**Health works locally but not via ngrok**

* You used `https://<ngrok>:8000/...` → **remove the `:8000`**.
* Or ngrok points to the wrong port. Start it with `ngrok http 8000`.

**401 Unauthorized**

* Missing/wrong `x-api-key`. Set default header in Service Registry.

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

