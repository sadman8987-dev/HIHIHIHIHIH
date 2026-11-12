from mediabot.keyboards import quality_keyboard


def test_quality_keyboard_offers_available_resolutions_and_audio() -> None:
    keyboard = quality_keyboard("abc123", [1080, 720, 480])
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "get:abc123:v:1080" in callbacks
    assert "get:abc123:v:720" in callbacks
    assert "get:abc123:v:480" in callbacks
    assert "get:abc123:a:0" in callbacks
    assert all(len(callback.encode()) <= 64 for callback in callbacks if callback)


def test_quality_keyboard_has_safe_fallback() -> None:
    keyboard = quality_keyboard("abc123", [])
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "get:abc123:v:0" in callbacks
    assert "get:abc123:a:0" in callbacks
