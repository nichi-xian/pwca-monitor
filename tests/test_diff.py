from pwca_monitor.diff import diff_snapshots
from pwca_monitor.parser import parse_page

P = {"content_selector": "main", "attachment_extensions": [".pdf"]}


def snap(body):
    return parse_page(f"<html><title>T</title><main>{body}</main></html>", "https://x/e", "https://x/e", "2026-10-13T00:00:00Z", P)


def test_identical_no_changes():
    d = diff_snapshots(snap("<p>a</p>"), snap("<p>a</p>"))
    assert not d.has_changes


def test_whitespace_only_no_changes():
    d = diff_snapshots(snap("<p>a  b</p>"), snap('<p class="z">\n a\n\tb </p>'))
    assert not d.has_changes


def test_add_remove_change():
    d = diff_snapshots(snap("<p>a</p><p>b</p>"), snap("<p>a</p><p>c</p><p>d</p>"))
    assert d.added_lines == ["c", "d"] and d.removed_lines == ["b"]


def test_links_added_changed_pdf():
    old = snap('<a href="/f">Form</a>')
    new = snap('<a href="/g">Form</a><a href="/doc.pdf">Doc</a>')
    d = diff_snapshots(old, new)
    assert [(o.url, n.url) for o, n in d.changed_links] == [("https://x/f", "https://x/g")]
    assert [l.url for l in d.added_links] == ["https://x/doc.pdf"]
    assert [l.url for l in d.added_attachments] == ["https://x/doc.pdf"]


def test_heading_change():
    d = diff_snapshots(snap("<h2>Info</h2>"), snap("<h2>Accommodation</h2>"))
    assert d.heading_added == ["Accommodation"] and d.heading_removed == ["Info"]


def test_diff_hash_stable():
    a, b = snap("<p>a</p>"), snap("<p>b</p>")
    assert diff_snapshots(a, b).diff_hash == diff_snapshots(a, b).diff_hash
    assert diff_snapshots(a, b).diff_hash != diff_snapshots(b, a).diff_hash
