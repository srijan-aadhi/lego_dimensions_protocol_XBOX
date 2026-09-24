# Lego Dimensions Xbox One Toy Pad → Desk Lamp: Project Notes

## Goal
Use an **Xbox One** Lego Dimensions toy pad as an RGB desk lamp on a Windows PC, controlled from Python, and have it start by itself when Windows starts.

Fork of https://github.com/woodenphone/lego_dimensions_protocol (remote `upstream`); this fork is remote `origin`, branch `master`. GitHub CLI handles HTTPS credentials (`gh auth setup-git`).

Machine-specific details (local paths and the like) live in `LOCAL_NOTES.md`, which is git-ignored.

## Status: working, auto-starts at log-on ✅
- The pad is detected, initialised and lights up.
- Solid colours, smooth fades between colours, and a rainbow cycle all work on the real hardware.
- A Task Scheduler task starts the lamp hidden 30 s after log-on (rainbow, 20 s per lap).
- The lamp waits for the pad if it is missing, logs to a file, and can be stopped cleanly with `lamp_off.py`. Reconnecting after an unplug is implemented but has not been tried with a real unplug yet.

## Environment
- Windows 11, Python 3.12 (a per-user install under `%LOCALAPPDATA%\Programs\Python\Python312`, which is what `install_startup_task.ps1` assumes), pip 24.0.
- Toy pad USB ID **0E6F:0141** (Xbox One version).

## Dependencies installed
pip can't download from PyPI on this PC. It fails with `ProtocolError('Connection aborted.', OSError(22, 'Invalid argument'))`.
- **What was ruled out:** `curl.exe` reaches pypi.org fine, the WinHTTP proxy is "Direct access", and pip is up to date.
- **Root cause:** not found. It is likely something interfering with Python's network connections, such as antivirus or a network optimizer. The Python-level connection tests and `netsh winsock reset` were suggested but not run.

