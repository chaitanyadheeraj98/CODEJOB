from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class AliasMatch:
    alias: str
    start: int
    end: int


class AliasMatcher:
    """Small Aho-Corasick matcher for normalized, word-bounded aliases."""

    def __init__(self, aliases: list[str]) -> None:
        self._next: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._outputs: list[list[str]] = [[]]
        for alias in dict.fromkeys(item for item in aliases if item):
            state = 0
            for char in alias:
                target = self._next[state].get(char)
                if target is None:
                    target = len(self._next)
                    self._next[state][char] = target
                    self._next.append({})
                    self._fail.append(0)
                    self._outputs.append([])
                state = target
            self._outputs[state].append(alias)

        queue = deque(self._next[0].values())
        while queue:
            state = queue.popleft()
            for char, target in self._next[state].items():
                queue.append(target)
                fallback = self._fail[state]
                while fallback and char not in self._next[fallback]:
                    fallback = self._fail[fallback]
                self._fail[target] = self._next[fallback].get(char, 0)
                self._outputs[target].extend(self._outputs[self._fail[target]])

    def find(self, text: str) -> list[AliasMatch]:
        matches: list[AliasMatch] = []
        state = 0
        for index, char in enumerate(text):
            while state and char not in self._next[state]:
                state = self._fail[state]
            state = self._next[state].get(char, 0)
            for alias in self._outputs[state]:
                start = index - len(alias) + 1
                end = index + 1
                if (start == 0 or text[start - 1] == " ") and (end == len(text) or text[end] == " "):
                    matches.append(AliasMatch(alias=alias, start=start, end=end))
        return matches
