"""Fido's voice: what the dog says, and the names of its audio clips.

The script is fixed text. Clips are generated once, offline, with
`scripts/generate_audio.py` (ElevenLabs), and committed to `backend/clips/`.
No user text (task names etc.) is ever sent to ElevenLabs, and nothing is
generated while a session is running, so a distraction never waits on a network
call and the demo keeps working if Wi-Fi or the API is down.

A *cue* is a moment (`nudge`, `bark`, `run`, ...). Each cue has several lines so
Fido does not repeat itself. Line `i` of cue `c` is the clip `c-<i+1>`
(`nudge-1`, `nudge-2`, ...), served at `/api/audio/<clip>.mp3`.

Lines are either spoken (`voice`, with Eleven v3 audio tags such as `[whimpers]`
or `[angry]`) or a sound effect (`sfx`, a prompt for woofs and growls). A spoken
line may also have a `bark`: a sound-effect prompt for a *real* dog bark that is
played first and joined to the speech in one clip. Barks come from the
sound-effects model, never from the voice model saying the word "woof", which
sounds like a person doing an impression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAGS = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True)
class Line:
    voice: str | None = None  # text-to-speech input, may contain [audio tags]
    sfx: str | None = None  # sound-effects prompt (woofs, growls)
    bark: str | None = None  # sound-effects prompt for a real bark played before `voice`
    caption: str = ""  # what the app shows; derived from `voice` when empty

    def __post_init__(self) -> None:
        if (self.voice is None) == (self.sfx is None):
            raise ValueError("a line is either spoken (voice) or a sound effect (sfx)")
        if self.bark is not None and self.voice is None:
            raise ValueError("only spoken lines take a bark prefix; sound-effect lines are the bark")
        if not self.caption:
            if self.voice is None:
                raise ValueError("sound-effect lines need a caption")
            spoken = re.sub(r"\s+", " ", _TAGS.sub("", self.voice)).strip()
            object.__setattr__(self, "caption", f"Woof! {spoken}" if self.bark else spoken)


def say(text: str, bark: str | None = None) -> Line:
    return Line(voice=text, bark=bark)


def sound(prompt: str, caption: str) -> Line:
    return Line(sfx=prompt, caption=caption)


# Real dog barks (sound-effects prompts). Deep and chesty: a big friendly dog, not a puppy yip.
BARK_ONE = "A single deep woof from a large friendly dog, chesty and warm, clean close recording, no background noise"
BARK_TWO = "Two deep woofs from a large dog, 'woof woof', chesty and playful, clean close recording, no background noise"
BARK_STERN = "Three firm deep barks from a large dog, insistent but not aggressive, clean close recording, no background noise"
BARK_ANGRY = "A large dog growling low then barking angrily, deep and loud, clean close recording, no background noise"

# When a real recording of the bark exists (clips/ingredients/<name>.mp3, see fido/recording.py)
# the generator uses it instead of calling the sound-effects model.
RECORDED_BARKS = {BARK_ONE: "woof-1", BARK_TWO: "woof-2", BARK_STERN: "woof-3", BARK_ANGRY: "growl-1"}


SCRIPT: dict[str, list[Line]] = {
    # A session was created and Fido is waiting for the phone to be docked.
    "dock": [
        say("[excited] Ooh, a new goal! Pop your phone on my back and let's get started!"),
        say("[cheerfully] Ready when you are! Put your phone on me and I'll keep you company."),
        say("[playfully] Hi hi hi! Give me your phone to look after. I'll take very good care of it."),
    ],
    # Phone docked, watching begins.
    "start": [
        say("[happy] Got it! I'm watching. You've got this, human!"),
        say("[cheerfully] Okay! Deep breath, one thing at a time. I'll be right here."),
        say("[excited] Let's go, let's go! Show that task who's boss!"),
    ],
    # First sign of distraction: gentle and persuasive.
    "nudge": [
        say("[whimpers] Psst... your task is waiting for you. I believe in you."),
        say("[softly] Hey... eyes back on the prize? Just a little more, then a break. Pinky promise."),
        say("[whines] I miss you being focused. Come back to your task with me?"),
        say("[gently] You were doing so well. One tiny step back to your work, okay?"),
    ],
    # Still distracted: firm but playful, with real woofs.
    "bark": [
        say("[excited] Hey, over here! Back to your task, please!", bark=BARK_TWO),
        say("[playfully] Last warning, human! Focus, focus, focus!", bark=BARK_STERN),
        say("[whines] Please? I really don't want to run away with your phone!", bark=BARK_ONE),
        sound(BARK_TWO, "Woof! Woof!"),
        sound(BARK_STERN, "Woof woof woof!"),
    ],
    # Running away with the phone: angry. Repeats while the escape lasts.
    "run": [
        say("[angry] That's it! I warned you! I'm taking your phone! Catch me if you can!", bark=BARK_ANGRY),
        say("[shouts] Too slow! Come and get it!", bark=BARK_TWO),
        say("[angry] You should have listened! Chase me!", bark=BARK_ANGRY),
        say("[angry] Not giving it back until you catch me!"),
        sound(BARK_ANGRY, "Grrr... WOOF!"),
    ],
    # The user caught Fido (good-boy card or app).
    "caught": [
        say("[laughs] Okay okay, you got me! Good human! Now back to work."),
        say("[sighs] Aww, you caught me. Fine. Here's your phone. Back to focus, deal?"),
        say("[happy] Yay, you caught me! That was fun. Now let's focus together."),
    ],
    # The escape timed out without being caught.
    "timeout": [
        say("[sighs] Okay, I'm tired of running. Come on, let's get back to work."),
        say("[yawns] I ran out of zoomies. Let's focus again, okay?"),
    ],
    # The user refocused before Fido ran away.
    "praise": [
        say("[happy] Good human! That's the spirit."),
        say("[excited] Yes! Back on track! I knew you could do it."),
        say("[cheerfully] Look at you go! Keep it up."),
    ],
    # Session completed.
    "done": [
        say("[excited] You did it! Who's a good human? You are!"),
        say("[joyfully] Session complete! Treat time! I'm so proud of you."),
        say("[laughs] You focused the whole time! Amazing!", bark=BARK_ONE),
    ],
}


def clip_name(cue: str, index: int) -> str:
    return f"{cue}-{index + 1}"


# clip name -> (cue, line). This is the *generated-voice* script, used only by
# scripts/generate_audio.py (not used at runtime now; see PLAYLIST below).
CLIPS: dict[str, tuple[str, Line]] = {
    clip_name(cue, i): (cue, line) for cue, lines in SCRIPT.items() for i, line in enumerate(lines)
}


# ---------------------------------------------------------------------------
# What Fido actually plays: the recorded clips in backend/clips/ (Dheer's voice and woofs).
#
# CAPTIONS: the words of each recorded clip, shown in the app. They MUST match what the clip
# really says, so edit them when you re-record.
# PLAYLIST: which clips play at each moment (a cue). A clip may appear under several cues
# (clips are reused). A cue that is missing or empty is silent. A cue's clips rotate in order.
# ---------------------------------------------------------------------------

CAPTIONS: dict[str, str] = {
    "dock-1": CLIPS["dock-1"][1].caption,  # spoken
    "nudge-1": CLIPS["nudge-1"][1].caption,  # spoken
    "woof-1": "Woof!",
    "bark-4": "Woof! Woof!",
    "bark-5": "Woof woof woof!",
    "run-5": "Grrr... WOOF!",
}

PLAYLIST: dict[str, list[str]] = {
    "dock": ["dock-1"],  # session created, asking for the phone
    "start": ["woof-1"],  # phone docked, watching begins
    "nudge": ["nudge-1"],  # first sign of distraction
    "bark": ["bark-4", "bark-5"],  # still distracted
    "run": ["run-5", "bark-5"],  # running away; repeats while the escape lasts
    "caught": ["bark-4"],  # the user caught Fido
    "timeout": ["nudge-1"],  # the escape timed out
    "praise": ["woof-1"],  # refocused before Fido ran away
    "done": ["bark-5"],  # session complete
}

# Every clip the app can play: the whitelist for /api/audio/<clip>.mp3.
PLAYABLE: tuple[str, ...] = tuple(dict.fromkeys(c for clips in PLAYLIST.values() for c in clips))

assert all(c in CAPTIONS for c in PLAYABLE), "every playlist clip needs a caption"
