"""Resolve ``filename:line`` queries against the indexed files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fuzzy_surgical.fuzzy_matcher import FuzzyMatcher
from fuzzy_surgical.query_parser import LineReference
from fuzzy_surgical.utils import relative_path

LineResult = Tuple[str, int, str, float]

EXACT_PATH_SCORE = 100.0
SUFFIX_PATH_SCORE = 80.0
BASENAME_SCORE = 60.0
FUZZY_PATH_SCORE = 30.0


@dataclass(frozen=True)
class JumpTarget:
    """A resolved file, its ordered lines and the line to select."""

    file_path: str
    requested_line: int
    selected_line: int
    results: List[LineResult]
    notice: str = ""


class LineJumpResolver:
    """Turns a ``path:line`` query into the lines of the matching file.

    Only files that are already indexed can be resolved, so the jump never
    reads a new path or escapes the chosen directory.
    """

    def resolve(
        self,
        reference: LineReference,
        file_lines: Dict[str, List[tuple]],
        root_dir: Optional[Path],
        limit: int = 2000,
        prefer_file: Optional[str] = None,
        prefer_line: Optional[int] = None,
    ) -> Optional[JumpTarget]:
        """Return the target file's lines with the requested line selected."""
        file_path = self.find_file(reference.path, file_lines, root_dir)
        if file_path is None:
            return None

        lines = file_lines.get(file_path) or []
        if not lines:
            return None

        selected_line = reference.line
        if prefer_file == file_path and prefer_line is not None and prefer_line >= 1:
            selected_line = prefer_line

        indexed_last = lines[-1][0]
        notice = ""
        if selected_line > indexed_last:
            notice = (
                f"{Path(file_path).name} has {indexed_last} indexed lines; "
                f"showing line {indexed_last}."
            )
            selected_line = indexed_last

        results: List[LineResult] = [
            (file_path, number, text, 0.0) for number, text in lines
        ]
        selected_index = self._index_of(results, selected_line)

        if 0 < limit < len(results):
            start = max(0, min(selected_index - limit // 2, len(results) - limit))
            results = results[start : start + limit]
            selected_index -= start

        return JumpTarget(
            file_path=file_path,
            requested_line=reference.line,
            selected_line=results[selected_index][1],
            results=results,
            notice=notice,
        )

    @classmethod
    def find_file(
        cls,
        target: str,
        file_lines: Dict[str, List[tuple]],
        root_dir: Optional[Path],
    ) -> Optional[str]:
        """Pick the indexed file that best matches a typed path fragment."""
        query = target.strip().replace("\\", "/").lower()
        if not query:
            return None

        query_name = query.rsplit("/", 1)[-1]
        # Free text such as "time:30" should stay a normal line search unless
        # the path portion clearly names a file (a dot or a separator).
        allow_fuzzy = "/" in query or "." in query_name
        best_path: Optional[str] = None
        best_key: Optional[Tuple[float, int, str]] = None

        for file_path in file_lines:
            rel = relative_path(file_path, root_dir).replace("\\", "/")
            rel_lower = rel.lower()
            name = rel_lower.rsplit("/", 1)[-1]
            absolute_lower = file_path.replace("\\", "/").lower()

            if rel_lower == query or absolute_lower == query:
                score = EXACT_PATH_SCORE
            elif query_name and name == query_name:
                score = BASENAME_SCORE
            elif rel_lower.endswith("/" + query) or absolute_lower.endswith("/" + query):
                score = SUFFIX_PATH_SCORE
            else:
                if not allow_fuzzy:
                    continue
                matched, fuzzy_score, _ = FuzzyMatcher.fuzzy_match(query, rel_lower)
                if not matched:
                    continue
                score = FUZZY_PATH_SCORE + min(fuzzy_score, 20.0)

            # Shorter relative paths are less ambiguous, so they win ties.
            key = (score, -len(rel), rel_lower)
            if best_key is None or key > best_key:
                best_key = key
                best_path = file_path

        return best_path

    @staticmethod
    def _index_of(results: List[LineResult], line_num: int) -> int:
        for index, (_, number, _, _) in enumerate(results):
            if number >= line_num:
                return index
        return max(0, len(results) - 1)
