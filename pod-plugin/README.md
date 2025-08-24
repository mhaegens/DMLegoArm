# LEGO Arm POD Plugin

This folder contains a SAP Digital Manufacturing (DM) POD plugin that lets an operator drive the LEGO® robotic arm exposed by the DMLegoArm project. The plugin is written in SAPUI5 and communicates with the zero‑dependency Python service included in this repository.

## What it does

* **Quick actions** – Buttons for Health, Inventory, Pick, Place, Pose and Reboot.
* **Manual moves** – Form for sending joint commands (motors A‑D) with speed/acceleration, dry‑run option and JSON preview.
* **Console output** – Timestamped log of every request/response.
* **Config bridge** – Settings can be provided by the POD Designer, runtime `postMessage` events or edited via a built‑in dialog. Values are persisted in `localStorage`.

## Backend overview

The plugin talks to `lego_arm_master.py`, a single‑file HTTP server that exposes a stable, versioned `/v1/*` REST API secured with an `x-api-key` header and optional idempotency key. The server can drive real motors or simulate them with `USE_FAKE_MOTORS=1`.

## Configuration

You can configure the following properties in the POD Designer or at runtime:

| Property      | Description |
|---------------|-------------|
| `baseUrl`     | HTTP base of the DMLegoArm API (default `http://legopi.local:5000`). |
| `authMode`    | `none`, `basic`, `bearer` or `x-api-key`. |
| `username` / `password` | Used when `authMode` is `basic`. |
| `bearerToken` | Used when `authMode` is `bearer`. |
| `apiKey`      | Used when `authMode` is `x-api-key`. |

## Deployment

1. Copy this entire folder to the POD plugin hosting location. The files are ready to run without a build step.
2. Register the plugin using `manifest.json` and point the POD to `index.html`.
3. Ensure `lego_arm_master.py` is running and reachable (ngrok or local network) before opening the POD.

*Optional:* If you use UI5 tooling you can bundle the resources with `ui5 build --all` before deployment.

## Development tips

* UI logic lives in [`controller/MainView.controller.js`](controller/MainView.controller.js); the layout is defined in [`view/MainView.view.xml`](view/MainView.view.xml).
* The property editor for POD Designer is implemented in [`builder/PropertyEditor.js`](builder/PropertyEditor.js).
* `index.html` bootstraps SAPUI5, sets up a small configuration bridge and loads the component.
