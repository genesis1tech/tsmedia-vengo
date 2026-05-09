from pathlib import Path
import re


ROUTER_PAGE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "tsv6"
    / "display"
    / "tsv6_player"
    / "router_page.html"
)


def _router_html() -> str:
    return ROUTER_PAGE.read_text(encoding="utf-8")


def test_ad_error_refreshes_vengo_instead_of_showing_placeholder():
    html = _router_html()
    ad_error_block = re.search(
        r'msg === "AD_ERROR"\) \{(?P<body>.*?)\} else if \(msg === "STARTED"',
        html,
        re.S,
    )

    assert ad_error_block is not None
    assert "refreshVengoIdle" in ad_error_block.group("body")
    assert "handleShowIdle" not in ad_error_block.group("body")


def test_vengo_watchdog_refreshes_silent_iframe_stalls():
    html = _router_html()

    assert "_VENGO_POSTMESSAGE_TIMEOUT_MS = 120 * 1000" in html
    assert "startVengoWatchdog" in html
    assert "no postMessage for " in html


def test_vengo_watchdog_tracks_iframe_postmessages():
    html = _router_html()

    assert "event.source === iframe.contentWindow" in html
    assert "noteVengoMessage" in html
