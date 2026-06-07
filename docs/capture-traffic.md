# Capturing SensorLinx Android traffic

You usually do **not** need this step. The integration uses [pysensorlinx](https://github.com/sslivins/pysensorlinx), which already reverse-engineered the cloud API at `https://mobile.sensorlinx.co`.

Capture traffic only if:

- Your controller type is not supported yet
- You want to verify what the app sends before changing setpoints
- HBX changes the API and the library needs updating

## Option A: HTTP Toolkit (easiest)

1. Install [HTTP Toolkit](https://httptoolkit.com/) on your PC.
2. Choose **Android device via ADB** or **Manual Android setup**.
3. Install the HTTPS certificate on your phone when prompted.
4. Open the SensorLinx app and log in, browse devices, change a setpoint.
5. Filter for host `mobile.sensorlinx.co`.
6. Export the session as HAR.
7. Run:

```bash
python scripts/analyze_har.py captures/session.har
```

## Option B: mitmproxy

1. Install mitmproxy: `pip install mitmproxy`
2. Start it: `mitmweb --listen-port 8080`
3. On Android, set Wi-Fi proxy to your PC IP, port 8080.
4. Browse to `http://mitm.it` on the phone and install the Android cert.
5. Use the SensorLinx app normally.
6. Save flows or export HAR from mitmweb.

## What to look for

| Endpoint | Purpose |
|----------|---------|
| `POST /account/login` | Email/password auth, returns bearer token |
| `GET /account/me` | User profile |
| `GET /buildings` | List homes/sites |
| `GET /buildings/{id}/devices` | All controllers and thermostats |
| `PATCH /buildings/{id}/devices/{syncCode}` | Change setpoints, modes, away |

For THM-0600 floor thermostats, useful fields include:

- `rm` — room temperature (°F)
- `flr` — floor temperature (°F)
- `rmT` / `rmCT` — heat/cool setpoints
- `cngOvr` — mode (auto/heat/cool/off)
- `away` / `awayMode` — away preset
- `dmd` — active heating/cooling demand bitfield

## HBX documentation

Official product docs live at [hbxcontrols.com/resources](https://www.hbxcontrols.com/resources).
