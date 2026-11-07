"""Model-facing presentation of Stagehand accessibility trees.

Only the text changes here. The facade keeps its own ID map, so an element left
out of the text stays actionable.
"""

import re
from dataclasses import dataclass

LINE = re.compile(r"^( *)\[([^\]]+)\] (.*)$")
STATIC_TEXT = "StaticText:"
# Typing animations render one node per character. Shorter runs may be separate labels.
MIN_CHARACTER_RUN = 3
# An icon inside these adds nothing, because the control has its own ID and name.
LABELLED_CONTROLS = frozenset({"button", "checkbox", "combobox", "link", "menuitem", "menuitemcheckbox",
                               "menuitemradio", "option", "radio", "switch", "tab"})
NO_CHANGES = "No changes since the previous snapshot. All of its IDs remain valid."
CHANGES_HEADER = "Changes since the previous snapshot. IDs not listed or counted under Removed remain valid."


@dataclass
class Node:
    indent: int
    id: str
    label: str

    def line(self, indent: int | None = None) -> str:
        return f"{' ' * (self.indent if indent is None else indent)}[{self.id}] {self.label}"


def parse(tree: str) -> list[Node]:
    """Read a formatted tree; anything else gives an empty list and is left to callers as text."""
    nodes: list[Node] = []
    for line in tree.splitlines():
        match = LINE.match(line)
        if match:
            nodes.append(Node(len(match[1]), match[2], match[3]))
        elif nodes:
            # An accessible name can span lines.
            nodes[-1].label += f"\n{line}"
        else:
            return []
    return nodes


def character(node: Node) -> str | None:
    """The text of a one-character node; whitespace-only nodes are spaces between words."""
    if not node.label.startswith(STATIC_TEXT):
        return None
    text = node.label[len(STATIC_TEXT):].removeprefix(" ")
    return (text or " ") if len(text) <= 1 else None


def merge_characters(nodes: list[Node]) -> list[Node]:
    merged: list[Node] = []
    index = 0
    while index < len(nodes):
        node = nodes[index]
        run: list[str] = []
        while index + len(run) < len(nodes) and nodes[index + len(run)].indent == node.indent:
            text = character(nodes[index + len(run)])
            if text is None:
                break
            run.append(text)
        joined = "".join(run)
        if len(run) >= MIN_CHARACTER_RUN and joined.strip():
            merged.append(Node(node.indent, node.id, f"{STATIC_TEXT} {joined}"))
            index += len(run)
        else:
            merged.append(node)
            index += 1
    return merged


def role(node: Node) -> str:
    return node.label.split(":", 1)[0].rsplit(", ", 1)[-1]


def drop_control_icons(nodes: list[Node]) -> list[Node]:
    kept: list[Node] = []
    ancestors: list[Node] = []
    for index, node in enumerate(nodes):
        while ancestors and ancestors[-1].indent >= node.indent:
            ancestors.pop()
        is_leaf = index + 1 == len(nodes) or nodes[index + 1].indent <= node.indent
        if not (node.label == "image" and is_leaf and ancestors and role(ancestors[-1]) in LABELLED_CONTROLS):
            kept.append(node)
        ancestors.append(node)
    return kept


def compact_snapshot(tree: str) -> str:
    nodes = parse(tree)
    if not nodes:
        return tree
    return "\n".join(node.line() for node in drop_control_icons(merge_characters(nodes)))


def removed_lines(nodes: list[Node], live: set[str]) -> list[str]:
    """List removed nodes, printing a subtree that is gone entirely as its root and a count."""
    lines: list[str] = []
    index = 0
    while index < len(nodes):
        node = nodes[index]
        index += 1
        if node.id in live:
            continue
        end = index
        while end < len(nodes) and nodes[end].indent > node.indent:
            end += 1
        below = nodes[index:end]
        if below and not any(item.id in live for item in below):
            lines.append(f"{node.line(2)} and {len(below)} {'node' if len(below) == 1 else 'nodes'} below it")
            index = end
        else:
            lines.append(node.line(2))
    return lines


def retained_structure(nodes: list[Node], retained: set[str]) -> list[tuple[str, str | None]]:
    """Keep the traversal order and actual parent of nodes present in both trees."""
    structure: list[tuple[str, str | None]] = []
    ancestors: list[Node] = []
    for node in nodes:
        while ancestors and ancestors[-1].indent >= node.indent:
            ancestors.pop()
        if node.id in retained:
            structure.append((node.id, ancestors[-1].id if ancestors else None))
        ancestors.append(node)
    return structure


def diff_snapshot(previous: str, current: str) -> str | None:
    """Describe a tree by its changes from the previous one.

    Returns None when retained nodes move or the full tree is no longer than
    the changes, as after a reload or tab switch, where IDs do not carry over.
    """
    nodes_before = parse(previous)
    before = {node.id: node for node in nodes_before}
    after = parse(current)
    if not before or not after:
        return None
    live = {node.id for node in after}
    retained = before.keys() & live
    # The text diff cannot describe moves or positions between unchanged siblings.
    if retained_structure(nodes_before, retained) != retained_structure(after, retained):
        return None
    changed = {node.id for node in after if node.id not in before or before[node.id].label != node.label}
    removed = removed_lines(nodes_before, live)
    if not changed and not removed:
        return NO_CHANGES

    sections = [CHANGES_HEADER]
    if changed:
        sections.append("Added or changed:")
        ancestors: list[Node] = []
        base = 0
        previous_changed = False
        for node in after:
            while ancestors and ancestors[-1].indent >= node.indent:
                ancestors.pop()
            if node.id in changed:
                if not previous_changed or node.indent < base:
                    base = node.indent
                    if ancestors:
                        sections.append(f"under {ancestors[-1].line(0)}")
                sections.append(node.line(2 + node.indent - base))
            previous_changed = node.id in changed
            ancestors.append(node)
    if removed:
        sections.append("Removed:")
        sections.extend(removed)
    text = "\n".join(sections)
    return text if len(text) < len(current) else None
