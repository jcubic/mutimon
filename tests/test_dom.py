"""Tests for DOM traversal (find), transformation (transform),
html value type, and html2text Liquid filter."""

from bs4 import BeautifulSoup

from mutimon import main


# ========================= apply_find =========================


class TestApplyFind:
    def test_select(self):
        html = "<div><p>Hello</p><span>World</span></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_find(el, [["select", "span"]])
        assert result.get_text(strip=True) == "World"

    def test_select_no_match(self):
        html = "<div><p>Hello</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_find(el, [["select", "span"]])
        assert result is None

    def test_until(self):
        html = """<div>
            <h2 id="start">Title</h2>
            <p>Content 1</p>
            <p>Content 2</p>
            <div class="end"><span class="stop">Stop</span></div>
            <p>After</p>
        </div>"""
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2#start")
        result = main.apply_find(el, [["until", ".stop"]])
        assert result is not None
        texts = [p.get_text(strip=True) for p in result.select("p")]
        assert "Content 1" in texts
        assert "Content 2" in texts
        assert "After" not in texts

    def test_until_no_siblings(self):
        html = "<div><h2>Only child</h2></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2")
        result = main.apply_find(el, [["until", ".stop"]])
        assert result is None

    def test_siblings(self):
        html = """<div>
            <h2>Title</h2>
            <p>First</p>
            <p>Second</p>
            <p>Third</p>
        </div>"""
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2")
        result = main.apply_find(el, [["siblings"]])
        assert result is not None
        paragraphs = result.select("p")
        assert len(paragraphs) == 3

    def test_siblings_no_siblings(self):
        html = "<div><h2>Alone</h2></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2")
        result = main.apply_find(el, [["siblings"]])
        assert result is None

    def test_chained_steps(self):
        html = """<div>
            <h2>Title</h2>
            <div class="content"><p>First</p><span class="tag">Info</span></div>
            <div class="footer">End</div>
        </div>"""
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2")
        result = main.apply_find(el, [["siblings"], ["select", ".tag"]])
        assert result is not None
        assert result.get_text(strip=True) == "Info"

    def test_none_propagation(self):
        result = main.apply_find(None, [["select", "div"]])
        assert result is None

    def test_none_mid_chain(self):
        html = "<div><p>Hello</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_find(el, [["select", ".missing"], ["select", "p"]])
        assert result is None


# ========================= apply_transform =========================


class TestApplyTransform:
    def test_remove(self):
        html = "<div><p>Keep</p><span class='ad'>Remove</span><p>Also keep</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_transform(el, [["remove", ".ad"]])
        assert "Remove" not in result.get_text()
        assert "Keep" in result.get_text()
        assert "Also keep" in result.get_text()

    def test_remove_multiple(self):
        html = "<div><span class='x'>A</span><p>B</p><span class='x'>C</span></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_transform(el, [["remove", ".x"]])
        assert result.get_text(strip=True) == "B"

    def test_remove_after(self):
        html = "<div><p>Keep</p><span class='sig'>Sig</span><p>After sig</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_transform(el, [["remove_after", ".sig"]])
        assert "Keep" in result.get_text()
        assert "Sig" not in result.get_text()
        assert "After sig" not in result.get_text()

    def test_remove_after_no_match(self):
        html = "<div><p>Keep</p><p>Also keep</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_transform(el, [["remove_after", ".missing"]])
        assert "Keep" in result.get_text()
        assert "Also keep" in result.get_text()

    def test_does_not_modify_original(self):
        html = "<div><p>Keep</p><span class='rm'>Remove</span></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        main.apply_transform(el, [["remove", ".rm"]])
        assert el.select_one(".rm") is not None

    def test_chained_transforms(self):
        html = "<div><span class='a'>A</span><p>B</p><span class='sig'>S</span><p>C</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("div")
        result = main.apply_transform(el, [["remove", ".a"], ["remove_after", ".sig"]])
        assert result.get_text(strip=True) == "B"


# ========================= extract_value type html =========================


class TestExtractValueHtml:
    def test_html_type(self):
        html = "<div><p>Hello <b>world</b></p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("p")
        result = main.extract_value(el, {"type": "html"})
        assert "<b>world</b>" in result
        assert "Hello" in result

    def test_html_empty_returns_default(self):
        html = "<div><p></p></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("p")
        result = main.extract_value(el, {"type": "html"}, default="fallback")
        assert result == "fallback"


# ========================= liquid_html2text filter =========================


class TestLiquidHtml2Text:
    def test_basic_conversion(self):
        result = main.liquid_html2text("<p>Hello <b>world</b></p>")
        assert "Hello" in result
        assert "world" in result

    def test_preserves_code_blocks(self):
        result = main.liquid_html2text("<pre><code>x = 1</code></pre>")
        assert "x = 1" in result

    def test_strips_tags(self):
        result = main.liquid_html2text("<div><span>text</span></div>")
        assert "<" not in result
        assert "text" in result

    def test_registered_as_filter(self):
        config = {"defs": {}}
        main.setup_liquid(config)
        tpl = main.liquid.from_string("{{ content | html2text }}")
        result = tpl.render(content="<p>Hello</p>")
        assert "Hello" in result


# ========================= extract_variables with find/transform =========================


class TestExtractVariablesWithFind:
    def test_find_with_value(self):
        html = """<div>
            <h2 class="item">Title</h2>
            <p class="desc">Description</p>
            <div class="end"><span class="stop">End</span></div>
        </div>"""
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2.item")
        variables = {
            "content": {
                "selector": "p",
                "find": [["until", ".stop"]],
                "value": {"type": "text"},
            }
        }
        data = main.extract_variables(el, variables)
        assert data["content"] == "Description End"

    def test_find_returns_default_on_miss(self):
        html = "<div><h2>Title</h2></div>"
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2")
        variables = {
            "content": {
                "selector": "p",
                "find": [["until", ".missing"]],
                "value": {"type": "text"},
                "default": "N/A",
            }
        }
        data = main.extract_variables(el, variables)
        assert data["content"] == "N/A"

    def test_find_with_transform(self):
        html = """<div>
            <h2>Title</h2>
            <p>Content</p>
            <span class="sig">Signature</span>
            <p>After</p>
            <div class="end"><span class="stop">End</span></div>
        </div>"""
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2")
        variables = {
            "content": {
                "selector": "p",
                "find": [["until", ".stop"]],
                "transform": [["remove_after", ".sig"]],
                "value": {"type": "html"},
            }
        }
        data = main.extract_variables(el, variables)
        assert "Content" in data["content"]
        assert "Signature" not in data["content"]

    def test_find_with_html_type(self):
        html = """<div>
            <h2>Title</h2>
            <p>Hello <b>bold</b></p>
            <div class="stop">End</div>
        </div>"""
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("h2")
        variables = {
            "body": {
                "selector": "p",
                "find": [["until", ".stop"]],
                "value": {"type": "html"},
            }
        }
        data = main.extract_variables(el, variables)
        assert "<b>bold</b>" in data["body"]
