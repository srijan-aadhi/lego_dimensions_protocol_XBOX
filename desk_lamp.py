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

The lamp stays on while the script runs. Press Ctrl+C to turn it off.
"""
import argparse
import colorsys
import sys
import time

from lego_dimensions_gateway import Gateway

# RGB LEDs look bluish at (255, 255, 255), so "white" presets are tuned warmer.
PRESETS = {
    "warm": (255, 130, 40),       # cosy, incandescent-bulb feel
    "soft": (255, 170, 90),       # neutral-warm
    "daylight": (255, 220, 180),  # the brightest, most "white" option
    "cool": (200, 220, 255),
    "reading": (255, 190, 120),
    "night": (120, 10, 0),        # dim red, easy on the eyes after dark
    "focus": (150, 200, 255),
    "red": (255, 0, 0),
    "orange": (255, 80, 0),
    "yellow": (255, 180, 0),
    "green": (0, 255, 0),
    "teal": (0, 200, 150),
    "blue": (0, 0, 255),
    "purple": (140, 0, 255),
    "pink": (255, 40, 120),
}

UPDATES_PER_SECOND = 20  # how often colours are sent during a fade


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


class Lamp:
    """Sends colours to the pad, skipping repeats so the USB link isn't flooded"""

    def __init__(self, gateway, brightness):
        self.gateway = gateway
        self.brightness = brightness
        self.current = None

    def show(self, colour):
        colour = scale(colour, self.brightness)
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


def run_sequence(lamp, colours, transition, hold):
    """Loop through the colours forever, fading from each one to the next"""
    lamp.show(colours[0])
    index = 0
    while True:
        time.sleep(hold)
        next_index = (index + 1) % len(colours)
        fade(lamp, colours[index], colours[next_index], transition)
        index = next_index


def run_rainbow(lamp, cycle_seconds):
    """Walk around the colour wheel, one full lap every cycle_seconds"""
    begin = time.monotonic()
    while True:
        hue = ((time.monotonic() - begin) / cycle_seconds) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        lamp.show((round(r * 255), round(g * 255), round(b * 255)))
        time.sleep(1 / UPDATES_PER_SECOND)


def main():
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
    parser.add_argument("--list", action="store_true", help="list presets and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="print USB packets")
    args = parser.parse_args()

    if args.list:
        for name, rgb in PRESETS.items():
            print(f"{name:10} #{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")
        return

    if not 1 <= args.brightness <= 100:
        parser.error("brightness must be between 1 and 100")
    if args.transition is not None and args.transition <= 0:
        parser.error("transition must be more than 0 seconds")
    if args.hold < 0:
        parser.error("hold can't be negative")

    colours = args.colours or [PRESETS["warm"]]

    try:
        gateway = Gateway(verbose=args.verbose)
    except ValueError:
        sys.exit("Toy pad not found. Is it plugged in, and is the WinUSB/libusb driver installed?")

    lamp = Lamp(gateway, args.brightness)
    print("Lamp on. Press Ctrl+C to turn it off.")
    try:
        if args.rainbow:
            run_rainbow(lamp, args.transition or 60)
        elif len(colours) > 1:
            run_sequence(lamp, colours, args.transition or 5, args.hold)
        else:
            fade(lamp, (0, 0, 0), colours[0], 1.5)  # gentle switch-on
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        gateway.blank_pads()
        print("\nLamp off")
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
