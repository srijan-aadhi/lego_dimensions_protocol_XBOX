#-------------------------------------------------------------------------------
# Name:        library
# Purpose:     Python library to control Lego Dimensions gateway/portal peripheral
#              Xbox version is unsupported due to likely harware differences.
# Author:      User
#
# Created:     21/11/2015
# Copyright:   (c) User 2015
# Licence:     <your licence>
#-------------------------------------------------------------------------------
# Command to function mapping:
# EP Cmd  - func_name()   - Description
# 01 0xc0 - switch_pad()  - Immediately switch one or all pad(s) to a single value
# 01 0xc2 - fade_pad()    - Immediately change the colour of one or all pad(s), fade and flash available
# 01 0xc3 - flash_pad()   - set 1 or all pad(s) to a colour with variable flash rates
# 01 0xc8 - switch_pads() - Immediately switch pad(s) to set of colours
# 01 0xc6 - fade_pads()   - Fade pad(s) to value(s)
# 01 0xc7 - flash_pads()  - Flash all 3 pads with individual colours and rates, either change to new or return to old based on pulse count

import logging
import time

import usb.core
import usb.util

log = logging.getLogger(__name__)

VENDOR_ID = 0x0e6f           # Logic3/PDP (made lego dimensions portal hardware)
XBOX_ONE_PRODUCT_ID = 0x0141  # Xbox One portal; talks GIP instead of plain HID

