
## Production processes

This folder hosts reusable production workflows that run **on the Raspberry Pi**.
Each process is implemented as a small Python module exposing a `run(arm)`
function. The REST API automatically exposes every entry in
`processes.PROCESS_MAP` under:

```
POST /v1/processes/<name>
```

Calling an endpoint executes the corresponding sequence of arm movements.

### Adding a new process

1. Create a `<process_name>.py` file with a `run(arm)` function.
2. Register it in `processes/__init__.py`'s `PROCESS_MAP`.
3. The service will expose `POST /v1/processes/<process_name>`.

All logic lives on the device so DM only needs to trigger the appropriate
endpoint. This repo ships with these processes:

* `pick-assembly-quality` – move parts from assembly to quality.
* `pick-quality-assembly` – move parts from quality to assembly.
* `shutdown` – park the arm (A open, B/C min, D neutral) then power off the Pi.
* `test` – run a quick joint exercise across the calibrated points.

The JSON files in this folder (`PickAssemblyQuality.json`, etc.) are sample
request sequences you can import into external tooling or use as reference when
building DM process definitions.
