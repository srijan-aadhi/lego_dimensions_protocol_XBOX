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

# Hue names for --dwell, in HSV degrees.
HUE_NAMES = {"red": 0, "orange": 30, "yellow": 60, "lime": 90, "green": 120, "teal": 150,
             "cyan": 180, "azure": 210, "blue": 240, "violet": 270, "magenta": 300, "rose": 330}
# Dwell per colour band, fitted so that a lap spends the seconds on each band
# that were tuned by eye (see PROJECT_NOTES.md). --dwell overrides one at a time.
DEFAULT_HOLDS = {
    "red": 3.61,
    "orange": 0.89,
    "yellow": 1.15,
    "lime": 0.5,
    "green": 0.76,
    "teal": 1.78,
    "cyan": 1.52,
    "azure": 0.38,
    "blue": 0.8,
    "violet": 0.7,
    "magenta": 0.54,
    "rose": 1.07,
}

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


def parse_hold(text):
    """Parse COLOUR=X (a hue name or degrees, and a slow-down factor) for --dwell"""
    try:
        where, factor = text.split("=", 1)
        where = where.strip().lower()
        hue = HUE_NAMES[where] if where in HUE_NAMES else float(where) % 360
        factor = float(factor)
    except (ValueError, KeyError):
        raise argparse.ArgumentTypeError(f"'{text}' should look like red=5 or 150=2")
    if factor <= 0:
        raise argparse.ArgumentTypeError("dwell factor must be more than 0")
    return hue, factor


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

    def __init__(self, gateway, brightness, stop, gamma=GAMMA,
                 cool_speed=1.0, cool_dim=1.0, yellow_boost=0.0, holds=None):
        self.gateway = gateway
        self.brightness = brightness
        self.stop = stop
        self.gamma = gamma
        self.cool_speed = cool_speed
        self.cool_dim = cool_dim
        self.yellow_boost = yellow_boost
        self.holds = holds or {}
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


