# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import struct
import zlib

from exam_forge.bank import Bank
from exam_forge.model import Item, Option, SourceLink
from exam_forge.question_document import questions_html
from exam_forge.topics import add_topic, assign_slide


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))


def test_every_question_with_its_answers_in_deck_order(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    image = bank.store_media("cells.png", png(), alt="Two blue cells")
    gone = bank.store_media("gone.png", png() + b"x", alt="Lost image")
    (bank.folder / "media" / gone.name).unlink()
    topics = add_topic((), "Probability")
    topics = assign_slide(topics, topics[0].id, "b")
    items = [
        Item("late", 1, "On <c>", (Option("x", "Yes & no", True, "Because"), Option("y", "Never", False, "")),
             sources=(SourceLink(slide_id="c"),), media=(image, gone), objective="Read fractions"),
        Item("early", 1, "Explain Bayes.", use="elp", kind="open", difficulty="hard", status="reviewed",
             model_answer="Posterior ∝ likelihood × prior", grading_notes="Two points", assistant_note="Shared stem",
             sources=(SourceLink(slide_id="b"),)),
        Item("loose", 1, "Nowhere", status="retired"),
    ]
    html = questions_html(items, topics, {"b": (2, "Bayes"), "c": (3, "Cells")}, bank.read_media, "de")
    assert html.index("Explain Bayes.") < html.index("On &lt;c&gt;") < html.index("Nowhere")
    assert '<html lang="de">' in html and "3 questions: 1 reviewed, 1 draft, 1 retired." in html
    chips = ['Reviewed', 'Exam', 'Open answer', 'Hard', 'Probability', 'Slide 2', 'Bayes']
    assert "".join(f'<span class="chip{" reviewed" if n == 0 else ""}">{c}</span>' for n, c in enumerate(chips)) in html
    assert "Posterior ∝ likelihood × prior" in html and "Two points" in html and "Shared stem" in html
    assert '<li class="ok"><span class="mk" aria-label="Correct">✓</span><div><div class="text option">Yes &amp; no' in html
    assert '<li class="no"><span class="mk" aria-label="Incorrect">✗</span><div><div class="text option">Never' in html
    assert "Because" in html and "Learning objective: Read fractions" in html
    assert html.count("data:image/png;base64,") == 1 and f"Image not available: {gone.name} — Lost image" in html
    assert "No slide linked" in html and "<script" not in html


def test_each_answer_has_one_marker_that_matches_it_and_the_heading_is_short():
    import re
    items = [Item("0123456789abcdef", 1, "Q", (Option("x", "A", True), Option("y", "B", False), Option("z", "C", False)))]
    html = questions_html(items)
    marks = re.findall(r'<li class="(ok|no)"><span class="mk" aria-label="(Correct|Incorrect)">', html)
    assert marks == [("ok", "Correct"), ("no", "Incorrect"), ("no", "Incorrect")]
    assert html.count('class="mk"') == 3 and "Correct</p>" not in html
    heading = re.search(r"<h2>.*?</h2>", html).group(0)
    assert "01234567 · r1" in heading and "0123456789abcdef" not in re.sub(r'title="[^"]*"', "", heading)
