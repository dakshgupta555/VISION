# Live sample contract

The live simulator (`python -m dtwin.live`) and the ESP32 monitoring device
send the same JSON to the backend. One sample per POST, or a batch.

    POST /api/live/sample
    Content-Type: application/json

    {
      "source": "sensor",                    // "sensor" (ESP32) or "simulator"
      "transformer_id": "DT-001",
      "timestamp": "2026-05-10T14:15:00",    // ISO 8601, device local time; an offset is accepted and dropped
      "IR": 61.2, "IY": 59.8, "IB": 60.4,    // phase currents, A
      "VR": 241.3, "VY": 242.0, "VB": 240.1, // phase-to-neutral voltages, V
      "oil_temp": 58.4,                      // top-oil temperature, C
      "ambient_temp": 34.9,                  // ambient temperature, C
      "period_s": 900                        // optional: seconds until the next sample
    }

- A channel the device cannot read is sent as `null`. That is a **missing
  channel** (the validator reports it dead), not stale data.
- If no sample arrives within **3 sample periods**, the twin marks every
  assessed component **grey, "no recent data"**. The period is `period_s` if
  sent, otherwise the median interval between arrivals.
- `source` must be `sensor` or `simulator`; anything else is rejected (422).
  The dashboard labels the stream "Measured: sensor" or "Simulated: physics
  model" from it.
- Batch form, used for the simulator's prewarm:
  `{"samples": [ ... ], "prewarm": true}`. Prewarm samples fill the history
  but do not count as live arrivals, and the dashboard marks their period.

Response: `{"accepted": 1, "analysis_ms": 110.3, "version": 512}`.

The existing analytics then run on the buffer using past data only, and every
subscriber to `GET /api/live/stream` (Server-Sent Events) receives the new
state. Faults injected from the dashboard (`/api/live/inject`) are read only
by the simulator, which changes its physics; the analytics receive samples and
nothing else.
