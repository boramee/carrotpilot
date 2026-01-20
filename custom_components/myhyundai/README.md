# MyHyundai (unofficial) Home Assistant integration

This is a minimal custom integration that uses the
`hyundai-kia-connect-api` library to read vehicle status and trigger
remote start.

## Requirements

- MyHyundai (Korea) account with username/password login
- Home Assistant with custom component support
- OTP/2FA accounts are not supported in this version

## Install

1. Copy `custom_components/myhyundai` into your Home Assistant
   configuration directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search
   for **MyHyundai**.
4. Enter your MyHyundai email and password.
5. If your account requires a vehicle PIN for remote commands, enter it
   during setup.

If you have more than one vehicle, the setup will prompt you to select
which vehicle to add.

## Notes for Korea (MyHyundai)

The `hyundai-kia-connect-api` library does not expose a dedicated Korea
region selector. This integration uses the library's "India" region as a
fallback because the API endpoints are compatible for some accounts.
If login fails, the library may not currently support your account.

## Entities

### Sensors

- Odometer
- Total driving range
- Fuel level
- Car battery
- EV battery (if supported)
- Air temperature
- Last updated

### Binary sensors

- Engine running
- Hood open
- Trunk open

### Lock

- Door lock (lock/unlock)

### Buttons

- Remote start
- Remote stop

## Remote start defaults

Remote start uses the API defaults for temperature and duration. If you
need to change defaults, edit constants in `custom_components/myhyundai/const.py`.
