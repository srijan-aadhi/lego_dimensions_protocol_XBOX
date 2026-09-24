"""
Turn off a desk lamp that is running in another process, for example the one
Task Scheduler starts hidden at log-on. It asks the running desk_lamp.py to
blank the pads and exit, which lamp_off.py cannot do itself because only one
program can hold the pad's USB connection at a time.

    python lamp_off.py

To turn the lamp back on, start the "Lego Dimensions Desk Lamp" task from
Task Scheduler, or run desk_lamp.py by hand.
"""
import sys
import time

from desk_lamp import PID_FILE, STOP_FILE

WAIT_SECONDS = 10


def main():
    # The running lamp checks for the stop file twice a second and deletes it
    # as it exits, so the file disappearing is the acknowledgment.
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.touch()
    print("Asked the lamp to turn off", end="", flush=True)

    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if not STOP_FILE.exists():
            print("\nLamp off")
            return 0
        time.sleep(0.25)
        print(".", end="", flush=True)

    # Nobody picked it up. Remove it so it can't stop the next lamp that starts.
    try:
        STOP_FILE.unlink()
    except FileNotFoundError:
        print("\nLamp off")
        return 0

    if PID_FILE.exists():
        pid = PID_FILE.read_text().strip()
        print(f"\nA lamp (PID {pid}) was recorded but did not answer within {WAIT_SECONDS} s.\n"
              f"It may have crashed, or be stuck on USB. Check the log next to the PID file,\n"
              f"or end the process by hand: taskkill /PID {pid} /F")
    else:
        print("\nNo lamp seems to be running. Nothing to do.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