**Workaround:** the needed `.whl` files are kept in `deps\`. Install them offline:
```powershell
python -m pip install --no-index --find-links .\deps pyusb libusb-package
```

Installed packages:
- **`pyusb`**: the USB library (`import usb.core`).
- **`libusb-package` 1.0.30.0** (`libusb_package-1.0.30.0-py3-none-win_amd64.whl`): bundles the libusb DLL for Windows.
- **`importlib_resources`**: a dependency of libusb-package.

Always use `python -m pip`. A plain `pip` once installed pyusb somewhere the script's Python didn't see.

## Driver
Zadig was used to replace the pad's driver with **WinUSB** (device `USB\VID_0E6F&PID_0141`).
- ⚠️ The pad won't work on an Xbox again until the original driver is restored (Device Manager → roll back driver).
- Only one program can hold the pad at a time. A second `desk_lamp.py` gets `USBError: [Errno 13] Access denied` and keeps retrying until the first one exits.

## Files in the project folder
The Python files below are Python 3 rewrites of the original Python 2 code.

### `lego_dimensions_gateway.py` (library)
- **Python 2 → 3 conversion:** `print()` calls, `class Gateway:`, `sum() % 256` checksum, non-mutating padding, and safe cleanup in `close()` and `__del__`.
- **Xbox One support:**
  - The pad is detected by product ID `0x0141`.
  - Each normal 32-byte LEGO message is wrapped in an Xbox GIP packet: `21 00 <seq> 20 <32-byte message>`.
  - The sequence number runs 1–255 and skips 0.
  - Writes go to endpoint `0x01` and reads come from `0x81`.
  - The protocol comes from https://github.com/Ellerbach/LegoDimensions/blob/main/XboxPortalProtocol.md
- **Xbox start-up handshake:**
  1. Clear any waiting messages, then send the wrapped wake message.
  2. If a LEGO reply arrives, the pad is already unlocked.
  3. Otherwise send GIP authenticate (`06 20 <seq> 02 01 00`), wait, and resend the wake if needed.
- On this pad the observed path is **"already unlocked"**: the wake reply arrives immediately, and every command gets the acknowledgment `55 01 02 58`. The authenticate branch has not been exercised yet; after a cold boot, check the log for "Unlocking Xbox One portal".
- **Bug fixed:** the routine that clears waiting messages originally looped forever and hung at "Initialising portal". It is now time-limited: 0.25 s at start-up and 0.05 s after each command, so fades stay smooth.
- Every USB write has a 1 s timeout.
- The optional `libusb_package` backend is used when it's installed.
- Verbose output goes through the `logging` module (logger `lego_dimensions_gateway`), so it reaches the log file when there is no console.
- The PS3/PS4/Wii U pad (`0x0241`) still uses the original unwrapped path.

### `desk_lamp.py` (the lamp)
- **Colours:**
  - Accepts presets or hex colours: warm, soft, daylight, cool, reading, night, focus, red, orange, yellow, green, teal, blue, purple, pink.
  - A single colour fades in over 1.5 s.
  - Several colours fade between each other in a loop.
- **Options:**
  - `-b` brightness (1–100)
  - `-t` fade length, or lap length with `--rainbow` (default 5 s, or 60 s for rainbow)
  - `--hold` seconds to stay on each colour
  - `--rainbow` cycle through all colours
  - `--list` show presets
  - `--log` also write messages to `%LOCALAPPDATA%\LegoLamp\desk_lamp.log` (1 MB, 2 backups). Turned on automatically when there is no console. `--log-file FILE` picks another file.
  - `-v` verbose logging (every USB packet, 20 lines a second; don't leave this on in the startup task)
- **How fades work:** the script mixes the colours itself and sends about 20 updates per second with command `C0` (all pads). It skips repeated colours.
- **Missing pad:** at start-up it retries with a back-off of 2 s doubling to 30 s until the pad answers, logging only when the error changes. Verified with a busy pad (second instance got `Access denied` and retried). If the pad is unplugged while running, the USB error is caught, the connection is dropped, and the same retry loop waits for it to return. **Not yet tested** with a real unplug.
- **Stopping:** Ctrl+C, or `lamp_off.py`. Both turn the pads off before exiting.
- **State files** in `%LOCALAPPDATA%\LegoLamp\`: `desk_lamp.pid` while running, `stop` while a stop is pending, `desk_lamp.log`.

### `lamp_off.py`
Creates the `stop` file and waits for the running lamp to delete it as it exits (up to 10 s). It cannot talk to the pad itself because the running lamp holds the USB connection. Killing the lamp process instead (Task Manager, or "End" in Task Scheduler) leaves the pads lit at the last colour.

### `install_startup_task.ps1`
Registers (or updates) the Task Scheduler task **"Lego Dimensions Desk Lamp"** and starts it:
- Runs `pythonw.exe desk_lamp.py --rainbow -t 20 --log` from the project folder, so there is no console window.
- Trigger: at log-on of the current user, 30 s delay so USB has enumerated the pad.
- Runs only when the user is logged on (interactive token); background tasks can't reach USB reliably.
- No execution time limit (the default would kill it after 3 days), starts on battery, doesn't stop on battery, ignores a second start while running.
- `-Arguments '...'` changes the lamp options, `-Uninstall` turns the lamp off and removes the task.
- If PowerShell blocks the script: `powershell -ExecutionPolicy Bypass -File .\install_startup_task.ps1`
- If the project folder lives in OneDrive and "Files On-Demand" ever dehydrates it, `pythonw` may find placeholders at log-on before OneDrive has started. Right-click the project folder → **Always keep on this device** to prevent that.

### `blinkenlights.py`
The original demo, converted to Python 3 and tidied: a shared send helper, an `ALL_PADS_OFF` constant, and a clean Ctrl+C exit. Its raw packets are **not** wrapped in GIP, so it only works with non-Xbox pads.

## Everyday commands
```powershell
python desk_lamp.py                  # warm white
python desk_lamp.py warm cool night  # loop of fades
python desk_lamp.py --rainbow -t 10  # rainbow, 10 s per lap
python lamp_off.py                   # turn off the hidden auto-started lamp
Start-ScheduledTask 'Lego Dimensions Desk Lamp'   # turn it back on
Get-Content $env:LOCALAPPDATA\LegoLamp\desk_lamp.log -Tail 20
```
Stop the auto-started lamp with `lamp_off.py` before running `desk_lamp.py` by hand, otherwise the manual one waits for the pad.

## Ideas not done
- **Tray icon** for on/off. Skipped because pip is offline on this PC and it would need more wheels (pystray, Pillow).
- **Turn off at sleep/shutdown.** A `pythonw` process gets no Ctrl+C or window message, so the pads keep their last colour until USB power drops. Waking from sleep should be fine: the USB error path reconnects.
- **Restore the Xbox driver** if the pad is ever needed on the console again.
