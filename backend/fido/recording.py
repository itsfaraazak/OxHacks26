"""What was recorded from the voice actor (Dheer), and where each take ends up.

The team decided to use only these six recordings (no AI voice, no clone): four dog sounds and
two spoken lines. Each take becomes a clip in `backend/clips/` that the app plays directly;
which moment plays which clip is `fido.speech.PLAYLIST`, and a clip may be reused for several
moments. Everything is done in character: a big, deep, warm, playful cartoon dog.

Files (see docs/audio.md):

- raw takes   `backend/recordings/<slot>/take-<n>.wav`, accepted one `final.wav`  (gitignored)
- consent     `backend/recordings/consent.json`                                  (gitignored)
- clips       `backend/clips/<clip>.mp3`  (what the app plays; commit with the actor's OK)
- woofs       `backend/clips/ingredients/<ingredient>.mp3` (only used by the unused generator,
              which can join a recorded woof before a generated line)

Nothing is uploaded anywhere: recordings stay on this computer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .speech import CAPTIONS


@dataclass(frozen=True)
class Take:
    slot: str  # folder under recordings/, also the unique id of the take
    kind: str  # "woof" | "line" | "passage"
    text: str  # words to say (or the sound to make)
    direction: str  # how to perform it
    clips: tuple[str, ...] = ()  # clip names (backend/clips/<name>.mp3) this take becomes directly
    ingredient: str | None = None  # name under clips/ingredients/ (a woof joined before spoken lines)
    seconds: tuple[float, float] = (0.5, 8.0)  # sensible length; the recorder warns outside it
    for_clone: bool = False  # material for a voice clone (the optional, unused generator)


def _line(clip: str, direction: str, seconds: tuple[float, float] = (2.0, 9.0)) -> Take:
    """A spoken line: the words are the clip's caption, so recording and app text always match."""
    return Take(
        slot=clip, kind="line", text=CAPTIONS[clip], direction=direction,
        clips=(clip,), seconds=seconds, for_clone=True,
    )


TAKES: tuple[Take, ...] = (
    # --- dog sounds ---
    Take(
        "woof-1", "woof", "Woof!",
        "ONE deep, chesty woof from the belly. A big friendly dog, not a puppy yap. Short and punchy.",
        clips=("woof-1",), ingredient="woof-1", seconds=(0.3, 2.0),
    ),
    Take(
        "woof-2", "woof", "Woof! Woof!",
        "TWO deep woofs, playful and excited, a clear gap between them.",
        clips=("bark-4",), ingredient="woof-2", seconds=(0.6, 3.0),
    ),
    Take(
        "woof-3", "woof", "Woof woof woof!",
        "THREE firm, insistent woofs, getting a little louder: 'I'm serious now'. Not aggressive.",
        clips=("bark-5",), ingredient="woof-3", seconds=(0.9, 4.0),
    ),
    Take(
        "growl-1", "woof", "Grrr... WOOF!",
        "A low, rumbling growl that builds, then ONE loud angry bark. Cartoon-villain dog, stealing your phone.",
        clips=("run-5",), ingredient="growl-1", seconds=(1.0, 4.0),
    ),
    # --- spoken lines (the words are the on-screen captions) ---
    _line("dock-1", "Excited and delighted, deep warm voice, like greeting your owner at the door."),
    _line("nudge-1", "Soft, whiny and pleading, quieter and a bit sad. Persuasive, like asking for a treat."),
)

BY_SLOT = {t.slot: t for t in TAKES}
assert len(BY_SLOT) == len(TAKES)
assert all(c in CAPTIONS for t in TAKES for c in t.clips)