# Standard wake/startup message: 55 0F B0 01 "(c) LEGO 2014" + checksum
WAKE_MESSAGE = [0x55, 0x0f, 0xb0, 0x01, 0x28, 0x63, 0x29, 0x20, 0x4c, 0x45, 0x47, 0x4f, 0x20, 0x32, 0x30, 0x31, 0x34, 0xf7, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

# GIP (Xbox Game Input Protocol) constants used by the Xbox One portal.
# Protocol details: https://github.com/Ellerbach/LegoDimensions/blob/main/XboxPortalProtocol.md
GIP_AUTHENTICATE = 0x06
GIP_LEGO_GATEWAY = 0x21
EP_OUT = 0x01
EP_IN = 0x81


def _get_backend():
    """
    Use the libusb DLL bundled by the 'libusb-package' pip package if installed.
    This saves Windows users from having to install libusb by hand.
    """
    try:
        import libusb_package
        return libusb_package.get_libusb1_backend()
    except ImportError:
        return None


class Gateway:
    """
    Represents a Lego Dimensions gateway/portal peripheral.
    Works with the PS3/PS4/Wii U portal and the Xbox One portal.
    """

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.reattach = False
        self.is_xbox_one = False
        self._gip_sequence = 0
        # Initialise USB connection to the device
        self.dev = self._init_usb()
        # Reset the state of the device to all pads off
        self.blank_pads()

    def _log(self, text):
        """Verbose diagnostics go through the logging module so they still reach a
        log file when the script runs without a console (pythonw.exe)."""
        if self.verbose:
            log.info(text)

    def _init_usb(self):
        """
        Connect to and initialise the portal
        """
        # find our device
        dev = usb.core.find(idVendor=VENDOR_ID, backend=_get_backend())

        # was it found?
        if dev is None:
            raise ValueError('Device not found')

        self.is_xbox_one = (dev.idProduct == XBOX_ONE_PRODUCT_ID)
        # Handshake milestones always log (not just in verbose mode) so a log file
        # from an unattended start-up shows which initialisation path ran.
        log.info(f"Found portal {dev.idVendor:04x}:{dev.idProduct:04x}"
                 f"{' (Xbox One)' if self.is_xbox_one else ''}")

        self.reattach = False
        try:
            if dev.is_kernel_driver_active(0):
                self.reattach = True
                dev.detach_kernel_driver(0)
        except NotImplementedError:
            # Kernel driver queries aren't supported on some platforms (e.g. Windows)
            pass

        # set the active configuration. With no arguments, the first
        # configuration will be the active one
        dev.set_configuration()
        self.dev = dev

        # Initialise portal
        self._log("Initialising portal")
        if self.is_xbox_one:
            self._log("Claiming USB interface")
            usb.util.claim_interface(dev, 0)
            self._init_xbox_one()
        else:
            dev.write(EP_OUT, WAKE_MESSAGE)  # Startup
        return dev

    # ---- Xbox One (GIP) transport -------------------------------------------

    def _next_gip_sequence(self):
        """GIP sequence counter: 1-255, wrapping and skipping zero"""
        self._gip_sequence = self._gip_sequence % 255 + 1
        return self._gip_sequence

    def _gip_packet(self, command, options, payload):
        """Build an unchunked GIP packet (payloads here are always < 128 bytes)"""
        return [command, options, self._next_gip_sequence(), len(payload)] + list(payload)

    def _read_packet(self, timeout_ms):
        """Read one packet from the portal, or None on timeout"""
        try:
            packet = list(self.dev.read(EP_IN, 64, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return None
        self._log("received: " + " ".join(f"{b:02X}" for b in packet))
        return packet

    def _wait_for_lego_response(self, timeout_s):
        """Wait for any wrapped LEGO frame from the portal"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            packet = self._read_packet(timeout_ms=200)
            if packet and packet[0] == GIP_LEGO_GATEWAY and len(packet) > 4 and packet[4] == 0x55:
                return packet
        return None

    def _drain(self, max_time_s=0.25):
        """
        Discard any acknowledgments/tag events waiting on the IN endpoint.
        Time-limited, because the portal may send packets continuously.
        """
        deadline = time.monotonic() + max_time_s
        while time.monotonic() < deadline:
            if self._read_packet(timeout_ms=20) is None:
                break

    def _init_xbox_one(self):
        """
        The Xbox One portal only accepts LEGO commands once it believes it
        has been authenticated by a console. Send the wake message first:
        a portal that is already unlocked answers straight away. If it
        doesn't, tell it authentication is complete and wait for the answer.
        """
        self._drain()
        self._log("Sending wake message")
        self.dev.write(EP_OUT, self._gip_packet(GIP_LEGO_GATEWAY, 0x00, WAKE_MESSAGE), timeout=1000)
        if self._wait_for_lego_response(timeout_s=1.0):
            log.info("Xbox One portal was already unlocked")
            return

        log.info("Unlocking Xbox One portal (sending GIP authenticate)")
        self.dev.write(EP_OUT, self._gip_packet(GIP_AUTHENTICATE, 0x20, [0x01, 0x00]), timeout=1000)
        # The portal normally answers the wake it already queued; if not, send it again
        if not self._wait_for_lego_response(timeout_s=2.0):
            self.dev.write(EP_OUT, self._gip_packet(GIP_LEGO_GATEWAY, 0x00, WAKE_MESSAGE), timeout=1000)
            if not self._wait_for_lego_response(timeout_s=2.0):
                raise RuntimeError("Xbox One portal did not respond to initialisation. "
                                   "Try unplugging it and plugging it back in.")
        log.info("Xbox One portal ready")

    def generate_checksum_for_command(self, command):
        """
        Given a command (without checksum or trailing zeroes),
        generate a checksum for it.
        """
        assert len(command) <= 31  # One byte must be left for the checksum
        # Add bytes, overflowing at 256
        return sum(command) % 256

    def close(self):
        """
        Release the device and reattach the kernel driver if we detached it
        """
        dev = getattr(self, "dev", None)
        if dev is None:
            return
        self.dev = None
        try:
            usb.util.dispose_resources(dev)
            if self.reattach:
                dev.attach_kernel_driver(0)
        except usb.core.USBError:
            pass

    def __del__(self):
        self.close()

    def pad_message(self, message):
        """Pad a message to 32 bytes"""
        assert len(message) <= 32  # Messages cannot be longer than 32 bytes
        return message + [0x00] * (32 - len(message))

    def convert_command_to_packet(self, command):
        """Take a command and add a checksum and padding"""
        assert len(command) <= 31  # One byte must be left for the checksum
        checksum = self.generate_checksum_for_command(command)
        message = command + [checksum]
        return self.pad_message(message)

    def send_command(self, command):
        """Take the command, add checksum and padding, then send it"""
        assert len(command) <= 31  # One byte must be left for the checksum
        packet = self.convert_command_to_packet(command)
        if self.is_xbox_one:
            # Xbox One portal: same 32-byte message, carried inside GIP command 0x21
            packet = self._gip_packet(GIP_LEGO_GATEWAY, 0x00, packet)
        self._log(f"packet: {packet!r}")
        self.dev.write(EP_OUT, packet, timeout=1000)
        if self.is_xbox_one:
            # Read back the portal's acknowledgment so its queue doesn't back up.
            # Kept short so smooth colour animations can send many updates per second.
            self._drain(max_time_s=0.05)

    def blank_pads(self):
        """
        Clear the pads to all off.
        """
        self.switch_pad(
            pad=0,  # All pads
            colour=(0, 0, 0),  # RGB
        )

    def switch_pad(self, pad, colour):
        """
        Change the colour of one or all pad(s) immediately
        Pad numbering: 0:All, 1:Center, 2:Left, 3:Right
        Colour values are 0-255, with 0 being off and 255 being maximum
        Colour should be a tuple of 0-255 values in the format (red, green, blue)
        Abstraction for command: 0xc0
        """
        red, green, blue = colour[0], colour[1], colour[2]
        command = [0x55, 0x06, 0xc0, 0x02, pad, red, green, blue]
        self.send_command(command)

    def flash_pad(self, pad, on_length, off_length, pulse_count, colour):
        """
        Flash one or all pad(s) a given colour
        The pad(s) will either revert to old colour or stay on the new one depending on the pulse_count value
        Odd: keep new colour, Even: keep previous colour. Exception: 0x00 keeps new colour.
        Pulse counts from 0xff will flash forever.
        Pad numbering: 0:All, 1:Center, 2:Left, 3:Right
        Colour values are 0-255, with 0 being off and 255 being maximum
        Colour should be a tuple of 0-255 values in the format (red, green, blue)
        Abstraction for command: 0xc3
        """
        red, green, blue = colour[0], colour[1], colour[2]
        command = [0x55, 0x09, 0xc3, 0x1f, pad, on_length, off_length, pulse_count, red, green, blue]
        self.send_command(command)

    def fade_pad(self, pad, pulse_time, pulse_count, colour):
        """
        Fade one or all pad(s) a given colour with optional pulsing effect
        The pad(s) will either revert to old colour or stay on the new one depending on the pulse_count value
        Odd: keep new colour, Even: keep previous colour. Exception: 0x00 keeps new colour.
        pulse_count values of 0x00 and above 0x199 will flash forever.
        pulse_time starts fast at 0x01 and continues to 0xff which is very slow, 0x00 causes immediate change.
        Pad numbering: 0:All, 1:Center, 2:Left, 3:Right
        Colour values are 0-255, with 0 being off and 255 being maximum
        Colour should be a tuple of 0-255 values in the format (red, green, blue)
        Abstraction for command: 0xc2
        """
        red, green, blue = colour[0], colour[1], colour[2]
        command = [0x55, 0x08, 0xc2, 0x0f, pad, pulse_time, pulse_count, red, green, blue]
        self.send_command(command)

    def switch_pads(self, *colours):
        """
        Requires 3 tuples:
        (Center),(Left),(Right)
        Each using the format:
        (R, G, B)
        Empty colour tuples will ignore that pad.
        Ignored pads will continue whatever they were doing previously.
        Abstraction for command: 0xc8
        """
        assert len(colours) == 3
        command = [0x55, 0x0e, 0xc8, 0x06]  # Start of command
        for colour in colours:
            if len(colour) != 3:
                # Disable command for this pad
                enable = 0
                red, green, blue = 0, 0, 0
            else:
                # Send colour values for this pad
                enable = 1
                red, green, blue = colour[0], colour[1], colour[2]
            command += [enable, red, green, blue]  # 3 identical segments, one for each colour
        self.send_command(command)

    def fade_pads(self, *pads):  # TODO get second opinion on arguments
        """
        Fade pad(s) to value(s)
        Each pad is represented by a tuple in the format:
        (fade_time, pulse_count, (R,G,B) )
        Colour values must be from 0-255 (0x00-0xff)
        Empty colour tuples will ignore that pad.
        TODO investigate time values
        TODO investigate count values
        Abstraction for command: 0xc6
        """
        assert len(pads) == 3
        command = [0x55, 0x14, 0xc6, 0x26]
        for pad in pads:
            if len(pad) != 3 or len(pad[2]) != 3:
                # Disable command for this pad
                enable = 0
                fade_time = 0
                pulse_count = 0
                red, green, blue = 0, 0, 0
            else:
                # Enable pad for the command
                enable = 1
                colour = pad[2]
                red, green, blue = colour[0], colour[1], colour[2]
                fade_time = pad[0]
                pulse_count = pad[1]
            command += [enable, fade_time, pulse_count, red, green, blue]
        self.send_command(command)

    def flash_pads(self, *pads):  # TODO get second opinion on arguments
        """
        Flash all 3 pads with individual colours and rates, either change to new or return to old based on pulse count.
        Each pad is represented by a tuple in the format:
        (on_length, off_length, pulse_count, (R,G,B) )
        Colour values must be from 0-255 (0x00-0xff)
        Empty colour tuples will ignore that pad.
        Ignored pads will continue whatever they were doing previously.
        On pulse length - 0x00 is almost impersceptible, 0xff is ~10 seconds
        Off pulse length - 0x00 is almost impersceptible, 0xff is ~10 seconds
        Number of flashes - odd value leaves pad in new colour, even leaves pad in old, except for 0x00, which changes to new. Values above 0xc6 dont stop.
        Abstraction for command: 0xc7
        """
        assert len(pads) == 3
        command = [0x55, 0x17, 0xc7, 0x3e]
        for pad in pads:
            if len(pad) != 4 or len(pad[3]) != 3:
                # Disable command for this pad
                enable = 0
                on_length = 0
                off_length = 0
                pulse_count = 0
                red, green, blue = 0, 0, 0
            else:
                # Enable pad for the command
                enable = 1
                colour = pad[3]
                red, green, blue = colour[0], colour[1], colour[2]
                on_length = pad[0]
                off_length = pad[1]
                pulse_count = pad[2]
            command += [enable, on_length, off_length, pulse_count, red, green, blue]
        self.send_command(command)


def demo_switch_pads_skip(gateway):
    """
    Show how the previous effect on a pad is preverved with gateway.switch_pads()
    """
    print("Demonstrating ignore pad functionality in gateway.switch_pads()")
    # Test flash_pad()
    gateway.flash_pad(
        pad=0,
        on_length=10,
        off_length=20,
        pulse_count=100,
        colour=(255, 0, 0),  # RGB
    )
    time.sleep(2)
    # test switch_pads()
    gateway.switch_pads(
        (255, 0, 0),  # C:RGB
        (0, 255, 0),  # L:RGB
        (),           # R:skip
    )


def test_flash_pads(gateway):
    # test flash_pads()
    gateway.flash_pads(  # 3 changing pads
        (5, 10, 15, (255, 0, 0)),   # (on_length, off_length, pulse_count, (R,G,B) )
        (20, 25, 30, (0, 255, 0)),  # (on_length, off_length, pulse_count, (R,G,B) )
        (35, 40, 45, (0, 0, 255)),  # (on_length, off_length, pulse_count, (R,G,B) )
    )
    pause_between_tests(gateway)
    gateway.flash_pads(  # Two ignored pads
        (5, 10, 15, ()),              # On, off, count, (R,G,B)
        (),                           # On, off, count, (R,G,B)
        (5, 40, 10, (255, 0, 255)),   # On, off, count, (R,G,B)
    )


def test_fade_pads(gateway):
    # test fade_pads()
    gateway.fade_pads(  # 3 changing pads
        (10, 20, (255, 0, 0)),  # (fade_time, pulse_count, (R,G,B) )
        (20, 10, (0, 255, 0)),  # (fade_time, pulse_count, (R,G,B) )
        (15, 15, (0, 0, 255)),  # (fade_time, pulse_count, (R,G,B) )
    )
    pause_between_tests(gateway)
    gateway.fade_pads(  # Two ignored pads
        (),                     # (fade_time, pulse_count, (R,G,B) )
        (20, 10, ()),           # (fade_time, pulse_count, (R,G,B) )
        (15, 15, (0, 0, 255)),  # (fade_time, pulse_count, (R,G,B) )
    )


def pause_between_tests(gateway):
    time.sleep(10)
    gateway.blank_pads()
    time.sleep(1)


def debug():
    """
    For testing and debugging and coding and stuff
    """
    # Get gateway object
    gateway = Gateway(verbose=True)

    # Test functions for library
    #test_flash_pads(gateway)
    #pause_between_tests(gateway)
    test_fade_pads(gateway)
    return

    # Unreachable: kept from the original for quick toggling during debugging
    # Test switch_pad()
    gateway.switch_pad(
        pad=0,
        colour=(0, 255, 0),  # RGB
    )
    pause_between_tests(gateway)

    # Test flash_pad()
    gateway.flash_pad(
        pad=0,
        on_length=10,
        off_length=20,
        pulse_count=100,
        colour=(255, 0, 0),  # RGB
    )
    pause_between_tests(gateway)

    # Test fade_pad()
    gateway.fade_pad(
        pad=1,
        pulse_time=10,
        pulse_count=10,
        colour=(255, 0, 255),  # RGB
    )
    pause_between_tests(gateway)

    # test switch_pads()
    gateway.switch_pads(
        (255, 0, 0),  # C:RGB
        (0, 255, 0),  # L:RGB
        (),           # R:skip
    )
    pause_between_tests(gateway)

    demo_switch_pads_skip(gateway)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    debug()


if __name__ == '__main__':
    main()
