"""A stand-in for src/host.py's run(): records every call, answers from a script.

Shared by the pytest suite (the `fake_host` fixture in conftest.py) and the Decky
unittest suite (decky/tests/test_backend.py), so both fake the host the same way.

    fh = FakeHost().fail("--device lightbar", out="connection timed out", times=1)
    fh.install(monkeypatch.setattr)        # host.run is now fh.run
    ...
    fh.argvs     # [[argv...], ...] in call order
    fh.calls     # Call(argv, timeout, as_deck, input) for each

Every call succeeds with (0, "") unless a rule matches. A rule matches on a
substring of the space-joined argv, or on pred(argv) for a callable; the first
matching rule with uses left answers.
"""

import collections

import host

Call = collections.namedtuple("Call", "argv timeout as_deck input")


class FakeHost:
    def __init__(self):
        self.calls = []
        self._rules = []

    def fail(self, match, rc=1, out="boom", *, timed_out=False, times=None):
        """Answer calls that match with (rc, out). `times` limits how many it answers."""
        self._rules.append([match, host.Result(rc, out, timed_out), times])
        return self

    def run(self, argv, timeout=30, *, as_deck=False, input=None):
        argv = list(argv)
        self.calls.append(Call(argv, timeout, as_deck, input))
        line = " ".join(argv)
        for rule in self._rules:
            match, result, times = rule
            hit = match(argv) if callable(match) else match in line
            if hit and times != 0:
                if times is not None:
                    rule[2] = times - 1
                return result
        return host.Result(0, "", False)

    def install(self, setattr):
        """setattr: monkeypatch.setattr, or any (obj, name, value) setter."""
        setattr(host, "run", self.run)
        return self

    @property
    def argvs(self):
        return [c.argv for c in self.calls]

    @property
    def lines(self):
        return [" ".join(c.argv) for c in self.calls]
