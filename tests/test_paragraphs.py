from voice2text.segments import Segment, Paragraph
from voice2text.paragraphs import drop_repeats, build_paragraphs

S = Segment

def test_drop_repeats_removes_consecutive_duplicates_ignoring_case_and_punctuation():
    segs = [S(0, 1, "Děkuji."), S(1, 2, "děkuji"), S(2, 3, "Děkuji!"), S(3, 4, "Dobře.")]
    assert [s.text for s in drop_repeats(segs)] == ["Děkuji.", "Dobře."]

def test_drop_repeats_keeps_non_consecutive_duplicates_and_drops_empty():
    segs = [S(0, 1, "Ano."), S(1, 2, "  "), S(2, 3, "Ne."), S(3, 4, "Ano.")]
    assert [s.text for s in drop_repeats(segs)] == ["Ano.", "Ne.", "Ano."]

def test_short_gap_joins_segments_into_one_paragraph():
    segs = [S(0, 2, " Dobrý den."), S(2.5, 4, "Začneme. ")]
    assert build_paragraphs(segs) == [Paragraph("Dobrý den. Začneme.", None)]

def test_pause_of_two_seconds_starts_new_paragraph():
    segs = [S(0, 2, "První."), S(4.0, 5, "Druhý.")]
    assert [p.text for p in build_paragraphs(segs)] == ["První.", "Druhý."]

def test_long_paragraph_breaks_only_after_sentence_end():
    long = "slovo " * 130  # ~780 chars, no sentence end
    segs = [S(0, 1, long.strip()), S(1.1, 2, "pokračuje."), S(2.2, 3, "Nová věta.")]
    paras = build_paragraphs(segs, max_chars=700)
    assert len(paras) == 2
    assert paras[0].text.endswith("pokračuje.")
    assert paras[1].text == "Nová věta."

def test_speaker_change_starts_new_paragraph_and_keeps_speaker():
    segs = [S(0, 1, "Ahoj.", 1), S(1.2, 2, "Čau.", 2), S(2.2, 3, "Jak je?", 2)]
    assert build_paragraphs(segs) == [Paragraph("Ahoj.", 1), Paragraph("Čau. Jak je?", 2)]

def test_empty_input_gives_no_paragraphs():
    assert build_paragraphs([]) == []
