"""
run_loop.py
-----------
Stays running during the trading day and calls collect_snapshot.py on a fixed
clock, saving each snapshot to the GitHub repo right away.

Why: GitHub often delays or skips scheduled runs when its servers are busy,
so asking it to start a new run every 15 minutes leaves big gaps. One run that
stays on and keeps its own clock is far more reliable.

Snapshot times (Eastern):
  every 15 minutes from 9:35am to 2:50pm
  every 5 minutes from 3:00pm to 3:55pm, plus 3:58pm

GitHub limits a single run to 6 hours, so the workflow starts one run in the
morning and a fresh one around midday that takes over (see collect.yml).
"""

import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MAX_MINUTES = 345   # stop before GitHub's 6-hour limit


def todays_slots(day):
    """All snapshot times for a given date."""
    at = lambda h, m: datetime(day.year, day.month, day.day, h, m, tzinfo=ET)
    slots, t = [], at(9, 35)
    while t < at(15, 0):
        slots.append(t)
        t += timedelta(minutes=15)
    t = at(15, 0)
    while t <= at(15, 55):
        slots.append(t)
        t += timedelta(minutes=5)
    slots.append(at(15, 58))
    return slots


def remaining_slots(now):
    """Snapshot times still ahead (a slot up to 2 minutes old still counts)."""
    return [s for s in todays_slots(now.date()) if s >= now - timedelta(minutes=2)]


def git(*args):
    return subprocess.run(["git", *args])


def save_to_repo():
    git("add", "data")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return  # nothing new
    git("commit", "-q", "-m", f"snapshot {datetime.now(ET):%Y-%m-%d %H:%M} ET")
    for _ in range(3):
        git("pull", "-q", "--rebase")
        if git("push", "-q").returncode == 0:
            return
        time.sleep(5)
    print("  warning: push failed; the data will be pushed with the next snapshot")


def main():
    started = time.monotonic()
    slots = remaining_slots(datetime.now(ET))
    if not slots:
        print("No snapshot times left today; nothing to do.")
        return
    print(f"{len(slots)} snapshots planned, from {slots[0]:%H:%M} to {slots[-1]:%H:%M} ET")

    for slot in slots:
        wait = (slot - datetime.now(ET)).total_seconds()
        if (time.monotonic() - started + max(wait, 0)) / 60 > MAX_MINUTES:
            print("Reached the time limit for one run; the next run takes over.")
            break
        if wait > 0:
            time.sleep(wait)
        subprocess.run([sys.executable, "collect_snapshot.py"])
        save_to_repo()

    print("Done for this run.")


if __name__ == "__main__":
    main()
