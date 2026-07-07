from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class _FrontierItem:
    url: str
    depth: int


class Frontier:
    """BFS frontier with built-in dedup.

    Holds the FIFO queue of URLs waiting to be fetched (breadth-first) and the
    'seen' set: every URL ever enqueued. A URL is added to 'seen' the moment it
    is discovered, so two pages linking to the same target enqueue it once.

    Only URLs that will actually be fetched are pushed here (in-scope, within
    depth, potentially-HTML). Out-of-scope and known-non-HTML URLs are recorded
    as resources by the crawler but never enter the frontier.

    In Phase 5 this moves behind the same interface but becomes database-backed;
    in Phase 19 it becomes distributed. The interface is intentionally minimal.
    """

    def __init__(self) -> None:
        self._queue: deque[_FrontierItem] = deque()
        self._seen: set[str] = set()

    def push(self, url: str, depth: int) -> bool:
        """Enqueue ``url`` at ``depth`` if not already seen. Returns True if
        newly added, False if it was already in the seen set."""
        if url in self._seen:
            return False
        self._seen.add(url)
        self._queue.append(_FrontierItem(url, depth))
        return True

    def pop(self) -> _FrontierItem | None:
        """FIFO pop. Returns None when the frontier is empty."""
        if not self._queue:
            return None
        return self._queue.popleft()

    def __len__(self) -> int:
        return len(self._queue)

    def __contains__(self, url: str) -> bool:
        return url in self._seen

    @property
    def seen_count(self) -> int:
        return len(self._seen)
