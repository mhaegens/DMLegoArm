# Production processes

This directory stores reusable production processes for SAP Digital Manufacturing.
Each process is a JSON file describing a sequence of API calls that the POD can
trigger via button. Steps are executed in order.

## File format

```json
{
  "description": "Short summary",
  "steps": [
    {"method": "POST", "path": "/v1/arm/pose", "body": {"name": "home", "speed": 60}},
    {"method": "POST", "path": "/v1/arm/move", "body": {"mode": "relative", "joints": {"A": -90}, "speed": 50}}
  ]
}
```

Add new process files using the same structure so that future production
processes can be maintained here.
