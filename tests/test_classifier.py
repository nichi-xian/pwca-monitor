from pwca_monitor.classifier import PRIORITY_LODGING, PRIORITY_NORMAL, KeywordClassifier
from pwca_monitor.models import DiffResult, Link

K = KeywordClassifier(["accommodation", "room", "fee", "check-in", "first come", "宿泊", "予約"])


def test_case_insensitive_and_word_boundary():
    assert K.find(["ACCOMMODATION info"]) == ["accommodation"]
    assert K.find(["classroom feedback"]) == []  # no partial-word matches


def test_hyphen_space_equivalence():
    assert K.find(["Check in 14:00"]) == ["check-in"]
    assert K.find(["check-in"]) == ["check-in"]


def test_japanese_substring():
    assert K.find(["宿泊予約は12月から"]) == ["宿泊", "予約"]


def test_classify_uses_added_text_and_new_links_only():
    d = DiffResult(added_lines=["Task briefing"], removed_lines=["Accommodation TBA"])
    assert K.classify(d) == (PRIORITY_NORMAL, [])
    d = DiffResult(added_links=[Link("Room list", "https://x/r")])
    assert K.classify(d) == (PRIORITY_LODGING, ["room"])
