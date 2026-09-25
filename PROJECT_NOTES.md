# Lego Dimensions Xbox One Toy Pad → Desk Lamp: Project Notes

## Goal
Use an **Xbox One** Lego Dimensions toy pad as an RGB desk lamp on a Windows PC, controlled from Python, and have it start by itself when Windows starts.

Fork of https://github.com/woodenphone/lego_dimensions_protocol (remote `upstream`); this fork is remote `origin`, branch `master`. GitHub CLI handles HTTPS credentials (`gh auth setup-git`).

Machine-specific details (local paths and the like) live in `LOCAL_NOTES.md`, which is git-ignored.

## Status: working, auto-starts at log-on ✅
- The pad is detected, initialised and lights up.
- Solid colours, smooth fades between colours, and a rainbow cycle all work on the real hardware.
- A Task Scheduler task starts the lamp hidden at log-on (rainbow, 66.5 s per lap). Verified after a real reboot on 2026-09-23: the task fired on time (then with a 30 s delay), but the lamp took about 2 minutes to come on: Python needed ~65 s to start on the cold boot and ~16 s to connect. The delay has since been removed and the task priority raised; a warm start takes ~1 s from task start to "Lamp on". Second reboot, same day, with those changes: boot 22:12:12, log-on 22:12:22, task fired 22:12:27, Python's first line 22:12:41, portal found 22:12:49, authenticate handshake done and lamp on 22:12:50. That is 28 s after log-on; the remaining time is interpreter and library loading from a cold disk.
- The lamp waits for the pad if it is missing, logs to a file, and can be stopped cleanly with `lamp_off.py`. Reconnecting after an unplug was verified with a real unplug on 2026-09-25. Recovery after the PC sleeps is implemented and was verified on 2026-09-25 against a pad left stuck by a real sleep/wake (see **Sleep/wake** below).

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
- While the pad keeps USB power (warm starts, sign-out/in) the observed path is **"already unlocked"**: the wake reply arrives immediately, and every command gets the acknowledgment `55 01 02 58`. After a full shutdown the pad loses power and needs the **authenticate** path; verified working on 2026-09-23 (pad ready within 1 s of the authenticate packet). The handshake milestones ("Found portal", "already unlocked" or "Unlocking Xbox One portal", "portal ready") and the connect time are always logged, so the log from an unattended start shows which path ran.
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
  - `--gamma` LED gamma correction (default 2.2; `1` sends raw values)
  - `--list` show presets
  - `--log` also write messages to `%LOCALAPPDATA%\LegoLamp\desk_lamp.log` (1 MB, 2 backups). Turned on automatically when there is no console. `--log-file FILE` picks another file.
  - `-v` verbose logging (every USB packet, 20 lines a second; don't leave this on in the startup task)
- **How fades work:** the script mixes the colours itself and sends about 20 updates per second with command `C0` (all pads). It skips repeated colours.
- **Gamma correction (added 2026-09-23):** the pad's LEDs are linear in light output, but eyes are not: a channel at 40/255 already looks ~40% lit. Without correction the rainbow rushed away from pure red (and green and blue) because the next channel became visible almost immediately. Colours are now treated as perceptual values (like sRGB hex codes) and encoded with `value^2.2` before sending. The presets were converted so they produce the same LED values as before (`warm` is stored as (255,188,110) and reaches the LEDs as (255,130,40)). Hex colours typed on the command line are now interpreted perceptually, which is what a hex code normally means. Lowest steps are still 8-bit: LED value 1 looks ~8% bright, so a channel switching on is the one remaining visible step.
- **Perceptually even rainbow (added 2026-09-23):** walking HSV hue at a constant speed still looked uneven after gamma correction: measured in Oklab, the perceived hue barely moves near pure red/green/blue (speed ~0) and sprints through orange-yellow and cyan (speed ~2x). `Rainbow` builds a 720-entry table at start-up that maps HSV hue to the *perceived distance* travelled along the wheel (Oklab L,a,b distance between neighbouring colours, after dimming/boosting) and inverts it, so time is spent in proportion to visible change.
  - First version paced by Oklab *hue angle* alone. That produced two visible jumps: near pure green the hue angle is flat while the colour still changes (one frame went (19,227,0) -> (0,216,21), 17x the average step), and between azure and blue the hue angle runs backwards for ~10° of HSV, which was clipped to zero and became a plateau (one frame (0,64,255) -> (7,0,255), 31x). Distance-based pacing has no plateaus and a worst frame of ~5x average, which is rounding. Fixed 2026-09-23.
- **Rainbow taste knobs (added 2026-09-23):** even perceived pacing still *felt* slow through green-blue, because the eye has one name ("blue") for a stretch as wide as red+orange+yellow. So three optional adjustments, all smooth raised-cosine windows over HSV hue:
  - `--cool-speed X` (default 1.3): move X times faster through lime..green..cyan..blue..purple (window centred on 195°, half-width 105°). `1` = even. Mostly changes how long cyan lasts; the green->cyan and cyan->blue transitions move less.
  - `--cool-dim F` (default 0.8): brightness multiplier over lime..green..cyan..azure (centre 150°, half-width 75°). Green and cyan are nearly as light as yellow (Oklab L 0.87/0.91 vs 0.97); dimming them to ~0.8 makes yellow the visible brightness peak. Yellow itself cannot be made brighter: both its LEDs are already at full.
  - `--yellow-boost F` (default 0.2): mixes up to F of blue into yellow (centre 60°, half-width 30°). A whiter yellow reads as brighter on RGB LEDs at the cost of saturation.
  - `--dwell COLOUR=X` (repeatable): linger X times longer around a hue, half-width 30°. COLOUR is a name (red, orange, yellow, lime, green, teal, cyan, azure, blue, violet, magenta, rose; 30° apart starting at red = 0°) or degrees. There is a default dwell for every band (`DEFAULT_HOLDS` in `desk_lamp.py`), fitted numerically so that a lap spends the seconds per band that were tuned by eye; a given `--dwell` overrides that one band. Because the windows overlap and multiply, the factors are not intuitive on their own: change them by feel one at a time, or refit. (`--hold` is a different option: seconds to stay on each colour in multi-colour mode.)
  - **Tuned by eye on 2026-09-23.** Seconds per lap (30° bands centred on each name): red 7.1, orange 10.0, yellow 7.4, lime 2.7, green 2.3, teal 5.7, cyan 7.9, azure 3.7, blue 2.3, violet 5.6, magenta 4.3, rose 7.5 = **66.5 s**, which is the start-up lap. Green and blue were ~0.5 s and ~1.2 s under hue-angle pacing; the switch to distance pacing gave them their natural ~2.3 s each (that *is* the fix for the jumps) and the lap grew by the difference rather than taking it from other colours. The table is renormalised, so raising one dwell shortens everything else a little; to add time to one colour without touching the others, lengthen the lap by the same proportion.
  - Bug found while adding these: the perceptual table accumulated each step relative to the *weighted* running total instead of the previous raw angle, which made any weighting cancel itself out. Fixed.
- **Missing pad:** at start-up it retries with a back-off of 2 s doubling to 5 s until the pad answers, logging only when the error changes. Verified with a busy pad (second instance got `Access denied` and retried). If the pad is unplugged while running, the USB error is caught, the connection is dropped, and the same retry loop waits for it to return. Verified with a real unplug/replug on 2026-09-25: I/O error, "Device not found", back on 7 s after the replug via the authenticate path. The ceiling was 30 s until then; a sleep + unplug + wake + replug test showed the replug missing one attempt by a second and then waiting the full 30 s, so it was lowered to 5 s (checking for the pad is cheap and repeats are not logged).
- **Sleep/wake (fixed 2026-09-25):** when the PC sleeps, Windows suspends the USB bus but keeps the pad's device instance: after the wake it still lists the pad as present, OK and fully powered, and never re-enumerates or resets it. The pad, though, stops accepting bulk writes: the lamp's next write times out (`[Errno 10060] Operation timed out`), and every reconnect attempt found the pad and then timed out on the wake message, silently (the retry loop only logs when the error text changes), every 30 s for two hours. Nothing in the old code ever reset the pad, so it never recovered. Fix: `Gateway(reset=True)` clears the halt on both endpoints (`dev.clear_halt`, i.e. `CLEAR_FEATURE(ENDPOINT_HALT)` plus a data-toggle reset) after claiming the interface and before the handshake. `connect()` switches it on after any attempt that finds the pad but times out, and the lost-contact path passes it straight away when the error was a timeout (an unplug is not a timeout, and a replug needs no reset). Verified against the stuck pad: attempt 1 timed out as before, attempt 2 with the reset went wake -> authenticate -> "portal ready" -> lamp on in 5 s. Then verified through a real ~35 s sleep with the lamp running under the task: the write timed out as the PC went to sleep ("Lost contact"), the reconnect attempt started and was frozen with the PC, and on wake it ran the reset, took the authenticate path and logged "Lamp back on" within a second of the resume, on the first attempt. Notes for anyone touching this: pyusb's `dev.reset()` is useless here, because it releases the interface before libusb's WinUSB reset runs and that reset only touches endpoints of claimed interfaces, so it silently does nothing; and WinUSB cannot cycle the port at all (only libusbK can). If clear-halt ever stops being enough, the next lever is restarting the device node (`pnputil /restart-device`), which needs admin. With `-v` and `LIBUSB_DEBUG=4` the pad can be seen re-announcing itself (GIP `02 20 ...` packets with its serial) after the wake, so it needs the authenticate path again, not the "already unlocked" one.
- **Stopping:** Ctrl+C, or `lamp_off.py`. Both turn the pads off before exiting.
- **State files** in `%LOCALAPPDATA%\LegoLamp\`: `desk_lamp.pid` while running, `stop` while a stop is pending, `desk_lamp.log`.

### `lamp_off.py`
Creates the `stop` file and waits for the running lamp to delete it as it exits (up to 10 s). It cannot talk to the pad itself because the running lamp holds the USB connection. Killing the lamp process instead (Task Manager, or "End" in Task Scheduler) leaves the pads lit at the last colour.

### `install_startup_task.ps1`
Registers (or updates) the Task Scheduler task **"Lego Dimensions Desk Lamp"** and starts it:
- Runs `pythonw.exe desk_lamp.py --rainbow -t 66.5 --log` from the project folder, so there is no console window. Change the lap time or any rainbow knob with e.g. `-Arguments '--rainbow -t 90 --dwell red=6'` and re-run the script.
- Trigger: at log-on of the current user, no delay (the lamp waits for the pad itself). `-DelaySeconds N` adds one if ever needed.
- Runs only when the user is logged on (interactive token); background tasks can't reach USB reliably.
- No execution time limit (the default would kill it after 3 days), normal process priority (Task Scheduler's default is below normal, which gets starved during log-on), starts on battery, doesn't stop on battery, ignores a second start while running.
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
- **Faster cold boot.** The ~65 s before Python's first line on a cold boot is Windows-side (disk contention from start-up apps, antivirus scanning the interpreter). If it matters, a Microsoft Defender exclusion for the Python folder is the usual lever; it needs admin and is a security trade-off.
- **Tray icon** for on/off. Skipped because pip is offline on this PC and it would need more wheels (pystray, Pillow).
- **Turn off at sleep/shutdown.** A `pythonw` process gets no Ctrl+C or window message, so the pads keep their last colour until USB power drops. (Waking from sleep was assumed to be fine via the USB error path; it was not, see **Sleep/wake** above. Fixed and verified through a real sleep/wake on 2026-09-25.)
- **Restore the Xbox driver** if the pad is ever needed on the console again.
