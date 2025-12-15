# LEGO Arm Control V2 POD Plugin

This folder contains a refreshed SAP Digital Manufacturing (DM) POD plugin that gives operators a richer view of the LEGO® arm and faster motion presets. The plugin is written in SAPUI5 and communicates with the zero‑dependency Python service included in this repository.

## What it does

* **Health + telemetry** – Live health checks plus a state snapshot showing joint angles and limits.
* **Preset joints** – One-tap buttons for common points (A open/close, B&C min/pick/max, D assembly/quality/neutral) with a configurable speed.
* **Pose shortcuts** – Combined poses such as Pick Assembly, Pick Quality, Neutral and Flex.
* **Nudges** – Adjustable +/- step controls per joint using rotation units and a configurable speed.
* **Console output** – Timestamped log of every request/response.
* **Config bridge** – Settings can be provided by the POD Designer, runtime `postMessage` events or edited via a built‑in dialog. Values are persisted in `localStorage`.

## Backend overview

The plugin talks to `lego_arm_master.py`, a single‑file HTTP server that exposes a stable, versioned `/v1/*` REST API secured with an `x-api-key` header and optional idempotency key. The server can drive real motors or simulate them with `USE_FAKE_MOTORS=1`.

## Configuration

You can configure the following properties in the POD Designer or at runtime:

| Property      | Description |
|---------------|-------------|
| `baseUrl`     | HTTP base of the DMLegoArm API (default `http://legopi.local:8000`). |
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
