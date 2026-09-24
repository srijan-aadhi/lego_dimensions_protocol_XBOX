# Lego Dimensions toy pad as a desk lamp

This fork of [woodenphone/lego_dimensions_protocol](https://github.com/woodenphone/lego_dimensions_protocol)
turns a Lego Dimensions toy pad into an RGB desk lamp controlled from Python 3 on Windows.
Unlike the original, it supports the **Xbox One** pad (USB ID `0E6F:0141`) by wrapping the
LEGO messages in Xbox GIP packets. The PS3/PS4/Wii U pad still works through the original path.

Full setup, design and troubleshooting notes are in [PROJECT_NOTES.md](PROJECT_NOTES.md).

## Quick start (Windows, Python 3.12)

1. Replace the pad's driver with WinUSB using [Zadig](https://zadig.akeo.ie/).
   (The pad won't work on an Xbox again until the original driver is rolled back.)
2. Install the USB libraries from the bundled wheels, no internet needed:
   ```powershell
   python -m pip install --no-index --find-links .\deps pyusb libusb-package
   ```
3. Try it:
   ```powershell
   python desk_lamp.py                  # warm white
   python desk_lamp.py --rainbow -t 20  # rainbow, one lap every 20 s
   python desk_lamp.py --list           # colour presets
   ```
4. Start it automatically at log-on (hidden, rainbow):
   ```powershell
   .\install_startup_task.ps1           # -Uninstall to remove
   python lamp_off.py                   # turn the hidden lamp off
   ```

## Files

- `lego_dimensions_gateway.py`: library for the pad's LED commands (Xbox One and original pads).
- `desk_lamp.py`: the lamp. Presets, hex colours, fades, rainbow, retry/reconnect, file logging.
- `lamp_off.py`: asks a running lamp to turn off and exit.
- `install_startup_task.ps1`: Task Scheduler registration.
- `blinkenlights.py`, `morse.py`, `demo.py`: original demos (non-Xbox pads only).
- `command notes/`, `checksum/`, `logs/`: the original protocol reverse-engineering material.

---

## Original readme

This project is currently focused on working out the communications protocol used.
The LED control commands appear to be deciphered.
A python library for controling the gateway device's lights is provided. (lego_dimensions_gateway.py)

The Xbox variant of the portal is not supported. *(Now supported by this fork, see above.)*

Windows installation:
Make sure the latest python 2.x is installed.
http://www.ninite.com is an easy way to install python.

Install LibUSB
Open the start menu
In the search box at the bottom, type "command"
Click on "Command Prompt" result which will appear at the top
Copy the following command and then right click in the command prompt window and select "Paste"
"C:\Python27\Scripts\pip install pyusb"
You now have the python bindings for libusb installed, but there is still more that needs doing.

Download libusb-win32 and extract it. http://sourceforge.net/projects/libusb-win32/
Find if you are using a 32bit(x86) or 64bit(x64 A.K.A. amd64) computer.
open the bin/ folder.
Open the folder that matches your computer.
Plug in the USB portal device.
Run install-filter-win.exe
Make sure "Install a device filter" is selected.
Click next.
You will be given a list of USB devices.
Choose the LEGO one.
It will go back to the device selection screen after it installs
Now that it does not have any option for a LEGO device, click "Cancel" to exit the installer.

Run "morse.py" to test that everything worked.
If the pads on the gateway portal device begin flashing, you have succeeded in installing everything.

Linux Installation:

Make sure Python 2.x is installed.
```sudo apt-get install python``` for debian users. Other distros will vary.
Install pyusb from your software center and pip.

Install Libusb (and Libusb1) from pip.

Download Reposotory from green clone button, or in command line
```git clone https://github.com/woodenphone/lego_dimensions_protocol```

Run "morse.py" to test that everything worked.
If the pads on the gateway portal device begin flashing, you have succeeded in installing everything.
