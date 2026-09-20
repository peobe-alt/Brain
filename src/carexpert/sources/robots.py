"""Read robots.txt the way the standard says, not the way it parses easily.

Why this file exists
--------------------

Python's `urllib.robotparser` gets two things wrong, and both were measured
on AutoScout24's real robots.txt.

It **over-blocks on `?`**. The site writes `Disallow: /lst?`, meaning "the
search page with a query string". `RuleLine.__init__` runs the pattern
through `urlunparse(urlparse(path))`, which drops an empty query and turns
it into `/lst` - a prefix that then blocks `/lst/volkswagen/golf`, a page
the site allows. That one line cost this project its largest source: the
diagnostic reported "INTERDIT PAR LE ROBOTS.TXT" on a URL nobody had
forbidden.

It **under-blocks on wildcards**. The same file writes `Disallow: */util/*`
and `Allow: /garages/*page=1`. `RuleLine.applies_to` is a bare `startswith`,
so no URL ever starts with `*` and the first rule matches nothing, while the
second never grants what it means to grant.

Both directions are wrong, and this project's first rule is that robots.txt
is honoured. So the matching follows RFC 9309:

* `*` matches any run of characters, `$` anchors to the end of the URL;
* the pattern is matched against path **and query**, unaltered;
* the longest matching rule wins, and `Allow` wins a tie - which is how a
  site carves an exception out of a broader refusal;
* an empty `Disallow:` allows everything, and is not a rule of length zero
  that would beat everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

#: A group with no rules at all allows everything: that is what a site means
#: by naming a crawler and saying nothing about it.
DEFAULT_ALLOW = True


@dataclass(frozen=True)
class Rule:
    """One `Allow:` or `Disallow:` line, compiled."""

    allow: bool
    pattern: str
    matcher: re.Pattern[str]

    @property
    def weight(self) -> int:
        """How specific this rule is: RFC 9309 ranks by pattern length."""
        return len(self.pattern)

    def matches(self, target: str) -> bool:
        return self.matcher.match(target) is not None


@dataclass
class Group:
    """The rules a site addresses to one or more named crawlers."""

    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


def _compile(pattern: str) -> re.Pattern[str]:
    """A robots path pattern as a regex: `*` is any run, `$` is the end.

    Everything else is literal - `?`, `&`, `=` and `.` included. Escaping
    them is the whole point: a query string is part of what a rule addresses.
    """
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = "".join(".*" if char == "*" else re.escape(char) for char in body)
    return re.compile(expression + ("$" if anchored else ""))


def _target(url: str) -> str:
    """The part of a URL that robots rules address: path plus query."""
    parsed = urlparse(unquote(url))
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return target


class RobotsRules:
    """A parsed robots.txt, queried per crawler."""

    def __init__(self, groups: list[Group] | None = None) -> None:
        self.groups = groups or []

    # -- parsing -----------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "RobotsRules":
        groups: list[Group] = []
        current: Group | None = None
        # Consecutive `User-agent` lines share the rules that follow. A rule
        # line closes the run, so the next agent starts a new group.
        naming = False

        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field_name, _, value = line.partition(":")
            field_name = field_name.strip().lower()
            value = value.strip()

            if field_name == "user-agent":
                if current is None or not naming:
                    current = Group()
                    groups.append(current)
                    naming = True
                current.agents.append(value.lower())
                continue

            if current is None:
                continue
            naming = False

            if field_name in ("disallow", "allow"):
                if not value:
                    # `Disallow:` with nothing after it allows everything, and
                    # an empty `Allow:` says nothing at all. Neither is a rule
                    # of length zero, which would otherwise outrank every
                    # other line by being matched by every URL.
                    continue
                current.rules.append(
                    Rule(allow=field_name == "allow", pattern=value,
                         matcher=_compile(value))
                )
            elif field_name == "crawl-delay":
                try:
                    current.crawl_delay = float(value.replace(",", "."))
                except ValueError:
                    continue

        return cls(groups)

    # -- querying ----------------------------------------------------------

    def group_for(self, user_agent: str) -> Group | None:
        """The group a crawler must obey: the most specific one naming it.

        A site that names `ClaudeBot` and `*` expects ClaudeBot to read its
        own group and ignore the general one entirely, even when the general
        one is more permissive.
        """
        token = user_agent.split("/")[0].strip().lower()
        best: tuple[int, Group] | None = None
        fallback: Group | None = None

        for group in self.groups:
            for agent in group.agents:
                if agent == "*":
                    if fallback is None:
                        fallback = group
                    continue
                if agent and agent in token:
                    if best is None or len(agent) > best[0]:
                        best = (len(agent), group)
        return best[1] if best else fallback

    def allowed(self, user_agent: str, url: str) -> bool:
        group = self.group_for(user_agent)
        if group is None or not group.rules:
            return DEFAULT_ALLOW

        target = _target(url)
        winner: Rule | None = None
        for rule in group.rules:
            if not rule.matches(target):
                continue
            # Longest wins; on a tie `Allow` wins, which is how a site carves
            # an exception out of a broader refusal.
            if winner is None or rule.weight > winner.weight or (
                rule.weight == winner.weight and rule.allow and not winner.allow
            ):
                winner = rule
        return winner.allow if winner else DEFAULT_ALLOW

    def crawl_delay(self, user_agent: str) -> float | None:
        group = self.group_for(user_agent)
        return group.crawl_delay if group else None
