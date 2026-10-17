"""Fuzzy matching algorithm for line searching."""

import heapq
from typing import Dict, List, Optional, Tuple


class FuzzyMatcher:
    """Implements fuzzy subsequence matching with a relevance score."""

    EXACT_BONUS = 2.5
    WORD_BOUNDARY_BONUS = 1.0

    @staticmethod
    def _positions(query: str, text: str) -> Optional[List[int]]:
        """Return match positions using C level string search.

        ``query`` and ``text`` must already be lowercase.
        """
        positions: List[int] = []
        start = 0
        for char in query:
            found = text.find(char, start)
            if found < 0:
                return None
            positions.append(found)
            start = found + 1
        return positions

    @staticmethod
    def _score(text: str, positions: List[int]) -> float:
        score = 0.0
        previous = -2
        for position in positions:
            if position == 0 or not text[position - 1].isalnum():
                score += 1.0
            if position == previous + 1:
                score += 0.5
            previous = position
        return score

    @classmethod
    def _match(cls, query_lower: str, text_lower: str) -> Tuple[Optional[List[int]], float]:
        """Return match positions and a score, or (None, 0) when unmatched.

        A literal substring always wins over a scattered subsequence so the
        selected result is the line containing the text the user typed.
        """
        index = text_lower.find(query_lower)
        if index >= 0:
            positions = list(range(index, index + len(query_lower)))
            score = cls._score(text_lower, positions) + cls.EXACT_BONUS
            if index == 0 or not text_lower[index - 1].isalnum():
                score += cls.WORD_BOUNDARY_BONUS
            return positions, score

        positions = cls._positions(query_lower, text_lower)
        if positions is None:
            return None, 0.0
        return positions, cls._score(text_lower, positions)

    @classmethod
    def fuzzy_match(cls, query: str, text: str) -> Tuple[bool, float, List[int]]:
        """Check if query fuzzily matches text.

        Returns (match, score, positions).
        """
        if not query:
            return True, 0.0, []

        positions, score = cls._match(query.lower(), text.lower())
        if positions is None:
            return False, 0.0, []
        return True, score, positions

    def search_lines(
        self,
        file_lines: Dict[str, List[tuple]],
        query: str,
        limit: int = 1000,
    ) -> List[Tuple[str, int, str, float]]:
        """Search all indexed lines.

        Returns (file_path, line_num, line_text, score) sorted by relevance.
        """
        if limit <= 0:
            return []

        if not query:
            results = []
            for file_path, lines in file_lines.items():
                for line_num, line_text in lines:
                    results.append((file_path, line_num, line_text, 0.0))
                    if len(results) >= limit:
                        return results
            return results

        query_lower = query.lower()
        results = []
        for file_path, lines in file_lines.items():
            for line_num, line_text in lines:
                positions, score = self._match(query_lower, line_text.lower())
                if positions is None:
                    continue
                results.append((file_path, line_num, line_text, score))

        # Higher scores first; shorter lines win ties so signal stays near the top.
        # nlargest avoids sorting every match when a large tree is indexed.
        def key(item):
            return (item[3], -len(item[2]), item[0], item[1])

        top = heapq.nlargest(limit, results, key=key)
        top.sort(key=lambda item: (-item[3], len(item[2]), item[0], item[1]))
        return top
