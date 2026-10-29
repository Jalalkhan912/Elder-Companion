from voice_agent.security import PinLockout, hash_pin, verify_pin


def test_hash_verify():
    h = hash_pin("1234")
    assert h.startswith("scrypt$")
    assert verify_pin("1234", h)
    assert not verify_pin("1235", h)
    assert not verify_pin("1234", "garbage")


def test_hashes_are_salted():
    assert hash_pin("1234") != hash_pin("1234")


def test_lockout_after_five_then_unlocks():
    now = [0.0]
    lock = PinLockout(clock=lambda: now[0])
    for _ in range(4):
        lock.record_failure()
        assert not lock.is_locked()
    lock.record_failure()
    assert lock.is_locked()
    now[0] = 299
    assert lock.is_locked()
    now[0] = 300
    assert not lock.is_locked()
    lock.record_failure()
    assert not lock.is_locked()
