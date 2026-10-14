import time

from src.notifier.queue import pause_until, paused_until, resume


def test_pause_resume_cycle():
    resume()
    assert paused_until() == 0.0

    target = time.time() + 60
    pause_until(target)
    assert paused_until() == target
    assert paused_until() > time.time()

    resume()
    assert paused_until() == 0.0
