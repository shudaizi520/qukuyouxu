import unittest
from html.parser import HTMLParser
from pathlib import Path

from ui_css import page_css


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []
        self.text = ""

    @property
    def classes(self):
        return set(self.attrs.get("class", "").split())

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def content(self):
        return (self.text + "".join(child.content() for child in self.children)).strip()


class Tree(HTMLParser):
    VOID = {"meta", "link", "input", "br", "img"}

    def __init__(self):
        super().__init__()
        self.root = Node("root", [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].text += data


class SmartMixSimplificationV0429Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "mixes.html").read_text(encoding="utf-8")
        cls.script = (STATIC / "mixes.js").read_text(encoding="utf-8")
        cls.tree = Tree()
        cls.tree.feed(cls.html)

    def test_each_builtin_row_has_four_fixed_actions(self):
        rows = [node for node in self.tree.root.descendants() if "mix-row" in node.classes]
        self.assertEqual(3, len(rows))
        for row in rows:
            head = next(node for node in row.descendants() if "mix-row-head" in node.classes)
            actions = next(node for node in head.descendants() if "mix-actions" in node.classes)
            buttons = [node for node in actions.descendants() if node.tag == "button"]
            self.assertEqual(["查看", "发布", "调整", "删除"], [node.content() for node in buttons])

    def test_preview_drawer_has_only_one_refresh_icon(self):
        rows = [node for node in self.tree.root.descendants() if "mix-row" in node.classes]
        for row in rows:
            preview = next(node for node in row.descendants() if "mix-preview-panel" in node.classes)
            buttons = [node for node in preview.descendants() if node.tag == "button"]
            self.assertEqual(1, len(buttons))
            self.assertIn("regenerate-mix", buttons[0].classes)
            self.assertEqual("刷新预览", buttons[0].attrs.get("aria-label"))

    def test_pending_refresh_rotates_its_only_icon_without_adding_a_spinner(self):
        styles = page_css("mixes")
        refresh_buttons = [
            node
            for node in self.tree.root.descendants()
            if node.tag == "button" and "mix-refresh" in node.classes
        ]
        self.assertEqual(4, len(refresh_buttons))
        for button in refresh_buttons:
            icons = [node for node in button.descendants() if node.tag == "svg"]
            self.assertEqual(1, len(icons))
        self.assertIn(".mix-refresh.pch-pending:before{content:none}", styles)
        self.assertIn(".mix-refresh.pch-pending svg{animation:pch-spin", styles)

    def test_ui_uses_only_pending_or_published_states(self):
        source = self.html + self.script
        for obsolete in ("恢复旧歌单", "重新创建", "待重新创建", "已删除", "未发布", "待更新"):
            self.assertNotIn(obsolete, source)
        self.assertIn("待发布", source)
        self.assertIn("已发布", source)


if __name__ == "__main__":
    unittest.main()