def oklab(rgb, gamma):
    """Perceptual position (L, a, b) of a 0-1 RGB colour, via Oklab"""
    r, g, b = (c ** gamma for c in rgb)  # to linear light, as the LEDs will emit it
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (v ** (1 / 3) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


class Rainbow:
    """
    The colour wheel, re-timed so the colour *looks* like it changes at a
    constant rate.

    Walking HSV hue at a constant speed looks uneven: the eye sees almost no
    change near pure red, green and blue, then a sprint through orange, yellow
    and cyan. This measures the perceived distance (Oklab) between neighbouring
    colours on the wheel and spends time in proportion to it. Distance, not
    hue angle: near pure green the hue angle is flat while the colour still
    changes, and around blue it briefly runs backwards, so pacing by angle
    alone produced visible jumps there.
    """

    STEPS = 720

    def __init__(self, gamma, cool_speed=1.0, cool_dim=1.0, yellow_boost=0.0, holds=None):
        """
        cool_speed: how many times faster to move through green-cyan-blue-purple.
            The eye has one word ("blue") for a stretch that is as wide as
            red+orange+yellow, so an evenly paced wheel feels slow there.
        cool_dim: brightness multiplier for lime-green-cyan (1 = none). These are
            nearly as light as yellow; dimming them makes yellow the visible peak.
        yellow_boost: blue mixed into yellow (0-1). Whiter yellow reads brighter.
        holds: {hue_degrees: factor}. Move `factor` times slower around that hue
            (raised-cosine window, half-width 30 degrees).
        """
        self.gamma = gamma
        self.cool_dim = cool_dim
        self.yellow_boost = yellow_boost
        self.hsv_hues = [i / self.STEPS for i in range(self.STEPS + 1)]
        previous = oklab(self.rim(self.hsv_hues[0]), gamma)
        cumulative = [0.0]
        for h in self.hsv_hues[1:]:
            point = oklab(self.rim(h), gamma)
            distance = math.dist(point, previous)
            previous = point
            # Speeding up = each perceived step counts for less of the lap.
            weight = 1.0 - (1.0 - 1.0 / cool_speed) * self.bump(h * 360, 195, 105)
            for hue_deg, factor in (holds or {}).items():
                weight *= 1.0 + (factor - 1.0) * self.bump(h * 360, hue_deg, 30)
            cumulative.append(cumulative[-1] + distance * weight)
        self.fractions = [c / cumulative[-1] for c in cumulative]  # 0..1, increasing

    @staticmethod
    def bump(hue_deg, centre, half_width):
        """Raised-cosine window: 1 at centre, fading smoothly to 0 at +/- half_width"""
        d = abs((hue_deg - centre + 180) % 360 - 180)
        return 0.5 * (1 + math.cos(math.pi * d / half_width)) if d < half_width else 0.0

    def rim(self, hue):
        """The (perceptual, 0-1) colour shown for an HSV hue, after dimming and boosting"""
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        deg = hue * 360
        dim = 1.0 - (1.0 - self.cool_dim) * self.bump(deg, 150, 75)   # lime..green..cyan..azure
        b = min(1.0, b + self.yellow_boost * self.bump(deg, 60, 30))  # whiten around yellow
        return (r * dim, g * dim, b * dim)

    def colour(self, fraction):
        """Colour at a point 0-1 around the wheel, evenly spaced to the eye"""
        fraction %= 1.0
        i = bisect.bisect_right(self.fractions, fraction) - 1
        i = min(i, self.STEPS - 1)
        f0, f1 = self.fractions[i], self.fractions[i + 1]
        t = (fraction - f0) / (f1 - f0) if f1 > f0 else 0.0
        hue = self.hsv_hues[i] + (self.hsv_hues[i + 1] - self.hsv_hues[i]) * t
        return tuple(round(c * 255) for c in self.rim(hue))


def run_rainbow(lamp, cycle_seconds):
    """Walk around the colour wheel, one full lap every cycle_seconds"""
    rainbow = Rainbow(lamp.gamma, lamp.cool_speed, lamp.cool_dim, lamp.yellow_boost, lamp.holds)
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
    parser.add_argument("--cool-speed", type=float, default=1.3, metavar="X",
                        help="rainbow: move X times faster through green-cyan-blue-purple (default 1.3; 1 = even)")
    parser.add_argument("--cool-dim", type=float, default=0.8, metavar="F",
                        help="rainbow: brightness of lime-green-cyan, 0-1 (default 0.8), so yellow stands out")
    parser.add_argument("--yellow-boost", type=float, default=0.2, metavar="F",
                        help="rainbow: mix this much blue into yellow, 0-1 (default 0.2), for a whiter, brighter yellow")
    parser.add_argument("--gamma", type=float, default=GAMMA,
                        help=f"LED gamma correction (default {GAMMA}); 1 sends raw values")
    parser.add_argument("--even", action="store_true",
                        help="rainbow: constant perceived speed all the way round (ignores --cool-speed and all dwells)")
    parser.add_argument("--dwell", action="append", type=parse_hold, default=[], metavar="COLOUR=X",
                        help="rainbow: linger X times longer around a hue, e.g. red=5 or 150=2. "
                             f"Colours: {', '.join(HUE_NAMES)}. Repeatable; overrides the defaults "
                             f"({', '.join(f'{k}={v:g}' for k, v in DEFAULT_HOLDS.items())}); 1 = even")
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
    if args.cool_speed <= 0:
        parser.error("cool-speed must be more than 0")
    if args.even:
        args.cool_speed = 1.0
        args.holds = {}
    else:
        holds = {HUE_NAMES[name]: factor for name, factor in DEFAULT_HOLDS.items()}
        holds.update(dict(args.dwell))
        args.holds = holds
    if not 0 <= args.cool_dim <= 1 or not 0 <= args.yellow_boost <= 1:
        parser.error("cool-dim and yellow-boost must be between 0 and 1")
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
        lamp = Lamp(gateway, args.brightness, stop, args.gamma,
                    args.cool_speed, args.cool_dim, args.yellow_boost, args.holds)
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
