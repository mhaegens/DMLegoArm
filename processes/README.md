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
endpoint. This repo ships with example processes `pick-assembly-quality` and
`pick-quality-assembly` that move parts between assembly and quality stations.

