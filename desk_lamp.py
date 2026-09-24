"""
Use a Lego Dimensions toy pad (including the Xbox One version) as a desk lamp.

Examples:
    python desk_lamp.py                      # warm white, full brightness
    python desk_lamp.py daylight -b 40       # a preset at 40% brightness
    python desk_lamp.py ff6600               # any hex colour
    python desk_lamp.py warm cool night      # fade between colours, looping
    python desk_lamp.py warm cool -t 20      # ...taking 20 seconds per fade
    python desk_lamp.py warm cool --hold 60  # ...staying on each colour for a minute
    python desk_lamp.py --rainbow            # slowly cycle through every colour
    python desk_lamp.py --rainbow -t 120     # ...one full cycle every 2 minutes
    python desk_lamp.py --list               # show presets

The lamp stays on while the script runs. Press Ctrl+C to turn it off, or run
lamp_off.py from another window (the only way when the lamp was started
hidden by Task Scheduler, see install_startup_task.ps1).

If the pad is missing at start-up the script keeps retrying until it appears,
and if the pad is unplugged while running it reconnects when it comes back.
"""
import argparse
import bisect
import colorsys
import math
import logging
import logging.handlers
import os
import sys
import tempfile
import time
from pathlib import Path

import usb.core

from lego_dimensions_gateway import Gateway

log = logging.getLogger("desk_lamp")

# RGB LEDs look bluish at (255, 255, 255), so "white" presets are tuned warmer.
# Values are perceptual (like sRGB hex colours); see GAMMA below.
PRESETS = {
    "warm": (255, 188, 110),       # cosy, incandescent-bulb feel
    "soft": (255, 212, 159),       # neutral-warm
    "daylight": (255, 238, 218),  # the brightest, most "white" option
    "cool": (228, 238, 255),
    "reading": (255, 223, 181),
    "night": (181, 59, 0),        # dim red, easy on the eyes after dark
    "focus": (200, 228, 255),
    "red": (255, 0, 0),
    "orange": (255, 151, 0),
    "yellow": (255, 218, 0),
    "green": (0, 255, 0),
    "teal": (0, 228, 200),
    "blue": (0, 0, 255),
    "purple": (194, 0, 255),
    "pink": (255, 110, 181),
}

UPDATES_PER_SECOND = 20  # how often colours are sent during a fade

# The pad's LEDs are linear: 40/255 is 16% of the light but looks about 40% as
# bright. Colours are therefore gamma-encoded before sending, so fades and the
# rainbow move at a steady *visual* pace instead of rushing away from pure
# red/green/blue. 1.0 sends values unchanged.
GAMMA = 2.2

# Where the running lamp keeps its state. lamp_off.py uses the same paths.
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "LegoLamp"
STOP_FILE = STATE_DIR / "stop"            # created by lamp_off.py to ask the lamp to exit
PID_FILE = STATE_DIR / "desk_lamp.pid"    # exists while a lamp is running
DEFAULT_LOG_FILE = STATE_DIR / "desk_lamp.log"

# Reconnect back-off when the pad is missing: start quick, settle at a slow poll.
RETRY_MIN_SECONDS = 2
RETRY_MAX_SECONDS = 30

# Errors that mean "no usable pad right now" rather than a bug in the script.
PAD_ERRORS = (ValueError, RuntimeError, usb.core.USBError)


class StopRequested(Exception):
    """Raised from inside the colour loops when lamp_off.py asks us to stop"""


class StopSignal:
    """Watches for the stop file, checking the disk at most twice a second"""

    def __init__(self, path):
        self.path = path
        self._next_check = 0.0

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def check(self):
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + 0.5
        if self.path.exists():
            raise StopRequested


def pause(seconds, stop):
    """time.sleep() that still notices a stop request"""
    end = time.monotonic() + seconds
    while True:
        stop.check()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


