"""The bisect behind ``maintain --task upgrade-deps``: which upgrades can go in, which cannot."""

from __future__ import annotations

from collections.abc import Callable

from engineering_team.modes.upgrade import Attempt, bisect_upgrades


def scripted(
    bad: set[int], *, together: set[frozenset[int]] | None = None
) -> tuple[Callable[[list[int]], Attempt], list[list[int]]]:
    """An attempt that fails when the group holds a bad item (or a pair that cannot coexist)."""

    log: list[list[int]] = []
    held: set[int] = set()  # what is already accepted: attempts build on it

    def attempt(group: list[int]) -> Attempt:
        log.append(list(group))
        for item in group:
            if item in bad:
                return Attempt(False, f"{item} breaks the tests")
        for pair in together or ():
            if pair <= held | set(group) and not pair <= held:
                return Attempt(False, f"{sorted(pair)} cannot be installed together")
        held.update(group)
        return Attempt(True)

    return attempt, log


def test_when_everything_works_one_attempt_takes_the_lot() -> None:
    attempt, log = scripted(set())

    outcome = bisect_upgrades(list(range(5)), attempt, group_size=8)

    assert outcome.accepted == [0, 1, 2, 3, 4] and outcome.rejected == []
    assert log == [[0, 1, 2, 3, 4]]


def test_one_bad_upgrade_is_found_by_halving_and_the_rest_go_in() -> None:
    attempt, log = scripted({5})

    outcome = bisect_upgrades(list(range(8)), attempt, group_size=8)

    assert outcome.accepted == [0, 1, 2, 3, 4, 6, 7]
    assert outcome.rejected == [(5, "5 breaks the tests")]
    assert log == [
        [0, 1, 2, 3, 4, 5, 6, 7], [0, 1, 2, 3], [4, 5, 6, 7], [4, 5], [4], [5], [6, 7],
    ]  # fmt: skip
    assert len(log) < 8 + 1  # fewer than trying them one by one after a failed lot


def test_a_group_size_of_one_tries_them_strictly_one_by_one() -> None:
    attempt, log = scripted({1})

    outcome = bisect_upgrades([0, 1, 2], attempt, group_size=1)

    assert log == [[0], [1], [2]]
    assert outcome.accepted == [0, 2] and [item for item, _ in outcome.rejected] == [1]


def test_a_long_list_is_taken_in_groups() -> None:
    attempt, log = scripted(set())

    bisect_upgrades(list(range(7)), attempt, group_size=3)

    assert log == [[0, 1, 2], [3, 4, 5], [6]]


def test_when_everything_is_bad_everything_is_rejected_with_its_own_reason() -> None:
    attempt, _ = scripted({0, 1, 2})

    outcome = bisect_upgrades([0, 1, 2], attempt, group_size=8)

    assert outcome.accepted == []
    assert outcome.rejected == [
        (0, "0 breaks the tests"), (1, "1 breaks the tests"), (2, "2 breaks the tests"),
    ]  # fmt: skip


def test_two_upgrades_that_only_fail_together_end_with_the_second_one_rejected() -> None:
    attempt, _ = scripted(set(), together={frozenset({0, 1})})

    outcome = bisect_upgrades([0, 1], attempt, group_size=8)

    assert outcome.accepted == [0]
    assert outcome.rejected == [(1, "[0, 1] cannot be installed together")]


def test_an_empty_list_makes_no_attempt() -> None:
    attempt, log = scripted(set())

    outcome = bisect_upgrades([], attempt, group_size=8)

    assert outcome.accepted == [] and outcome.rejected == [] and log == []
