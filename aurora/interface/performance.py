"""
Performance mode: lighter graphics while the PC is busy.

The Command Centre's animations (the AI core, the voice waveform, page
fades) are cheap, but on a 4 GB laptop every bit of CPU matters while the
model is answering. Performance mode keeps the same layout and makes the
graphics cheaper:

    the AI core       still while idle; busy states at 5 frames a second
                      instead of 15
    voice waveform    redrawn at most 5 times a second
    page changes      no fade

The Settings page chooses:

    auto   (default) on while the CPU or RAM stays high, off once it has
           calmed down. "High" must last three samples (6 seconds) and
           "calm" three more, so it doesn't flicker on and off.
    on     always
    off    never
"""

from collections import deque

MODES = ("auto", "on", "off")

HIGH_CPU = 85.0          # percent of all cores
HIGH_RAM = 92.0          # percent of physical memory in use (a 4 GB PC often sits at 85-90%)
CALM_CPU = 65.0
CALM_RAM = 88.0
SAMPLES = 3              # the sampler runs every 2 seconds


class Pressure:
    """Says whether the PC is under pressure, from CPU and RAM samples, with hysteresis."""

    def __init__(self, samples: int = SAMPLES):
        self.recent: deque[tuple[float | None, float | None]] = deque(maxlen=samples)
        self.under_pressure = False
        self.reason = ""

    def update(self, cpu: float | None, ram: float | None) -> bool:
        self.recent.append((cpu, ram))
        if len(self.recent) < self.recent.maxlen:
            return self.under_pressure
        cpus = [c for c, _ in self.recent]
        rams = [r for _, r in self.recent]
        busy_cpu = all(c is not None and c >= HIGH_CPU for c in cpus)
        busy_ram = all(r is not None and r >= HIGH_RAM for r in rams)
        if not self.under_pressure and (busy_cpu or busy_ram):
            self.under_pressure = True
        elif self.under_pressure and all((c or 0) < CALM_CPU for c in cpus) and \
                all((r or 0) < CALM_RAM for r in rams):
            self.under_pressure = False
        if self.under_pressure:
            cpu_now, ram_now = self.recent[-1]
            parts = []
            if cpu_now is not None and cpu_now >= CALM_CPU:
                parts.append(f"CPU {cpu_now:.0f}%")
            if ram_now is not None and ram_now >= CALM_RAM:
                parts.append(f"RAM {ram_now:.0f}%")
            self.reason = ", ".join(parts) or "the PC was busy"
        else:
            self.reason = ""
        return self.under_pressure


def reduced(mode: str, pressure: Pressure) -> bool:
    """Whether graphics should be reduced now."""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return pressure.under_pressure


def describe(mode: str, pressure: Pressure) -> str:
    """One line for the Dashboard ("" when performance mode is off)."""
    if mode == "on":
        return "Performance mode on: lighter animations (Settings)"
    if mode == "auto" and pressure.under_pressure:
        return f"Performance mode: lighter animations while the PC is busy ({pressure.reason})"
    return ""
