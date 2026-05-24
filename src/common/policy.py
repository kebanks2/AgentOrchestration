"""Policy runtime checks for orchestration transitions."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = "allowed"


class PolicyRuntime:
    """Small fail-closed policy boundary used before task dispatch.

    The runtime is intentionally dependency-light: callers can inject a local
    or remote evaluator. Any evaluator failure is treated as unavailable policy
    infrastructure rather than as permission to continue.
    """

    def __init__(
        self,
        evaluator: Optional[Callable[[Dict[str, Any]], PolicyDecision]] = None,
        available: bool = True,
    ):
        self._evaluator = evaluator
        self._available = available

    def set_available(self, available: bool) -> None:
        self._available = available

    def authorize_task(self, task: Dict[str, Any]) -> PolicyDecision:
        if not self._available:
            return PolicyDecision(False, "policy_unavailable")
        if self._evaluator is None:
            return PolicyDecision(True)
        try:
            decision = self._evaluator(task)
        except Exception:
            return PolicyDecision(False, "policy_unavailable")
        if isinstance(decision, PolicyDecision):
            return decision
        allowed = bool(decision)
        reason = "allowed" if allowed else "policy_denied"
        return PolicyDecision(allowed, reason)
