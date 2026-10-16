"""Judge interface and its Jev (TypeSafe System One) implementation.

typesafe_sdk is imported lazily inside JevJudge so importing this module stays cheap.
"""
from __future__ import annotations

from typing import Protocol

from .models import PromotionVerdict, TagVerdict, WindowVerdict

KINDS = {
    "rejection": "The user rejects, vetoes or says not to use or do something.",
    "decision": "The user makes a choice or decision between options, or fixes an approach.",
    "preference": "The user states a standing preference about how they like to work or how output should look.",
    "correction": "The user corrects a mistake or a wrong assumption the assistant made.",
    "none": "The message is only a task request, question, acknowledgement or other one-off content.",
}


class Judge(Protocol):
    def classify_window(self, text: str, context: str) -> WindowVerdict: ...
    def same_fact(self, a: str, b: str) -> float: ...
    def contradicts(self, a: str, b: str) -> float: ...
    def choose_tag(self, text: str, tags: dict[str, str]) -> TagVerdict: ...
    def promotion(self, texts: list[str]) -> PromotionVerdict: ...
    def fits_tag(self, text: str, tag: str, description: str) -> float: ...


class JevJudge:
    def __init__(self, client=None, model: str = "jev-latest"):
        self._client = client
        self.model = model

    @property
    def client(self):
        if self._client is None:
            from typesafe_sdk import TypeSafeClient  # reads TYPESAFE_API_KEY from env
            self._client = TypeSafeClient()
        return self._client

    def _ask(self, state: str, questions: dict):
        return self.client.system_one(state=state, questions=questions, model=self.model)

    @staticmethod
    def _noul(instructions: str):
        from typesafe_sdk import Noul
        return Noul(instructions=instructions)

    def _one_noul(self, key: str, state: str, instructions: str) -> float:
        resp = self._ask(state, {key: self._noul(instructions)})
        return float(resp.nouls[key].noul)

    def classify_window(self, text: str, context: str) -> WindowVerdict:
        from typesafe_sdk import Choice
        state = (f"Assistant message (context):\n{context}\n\n"
                 f"User message:\n{text}")
        resp = self._ask(state, {
            "kind": Choice(instructions="What does the user's message express?", criteria=dict(KINDS)),
            "sensitive": self._noul(
                "The user message contains a credential, secret, or private personal data."),
        })
        ans = resp.choices["kind"]
        kind = ans.choice
        return WindowVerdict(kind, float(ans.probabilities.get(kind, 0.0)),
                             float(resp.nouls["sensitive"].noul))

    def same_fact(self, a: str, b: str) -> float:
        return self._one_noul(
            "same", f"Statement A:\n{a}\n\nStatement B:\n{b}",
            "Statement A and statement B express the same fact, rule or decision.")

    def contradicts(self, a: str, b: str) -> float:
        return self._one_noul(
            "contradicts", f"Statement A:\n{a}\n\nStatement B:\n{b}",
            "Statement A and statement B contradict each other, so they cannot both be followed.")

    def choose_tag(self, text: str, tags: dict[str, str]) -> TagVerdict:
        from typesafe_sdk import Choice
        criteria = {name: desc for name, desc in tags.items() if name != "none"}
        criteria["none"] = "None of the other tags describes this fact."
        resp = self._ask(f"Fact:\n{text}", {
            "tag": Choice(instructions="Which tag best describes the topic of the fact?",
                          criteria=criteria)})
        ans = resp.choices["tag"]
        return TagVerdict(None if ans.choice == "none" else ans.choice,
                          {k: float(v) for k, v in ans.probabilities.items()})

    def promotion(self, texts: list[str]) -> PromotionVerdict:
        state = "Facts recorded in different projects:\n" + "\n".join(
            f"{i}. {t}" for i, t in enumerate(texts, 1))
        resp = self._ask(state, {
            "personal": self._noul(
                "These facts state a personal working rule or preference that is not tied to one project."),
            "same": self._noul("All of these facts express the same underlying fact."),
            "stable": self._noul(
                "These facts describe a lasting rule that is unlikely to change soon."),
        })
        n = resp.nouls
        return PromotionVerdict(float(n["personal"].noul), float(n["same"].noul),
                                float(n["stable"].noul))

    def fits_tag(self, text: str, tag: str, description: str) -> float:
        return self._one_noul(
            "fits", f"Fact:\n{text}\n\nTag: {tag}\nTag description: {description}",
            "The fact belongs under the tag, matching the tag's description.")
