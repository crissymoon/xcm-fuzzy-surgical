"""Command and Option chord handling for the whole-file editor.

macOS terminals keep Command chords for their own menus, so these chords only
arrive when the terminal reports them anyway, either through the CSI u super
modifier or by sending Escape plus the letter. Everything here mirrors the
Ctrl key commands so both paths stay in step.
"""

from __future__ import annotations

from fuzzy_surgical.utils import ChordKey


def run_chord(editor, key: ChordKey) -> bool:
    """Apply a Command or Option chord, returning False to keep editing."""
    char = key.char.lower()
    if char == "x":
        editor._cut_selection()
        return False
    if char == "c":
        editor._copy_selection()
        return False
    if char == "v":
        editor._paste_clipboard()
        return False
    if char == "a":
        editor._select_all()
        return False
    if char == "s":
        editor._save()
        return False
    if char == "z":
        editor._undo()
        return False
    if char == "g":
        editor._open_jump_prompt()
        return False
    label = ("Cmd+" if key.super else "Alt+") + key.char.upper()
    editor.notice = (
        f"{label} is not bound here. Ctrl+X cuts, Ctrl+C copies, Ctrl+V pastes."
    )
    return False
