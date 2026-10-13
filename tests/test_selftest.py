import pytest

from pwca_monitor.selftest import SCENARIOS


@pytest.mark.parametrize("name,fn", SCENARIOS, ids=[n for n, _ in SCENARIOS])
def test_scenario(name, fn):
    fn()
