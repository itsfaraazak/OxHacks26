from detector.presage import PresageReading, SessionPresage


class Bridge:
    def __init__(self, seconds):
        self.seconds = seconds
        self.closed = False
        self.frames = []
        self.status = "running"

    def close(self):
        self.closed = True

    def send(self, *frame):
        self.frames.append(frame)

    def reset(self, now):
        pass

    def read(self, now):
        return PresageReading(status=self.status)


def test_lifecycle_stops_idle_and_starts_each_new_active_period():
    created = []

    def factory(seconds):
        bridge = Bridge(seconds)
        created.append(bridge)
        return bridge

    owner = SessionPresage(25, factory)
    owner.reconcile(None)
    assert not created
    owner.reconcile("first")
    owner.send(1, 1, b"abc", 123)
    owner.reconcile("first")
    assert len(created) == 1
    assert created[0].frames
    owner.reconcile(None)  # pause/end/escape/backend offline
    assert created[0].closed
    assert "idle" in owner.read(0).status
    owner.reconcile("first")  # resume
    assert len(created) == 2
    owner.reconcile("second")  # session replacement without observed idle frame
    assert created[1].closed
    assert len(created) == 3
    owner.close()
    assert created[2].closed


def test_no_paid_retry_loop_after_failure_or_budget():
    calls = []

    def fails(seconds):
        calls.append(seconds)
        raise RuntimeError("unavailable")

    owner = SessionPresage(25, fails)
    for _ in range(10):
        owner.reconcile("first")
    assert calls == [25]
    owner.reconcile(None)
    owner.reconcile("second")
    assert calls == [25, 25]

    bridge = Bridge(25)
    owner = SessionPresage(25, lambda _: bridge)
    owner.reconcile("first")
    bridge.status = "stopped (budget or shutdown)"
    owner.reconcile("first")
    assert owner.read(0).status == bridge.status
    assert not bridge.closed
