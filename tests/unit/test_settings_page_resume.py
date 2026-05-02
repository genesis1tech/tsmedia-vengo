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

    assert 'window.addEventListener("pagehide", postExitSettings)' in html
    assert 'window.addEventListener("beforeunload", postExitSettings)' in html
    assert 'navigator.sendBeacon("/api/exit-settings"' in html
    assert 'fetch("/api/exit-settings", { method: "POST", keepalive: true })' in html


def test_close_button_reuses_unload_resume_path():
    html = _settings_html()

    assert "function exitSettings()" in html
    assert "postExitSettings();" in html
