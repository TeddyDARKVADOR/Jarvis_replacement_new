"""
eval_lab/world.py — the simulated world a trial runs in.

THE CLOCK IS THE WHOLE PROBLEM
    Most of what is interesting about JARVIS happens at a time boundary: a
    device goes stale after 90 s, a turn's origin after 180 s, a phone report
    after 300 s, a cooldown after 900 s, sleep needs 30 min of idle. Testing
    any of that against the wall clock means either sleeping or not testing it.

    context/ takes `now` as a parameter everywhere it matters, so it needs no
    help. server/devices.py, server/device_api.py and core/confirm.py read
    `time.monotonic()` directly, and context/store.py reads `time.time()` and
    `datetime.now()` when called the way production calls it. None of them is
    edited for the lab. Instead `SimClock.install()` replaces the `time` /
    `datetime` NAME inside those modules — not the `time` module itself, which
    the rest of the process, including the lab, keeps using normally.

WHY A TRIAL CANNOT LEAK INTO THE NEXT ONE
    Every trial builds its own registry, policy and queue. The few paths that
    go through module singletons (context.get_store(), server.notify.get_hub())
    are reset before and after, inside `isolated()`, and the clock patch is
    undone in a `finally`. The selftest runs the same scenario before and after
    a deliberately dirty one and demands identical traces.
"""
from __future__ import annotations

import contextlib
import importlib
from datetime import datetime, timedelta, timezone

from .scenario import DEFAULT_TIME

# Modules whose clock the simulation owns. Each entry: module, names to patch.
_CLOCK_TARGETS = (
    ("context.situation", ("time", "datetime")),
    ("context.store", ("time",)),
    ("context.policy", ("time",)),
    ("server.devices", ("time",)),
    ("server.device_api", ("time",)),
    ("server.notify", ("time",)),
    ("core.confirm", ("time",)),
)


def epoch_of(local: str) -> float:
    """A naive local ISO time -> seconds. Treated as UTC: only differences matter,
    and treating it as UTC keeps the result independent of the host's zone."""
    return datetime.fromisoformat(local).replace(tzinfo=timezone.utc).timestamp()


class SimClock:
    """Wall clock and monotonic clock, moved only by the scenario."""

    def __init__(self, local: str = DEFAULT_TIME, monotonic: float = 10_000.0):
        self.local = datetime.fromisoformat(local)
        self.epoch = epoch_of(local)
        self.mono = float(monotonic)

    def advance(self, seconds: float) -> None:
        self.local += timedelta(seconds=seconds)
        self.epoch += seconds
        self.mono += seconds

    def set_local(self, local: str) -> None:
        """Jump the wall clock to an absolute time. Time never runs backwards
        in a trial — a queue item would acquire a negative age — so a target
        in the past is refused."""
        delta = epoch_of(local) - self.epoch
        if delta < 0:
            from .scenario import Invalid
            raise Invalid([f"le temps ne recule pas : {local} < {self.local.isoformat()}"])
        self.advance(delta)

    def next_time_of_day(self, hhmm: str) -> None:
        """Move forward to the next occurrence of HH:MM (today or tomorrow)."""
        h, m = (int(x) for x in hhmm.split(":"))
        target = self.local.replace(hour=h, minute=m, second=0, microsecond=0)
        if target < self.local:
            target += timedelta(days=1)
        self.advance((target - self.local).total_seconds())

    # ── what the patched modules see ─────────────────────────────────────────

    def _time_namespace(self):
        clock = self

        class _Time:
            @staticmethod
            def time() -> float:
                return clock.epoch

            @staticmethod
            def monotonic() -> float:
                return clock.mono

            @staticmethod
            def sleep(_s):
                raise RuntimeError("sleep() inside a simulated trial: the clock is the scenario's")

            def __getattr__(self, name):
                import time as real
                return getattr(real, name)

        return _Time()

    def _datetime_class(self):
        clock = self

        class _Datetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.local if tz is None else clock.local.replace(tzinfo=tz)

        return _Datetime

    @contextlib.contextmanager
    def install(self):
        saved = []
        fake_time, fake_dt = self._time_namespace(), self._datetime_class()
        try:
            for mod_name, names in _CLOCK_TARGETS:
                try:
                    mod = importlib.import_module(mod_name)
                except Exception:
                    continue          # an optional module absent is not the lab's concern
                for name in names:
                    if hasattr(mod, name):
                        saved.append((mod, name, getattr(mod, name)))
                        setattr(mod, name, fake_time if name == "time" else fake_dt)
            yield self
        finally:
            for mod, name, value in reversed(saved):
                setattr(mod, name, value)


def _reset_singletons() -> None:
    for mod_name in ("context", "server.notify"):
        try:
            importlib.import_module(mod_name).reset()
        except Exception:
            pass


@contextlib.contextmanager
def isolated(clock: SimClock):
    """Fresh singletons, the scenario's clock, and everything put back after."""
    _reset_singletons()
    try:
        with clock.install():
            yield clock
    finally:
        _reset_singletons()


class SutCrash(Exception):
    """JARVIS's own code raised. Kept apart from every other exception so a
    broken simulator can never be reported as a bug in JARVIS."""

    def __init__(self, where: str, exc: BaseException):
        super().__init__(f"{where}: {type(exc).__name__}: {exc}")
        self.where = where
        self.original = exc


def sut(where: str, fn, *args, **kwargs):
    """Call into the system under test. The one door between lab and JARVIS."""
    try:
        return fn(*args, **kwargs)
    except SutCrash:
        raise
    except Exception as exc:        # noqa: BLE001 — classifying is the point
        raise SutCrash(where, exc) from exc