def parse_colour(text):
    """Accept a preset name or a hex colour like ff8800 / #ff8800"""
    text = text.lower()
    if text in PRESETS:
        return PRESETS[text]
    hex_part = text.lstrip("#")
    if len(hex_part) == 6:
        try:
            return tuple(int(hex_part[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"'{text}' is not a preset ({', '.join(PRESETS)}) or a hex colour like ff8800")


def scale(colour, brightness):
    return tuple(round(c * brightness / 100) for c in colour)


def blend(a, b, t):
    """Mix colour a into colour b; t goes from 0.0 (all a) to 1.0 (all b)"""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gamma_encode(colour, gamma):
    """Turn a perceptual colour into the LED values that look like it"""
    return tuple(round(255 * (c / 255) ** gamma) for c in colour)


class Lamp:
    """Sends colours to the pad, skipping repeats so the USB link isn't flooded"""

    def __init__(self, gateway, brightness, stop, gamma=GAMMA):
        self.gateway = gateway
        self.brightness = brightness
        self.stop = stop
        self.gamma = gamma
        self.current = None

    def reconnected(self, gateway):
        """Use a fresh Gateway after the pad came back; the next colour is always resent"""
        self.gateway = gateway
        self.current = None

    def show(self, colour):
        self.stop.check()
        colour = gamma_encode(scale(colour, self.brightness), self.gamma)
        if colour != self.current:
            self.gateway.switch_pad(pad=0, colour=colour)
            self.current = colour


def fade(lamp, start, end, seconds):
    """Smoothly change from start to end over the given number of seconds"""
    begin = time.monotonic()
    while True:
        t = (time.monotonic() - begin) / seconds if seconds > 0 else 1.0
        if t >= 1.0:
            lamp.show(end)
            return
        lamp.show(blend(start, end, t))
        time.sleep(1 / UPDATES_PER_SECOND)


def run_single(lamp, colour):
    """Fade the colour in gently, then hold it"""
    fade(lamp, (0, 0, 0), colour, 1.5)
    while True:
        pause(3600, lamp.stop)


def run_sequence(lamp, colours, transition, hold):
    """Loop through the colours forever, fading from each one to the next"""
    lamp.show(colours[0])
    index = 0
    while True:
        pause(hold, lamp.stop)
        next_index = (index + 1) % len(colours)
        fade(lamp, colours[index], colours[next_index], transition)
        index = next_index


def oklab_hue(rgb, gamma):
    """Perceived hue angle (degrees) of a perceptual 0-1 RGB colour, via Oklab"""
    r, g, b = (c ** gamma for c in rgb)  # to linear light, as the LEDs will emit it
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (v ** (1 / 3) for v in (l, m, s))
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    b2 = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return math.degrees(math.atan2(b2, a))


class Rainbow:
    """
    The colour wheel, re-timed so the *perceived* hue changes at a constant rate.

    Walking HSV hue at a constant speed looks uneven: the eye sees almost no
    change near pure red, green and blue, then a sprint through orange, yellow
    and cyan. This maps an evenly advancing perceived hue (Oklab) back to the
    HSV hue that produces it.
    """

    STEPS = 720

    def __init__(self, gamma):
        self.hsv_hues = [i / self.STEPS for i in range(self.STEPS + 1)]
        unwrapped = [oklab_hue(colorsys.hsv_to_rgb(self.hsv_hues[0], 1.0, 1.0), gamma)]
        for h in self.hsv_hues[1:]:
            angle = oklab_hue(colorsys.hsv_to_rgb(h, 1.0, 1.0), gamma)
            step = (angle - unwrapped[-1] + 180) % 360 - 180  # shortest signed step
            unwrapped.append(unwrapped[-1] + max(step, 0.0))
        span = unwrapped[-1] - unwrapped[0]
        self.fractions = [(u - unwrapped[0]) / span for u in unwrapped]  # 0..1, increasing

    def colour(self, fraction):
        """Colour at a point 0-1 around the wheel, evenly spaced to the eye"""
        fraction %= 1.0
        i = bisect.bisect_right(self.fractions, fraction) - 1
        i = min(i, self.STEPS - 1)
        f0, f1 = self.fractions[i], self.fractions[i + 1]
        t = (fraction - f0) / (f1 - f0) if f1 > f0 else 0.0
        hue = self.hsv_hues[i] + (self.hsv_hues[i + 1] - self.hsv_hues[i]) * t
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        return (round(r * 255), round(g * 255), round(b * 255))


def run_rainbow(lamp, cycle_seconds):
    """Walk around the colour wheel, one full lap every cycle_seconds"""
    rainbow = Rainbow(lamp.gamma)
    begin = time.monotonic()
    while True:
        lamp.show(rainbow.colour((time.monotonic() - begin) / cycle_seconds))
        time.sleep(1 / UPDATES_PER_SECOND)


def connect(verbose, stop):
    """
    Open the pad, retrying until it is plugged in and answering.
    Logs the first failure (and any new kind of failure) rather than every attempt.
    """
    delay = RETRY_MIN_SECONDS
    last_error = None
    began = time.monotonic()
    while True:
        try:
            gateway = Gateway(verbose=verbose)
        except usb.core.NoBackendError:
            raise  # libusb itself is missing; retrying won't help
        except PAD_ERRORS as error:
            message = f"{type(error).__name__}: {error}"
            if message != last_error:
                log.warning("Toy pad not available (%s). Retrying until it appears.", message)
                last_error = message
            pause(delay, stop)
            delay = min(delay * 2, RETRY_MAX_SECONDS)
            continue
        log.info("Toy pad connected after %.1f s", time.monotonic() - began)
        return gateway


def setup_logging(log_file, verbose):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    if sys.stderr is not None:  # None when started with pythonw.exe
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)


def parse_args():
    parser = argparse.ArgumentParser(description="Lego Dimensions toy pad desk lamp")
    parser.add_argument("colours", nargs="*", type=parse_colour,
                        help="one or more preset names or hex colours (default: warm). "
                             "With more than one, the lamp fades between them in a loop.")
    parser.add_argument("-b", "--brightness", type=int, default=100,
                        help="brightness percentage, 1-100 (default: 100)")
    parser.add_argument("-t", "--transition", type=float,
                        help="seconds per fade (default 5), or per full lap with --rainbow (default 60)")
    parser.add_argument("--hold", type=float, default=0,
                        help="seconds to stay on each colour before fading to the next (default: 0)")
    parser.add_argument("--rainbow", action="store_true", help="slowly cycle through all colours")
    parser.add_argument("--gamma", type=float, default=GAMMA,
                        help=f"LED gamma correction (default {GAMMA}); 1 sends raw values")
    parser.add_argument("--list", action="store_true", help="list presets and exit")
    parser.add_argument("--log", action="store_true",
                        help=f"also write messages to {DEFAULT_LOG_FILE}. "
                             "Always on when there is no console, e.g. under pythonw.exe.")
    parser.add_argument("--log-file", type=Path, metavar="FILE",
                        help="write the log to this file instead of the default one")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every USB packet")
    args = parser.parse_args()

    if not 1 <= args.brightness <= 100:
        parser.error("brightness must be between 1 and 100")
    if args.transition is not None and args.transition <= 0:
        parser.error("transition must be more than 0 seconds")
    if args.hold < 0:
        parser.error("hold can't be negative")
    if args.gamma <= 0:
        parser.error("gamma must be more than 0")
    if args.log_file is None and (args.log or sys.stderr is None):
        args.log_file = DEFAULT_LOG_FILE
    return args


def run(args):
    stop = StopSignal(STOP_FILE)
    stop.clear()  # a stop file left over from a crash must not stop us straight away
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    colours = args.colours or [PRESETS["warm"]]
    if args.rainbow:
        mode = f"rainbow, {args.transition or 60:g} s per lap"
    elif len(colours) > 1:
        mode = f"fading between {len(colours)} colours"
    else:
        mode = "#%02x%02x%02x" % colours[0]
    log.info("Lamp starting (%s, brightness %d%%). Stop with Ctrl+C or lamp_off.py.", mode, args.brightness)

    gateway = None
    try:
        gateway = connect(args.verbose, stop)
        lamp = Lamp(gateway, args.brightness, stop, args.gamma)
        log.info("Lamp on")
        while True:
            try:
                if args.rainbow:
                    run_rainbow(lamp, args.transition or 60)
                elif len(colours) > 1:
                    run_sequence(lamp, colours, args.transition or 5, args.hold)
                else:
                    run_single(lamp, colours[0])
            except usb.core.USBError as error:
                # The pad was unplugged (or USB hiccuped) mid-run. Drop the dead
                # handle and wait for it to come back.
                log.warning("Lost contact with the toy pad (%s). Waiting for it to return.", error)
                gateway.close()
                gateway = None
                gateway = connect(args.verbose, stop)
                lamp.reconnected(gateway)
                log.info("Lamp back on")
    except KeyboardInterrupt:
        log.info("Ctrl+C: lamp off")
    except StopRequested:
        log.info("Stop requested: lamp off")
    finally:
        if gateway is not None:
            try:
                gateway.blank_pads()
            except usb.core.USBError as error:
                log.warning("Could not turn the pads off (%s)", error)
            gateway.close()
        stop.clear()
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


def main():
    args = parse_args()
    if args.list:
        for name, rgb in PRESETS.items():
            print(f"{name:10} #{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")
        return
    setup_logging(args.log_file, args.verbose)
    try:
        run(args)
    except usb.core.NoBackendError:
        log.critical("libusb was not found. Install the libusb-package wheel from deps/ "
                     "(see PROJECT_NOTES.md).")
        sys.exit(1)
    except Exception:
        # Under pythonw.exe an uncaught exception would vanish silently.
        log.exception("Lamp crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
