import pytest

from agent.modules.browser.snapshot import NO_CHANGES, compact_snapshot, diff_snapshot, parse


def tree(*lines):
    return "\n".join(lines)


def test_compact_merges_typed_characters_and_keeps_word_spaces():
    compacted = compact_snapshot(tree(
        "[1-1] paragraph",
        "  [1-2] StaticText: H",
        "  [1-3] StaticText: i",
        "  [1-4] StaticText: ",
        "  [1-5] StaticText: y",
        "  [1-6] StaticText: o",
        "  [1-7] StaticText: Whole sentence stays separate.",
    ))
    assert compacted == tree(
        "[1-1] paragraph",
        "  [1-2] StaticText: Hi yo",
        "  [1-7] StaticText: Whole sentence stays separate.",
    )


def test_compact_leaves_short_runs_and_other_depths_apart():
    labels = tree(
        "[1-1] button: Grade",
        "  [1-2] StaticText: A",
        "  [1-3] StaticText: B",
        "[1-4] group",
        "  [1-5] div",
        "    [1-6] StaticText: x",
        "  [1-8] StaticText: ",
        "  [1-9] StaticText: ",
        "  [1-10] StaticText: ",
    )
    assert compact_snapshot(labels) == labels


def test_compact_drops_only_unnamed_icons_inside_controls():
    compacted = compact_snapshot(tree(
        "[1-1] button: Send message",
        "  [1-2] image",
        "[1-3] link: Changelog",
        "  [1-4] image",
        "[1-5] button: Logo",
        "  [1-6] image: Company",
        "[1-7] image",
        "[1-8] div",
        "  [1-9] image",
        "[1-10] button: Expand",
        "  [1-11] image",
        "    [1-12] StaticText: nested",
    ))
    assert compacted == tree(
        "[1-1] button: Send message",
        "[1-3] link: Changelog",
        "[1-5] button: Logo",
        "  [1-6] image: Company",
        "[1-7] image",
        "[1-8] div",
        "  [1-9] image",
        "[1-10] button: Expand",
        "  [1-11] image",
        "    [1-12] StaticText: nested",
    )


def test_text_that_is_not_a_tree_is_left_alone():
    assert compact_snapshot("button Login [1-1]") == "button Login [1-1]"
    assert diff_snapshot("button Login [1-1]", "button Logout [1-1]") is None


def test_multiline_names_survive_a_round_trip():
    text = tree("[1-1] paragraph", "  [1-2] StaticText: first", "second line", "[1-3] button: Next")
    assert compact_snapshot(text) == text
    assert parse(text)[1].label == "StaticText: first\nsecond line"


PAGE = tree(
    "[1-1] RootWebArea: App",
    "  [1-2] body",
    "    [1-3] button: Select model",
    "    [1-4] button: Send message",
    "    [1-5] group",
    "      [1-6] button: Flash",
    "      [1-7] button: Pro",
    "    [1-8] StaticText: A long enough footer line so that a small change stays shorter than the full tree.",
)


def test_diff_lists_new_subtree_under_its_parent_and_removed_elements():
    after = PAGE.replace("    [1-4] button: Send message\n", "") + "\n    [1-9] menu: Pro\n      [1-10] menuitemradio: 512K"
    assert diff_snapshot(PAGE, after) == tree(
        "Changes since the previous snapshot. IDs not listed or counted under Removed remain valid.",
        "Added or changed:",
        "under [1-2] body",
        "  [1-9] menu: Pro",
        "    [1-10] menuitemradio: 512K",
        "Removed:",
        "  [1-4] button: Send message",
    )


def test_diff_prints_a_subtree_that_is_gone_entirely_as_its_root():
    after = PAGE.replace("    [1-5] group\n      [1-6] button: Flash\n      [1-7] button: Pro\n", "")
    assert diff_snapshot(PAGE, after).splitlines()[1:] == [
        "Removed:",
        "  [1-5] group and 2 nodes below it",
    ]
    # A long footer keeps the page bigger than the change, so the diff is returned.
    one_child = PAGE.replace("    [1-5] group\n      [1-6] button: Flash\n", "    [1-5] group\n")
    after_one = one_child.replace("    [1-5] group\n      [1-7] button: Pro\n", "")
    assert diff_snapshot(one_child, after_one).splitlines()[-1] == "  [1-5] group and 1 node below it"


def test_diff_returns_the_full_tree_when_a_removed_parents_child_moves_out():
    # The full tree shows the surviving child's new parent without marking its ID stale.
    after = PAGE.replace("    [1-5] group\n      [1-6] button: Flash\n", "").replace("      [1-7] button: Pro", "    [1-7] button: Pro")
    assert diff_snapshot(PAGE, after) is None


@pytest.mark.parametrize("before, after", [
    (
        tree("[1-1] list", "  [1-2] button: Alpha", "  [1-3] button: Beta"),
        tree("[1-1] list", "  [1-3] button: Beta", "  [1-2] button: Alpha"),
    ),
    (
        tree("[1-1] group: Available", "  [1-2] button: Item", "[1-3] group: Selected"),
        tree("[1-1] group: Available", "[1-3] group: Selected", "  [1-2] button: Item"),
    ),
    (
        tree("[1-1] group", "  [1-2] button: Item"),
        tree("[1-1] group", "  [1-3] group: Wrapper", "    [1-2] button: Item"),
    ),
    (
        tree("[1-1] group: Alpha", "[1-2] group: Beta"),
        tree("[1-2] group: Beta", "[1-1] group: Alpha"),
    ),
])
def test_diff_returns_the_full_tree_when_retained_nodes_move(before, after):
    footer = "\n[1-99] StaticText: " + "Unchanged footer text. " * 20
    assert diff_snapshot(before + footer, after + footer) is None


def test_diff_returns_the_full_tree_when_nodes_move_and_labels_change():
    # This page is long enough that a label-only diff would otherwise be returned.
    after = PAGE.replace(
        "      [1-6] button: Flash\n      [1-7] button: Pro",
        "      [1-7] button: Pro (selected)\n      [1-6] button: Flash",
    )
    assert diff_snapshot(PAGE, after) is None


def test_diff_reports_changed_names():
    after = PAGE.replace("button: Pro", "button: Pro (selected)")
    assert diff_snapshot(PAGE, after) == tree(
        "Changes since the previous snapshot. IDs not listed or counted under Removed remain valid.",
        "Added or changed:",
        "under [1-5] group",
        "  [1-7] button: Pro (selected)",
    )


def test_diff_starts_a_new_group_when_a_changed_run_moves_up_the_tree():
    after = PAGE.replace("button: Pro", "button: Pro!").replace("StaticText: A long", "StaticText: A longer")
    assert diff_snapshot(PAGE, after).splitlines()[2:] == [
        "under [1-5] group",
        "  [1-7] button: Pro!",
        "under [1-2] body",
        "  [1-8] StaticText: A longer enough footer line so that a small change stays shorter than the full tree.",
    ]


def test_diff_reports_an_unchanged_page():
    assert diff_snapshot(PAGE, PAGE) == NO_CHANGES


def test_diff_gives_way_to_the_full_tree_when_ids_do_not_carry_over():
    reloaded = PAGE.replace("[1-", "[1-9")
    assert diff_snapshot(PAGE, reloaded) is None
