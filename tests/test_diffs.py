"""The diffs loadout prints keep one change per line, as git shows them."""

from __future__ import annotations

from loadout.diffs import diff_lines


def test_a_last_line_without_a_newline_stays_on_its_own_line() -> None:
    """Review 3, after 2.11: `-}` and `+{}` printed as one line, `-}+{}`."""
    assert diff_lines("doc", '{\n  "a": 1\n}', "{}", context=1) == [
        "--- doc (on disk)\n",
        "+++ doc (expected)\n",
        "@@ -1,3 +1 @@\n",
        "-{\n",
        '-  "a": 1\n',
        "-}\n",
        "\\ No newline at end of file\n",
        "+{}\n",
        "\\ No newline at end of file\n",
    ]


def test_lines_that_end_in_a_newline_carry_no_marker() -> None:
    assert diff_lines("doc", "a\n", "b\n", context=1) == [
        "--- doc (on disk)\n",
        "+++ doc (expected)\n",
        "@@ -1 +1 @@\n",
        "-a\n",
        "+b\n",
    ]
