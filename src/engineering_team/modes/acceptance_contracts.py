"""The contracts of the ``acceptance`` stage: what the quality engineer hands over, and what the
controller records.

``AcceptanceSuite`` is the agent's account (the tests it says it wrote); it is never evidence.
``AcceptanceRecord`` is the controller's: the tests it ran itself on the baseline and saw fail for
the right reason, pinned by digest, and whether each passes after the implementation.
"""

from __future__ import annotations

from pydantic import Field

from engineering_team.contracts import Contract
from engineering_team.modes.fix_contracts import ReproRun

CHECK_PREFIX = "acceptance-"


class AcceptanceTest(Contract):
    """One acceptance test: the criteria it proves, its new test files, and one command line
    (no shell operators) that runs only this test."""

    criteria: list[str] = Field(default_factory=list)  # spec criterion ids, e.g. AC-1
    files: list[str] = Field(default_factory=list)  # new test files, relative paths
    command: str = ""
    expected_failure: str = ""  # what it should say on the baseline, for the record
    summary: str = ""


class AcceptanceSuite(Contract):
    """What the quality engineer returns: its tests, and the criteria no automated test can show
    (``untestable``: ``AC-3: needs a person to look at the layout``)."""

    tests: list[AcceptanceTest] = Field(default_factory=list)
    untestable: list[str] = Field(default_factory=list)
    summary: str = ""


class AcceptedTest(Contract):
    """A test the controller saw fail on the baseline for the right reason (``red``); ``green``
    is set once the same command passes in verification with its files unchanged."""

    id: str  # the check id: acceptance-1, acceptance-2, ...
    criteria: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    command: str
    expected_failure: str = ""
    red: ReproRun
    green: ReproRun | None = None


class AcceptanceRecord(Contract):
    """What the controller established, from its own runs.

    ``digests`` pin every accepted file at the moment it was seen failing; ``created`` is every
    file the stage's attempts wrote, so files of a rejected test are removed and a stage that
    runs again starts clean. ``rejected`` says why each refused test was refused.
    """

    attempts: int = 0
    revision: str | None = None  # the baseline tree the accepted tests failed on
    accepted: list[AcceptedTest] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    untestable: list[str] = Field(default_factory=list)  # the agent's account, for a person
    digests: dict[str, str] = Field(default_factory=dict)
    created: list[str] = Field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys(name for test in self.accepted for name in test.files))

    def covered(self) -> list[str]:
        """The criteria at least one accepted test is for, in first-seen order."""

        return list(dict.fromkeys(c for test in self.accepted for c in test.criteria))
