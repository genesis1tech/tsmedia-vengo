from pathlib import Path


SETTINGS_PAGE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "tsv6"
    / "display"
    / "tsv6_player"
    / "settings.html"
)


def _settings_html() -> str:
    return SETTINGS_PAGE.read_text(encoding="utf-8")


def test_settings_page_posts_exit_on_unload():
    html = _settings_html()

    assert 'window.addEventListener("pagehide", beaconExitSettings)' in html
    assert 'window.addEventListener("beforeunload", beaconExitSettings)' in html
    assert 'navigator.sendBeacon("/api/exit-settings"' in html
    assert 'fetch("/api/exit-settings", { method: "POST", keepalive: true })' in html


def test_close_button_reuses_unload_resume_path():
    html = _settings_html()

    assert "function exitSettings()" in html
    assert "function postExitSettings()" in html
    assert "postExitSettings().then(goHome);" in html
    assert "setTimeout(goHome, 1500);" in html


def test_settings_page_defaults_to_status_with_wifi_and_servo_tabs():
    html = _settings_html()

    assert 'id="statusView" class="view active"' in html
    assert 'id="wifiView" class="view"' in html
    assert 'id="motorView" class="view"' in html
    assert 'id="wifiTab"' in html
    assert 'id="motorTab"' in html
    assert 'fetch("/api/device/status")' in html
    assert 'fetch("/api/motor/status")' in html
