from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def quality_keyboard(key: str, heights: list[int]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    seen: set[int] = set()
    for target in (2160, 1440, 1080, 720, 480, 360):
        available = [height for height in heights if height <= target]
        if not available:
            continue
        selected = max(available)
        if selected in seen:
            continue
        seen.add(selected)
        label = f"🎬 {selected}p"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"get:{key}:v:{selected}")])
    if heights:
        best = max(heights)
        if best not in seen:
            buttons.insert(0, [InlineKeyboardButton(text=f"✨ أفضل جودة ({best}p)", callback_data=f"get:{key}:v:{best}")])
    else:
        buttons.append([InlineKeyboardButton(text="🎬 أفضل جودة متاحة", callback_data=f"get:{key}:v:0")])
    buttons.append([InlineKeyboardButton(text="🎧 صوت MP3", callback_data=f"get:{key}:a:0")])
    buttons.append([InlineKeyboardButton(text="إلغاء", callback_data=f"cancel:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="إلغاء الطلب", callback_data=f"cancel:{key}")]])
