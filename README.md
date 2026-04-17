# jarvis-device-zwave

Z-Wave device protocol adapter for [Jarvis](https://github.com/alexberardi/jarvis). Discovers and controls Z-Wave devices via [Z-Wave JS UI](https://github.com/zwave-js/zwave-js-ui).

## Supported Domains

| Domain | Actions | Z-Wave Command Class |
|--------|---------|----------------------|
| switch | turn_on, turn_off, toggle | Binary Switch (37) |
| light | turn_on, turn_off, set_brightness, toggle | Multilevel Switch (38) |
| lock | lock, unlock | Door Lock (98) |
| climate | set_temperature, set_hvac_mode | Thermostat Setpoint/Mode (67/64) |
| cover | open, close, stop | Multilevel Switch (38) |

## How It Works

1. **Z-Wave JS UI** manages your Z-Wave USB stick and mesh network
2. You pair devices using Z-Wave JS UI's web interface
3. This adapter **discovers** paired devices and makes them available in Jarvis
4. Voice commands and the mobile app control devices via Z-Wave JS UI's socket.io API

## Setup

1. Install a Z-Wave USB stick (e.g., Zooz ZST39, Aeotec Z-Stick 7) on your Pi
2. Run [Z-Wave JS UI](https://github.com/zwave-js/zwave-js-ui) as a Docker container
3. Pair your Z-Wave devices via the Z-Wave JS UI web interface
4. Install this package from the Jarvis Pantry
5. Set the **Z-Wave JS UI URL** secret (e.g., `http://10.0.0.244:8091`)
6. Scan for devices

## Components

- **ZWaveProtocol** (`IJarvisDeviceProtocol`) — discover, control, and query Z-Wave devices
- **ZWaveAgent** (`IJarvisAgent`) — background cache refresh every 5 minutes
- **ZWaveService** — singleton socket.io client shared by both

## Dependencies

- `python-socketio[asyncio_client]`

## License

MIT
