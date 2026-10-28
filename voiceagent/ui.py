import re

YES = re.compile(r"\b(yes|yeah|yep|yup|go ahead|do it|confirm|confirmed|proceed|sure|okay|ok)\b", re.I)
NO = re.compile(r"\b(no|nope|cancel|stop|don't|do not|abort|wait|hold on|negative)\b", re.I)


def parse_yes_no(heard: str) -> bool | None:
    """True/False for a clear answer, None if unclear. 'No' wins over 'yes' (safer)."""
    if NO.search(heard):
        return False
    if YES.search(heard):
        return True
    return None


class TextUI:
    """Keyboard stand-in for debugging without mic/speakers."""

    def listen(self) -> str:
        return input("you> ").strip()

    def say(self, text: str) -> None:
        print(f"[agent] {text}")

    def confirm(self, question: str) -> bool:
        return input(f"[confirm] {question} (y/N) ").strip().lower() in ("y", "yes")


class VoiceUI:
    def __init__(self):
        from . import audio, stt, tts
        self._audio, self._stt, self._tts = audio, stt, tts

    def listen(self) -> str:
        from .config import cfg
        if cfg.push_to_talk:
            input("Press Enter, then speak... ")
        text = self._stt.transcribe(self._audio.record_utterance())
        print(f"[you] {text}")
        return text

    def say(self, text: str) -> None:
        self._tts.speak(text)

    def confirm(self, question: str, retries: int = 2) -> bool:
        for _ in range(retries + 1):
            self.say(question + " Say yes to go ahead, or no to cancel.")
            heard = self.listen()
            answer = parse_yes_no(heard)
            if answer is not None:
                return answer
        return False
