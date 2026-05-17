"""Tests for CJK tokenization (Issue #8)."""

from __future__ import annotations

from infomesh.search.cjk import (
    cjk_bigrams,
    cjk_trigrams,
    is_cjk_text,
    recommend_tokenizer,
    segment_korean,
    tokenize_query_cjk,
)


class TestIsCJKText:
    def test_pure_chinese(self) -> None:
        assert is_cjk_text("这是一个测试文本关于中文分词")

    def test_pure_korean(self) -> None:
        assert is_cjk_text("한국어 텍스트 테스트")

    def test_pure_english(self) -> None:
        assert not is_cjk_text("this is pure english text")

    def test_mixed_below_threshold(self) -> None:
        # Mostly English with a few CJK chars
        assert not is_cjk_text("hello world 你好", threshold=0.5)

    def test_empty(self) -> None:
        assert not is_cjk_text("")


class TestCJKBigrams:
    def test_chinese_bigrams(self) -> None:
        result = cjk_bigrams("中文测试")
        assert result == ["中文", "文测", "测试"]

    def test_single_char(self) -> None:
        result = cjk_bigrams("中")
        assert result == ["中"]

    def test_mixed_text(self) -> None:
        result = cjk_bigrams("hello中文world")
        assert "hello" in result
        assert "中文" in result
        assert "world" in result

    def test_korean_bigrams(self) -> None:
        result = cjk_bigrams("테스트")
        assert "테스" in result
        assert "스트" in result


class TestCJKTrigrams:
    def test_trigrams(self) -> None:
        result = cjk_trigrams("中文测试句")
        assert "中文测" in result
        assert "文测试" in result
        assert "测试句" in result

    def test_short_text(self) -> None:
        result = cjk_trigrams("中文")
        assert result == ["中文"]


class TestRecommendTokenizer:
    def test_cjk_text(self) -> None:
        assert recommend_tokenizer("这是中文内容需要特殊分词处理") == "trigram"

    def test_english_text(self) -> None:
        assert recommend_tokenizer("this is english content") == "unicode61"


class TestTokenizeQueryCJK:
    def test_cjk_query(self) -> None:
        result = tokenize_query_cjk("中文搜索")
        # Should contain bigrams
        assert "中文" in result
        assert "搜索" in result

    def test_english_query_passthrough(self) -> None:
        result = tokenize_query_cjk("python sort list")
        assert result == "python sort list"


class TestSegmentKorean:
    def test_short_word(self) -> None:
        result = segment_korean("테스트")
        assert "테스트" in result

    def test_long_word(self) -> None:
        result = segment_korean("프로그래밍언어")
        assert len(result) > 1

    def test_mixed(self) -> None:
        result = segment_korean("Python 프로그래밍")
        assert any("Python" in t for t in result)


class TestThaiSupport:
    """Test Thai language support in CJK tokenizer."""

    def test_is_cjk_text_thai(self) -> None:
        assert is_cjk_text("ข้อผิดพลาดในการติดตั้ง")

    def test_thai_bigrams(self) -> None:
        result = cjk_bigrams("ค้นหา")
        assert len(result) > 1

    def test_thai_mixed(self) -> None:
        result = cjk_bigrams("Python ค้นหา")
        assert any("Python" in t for t in result)
        assert len(result) > 1


class TestArabicDetection:
    """Test Arabic script detection."""

    def test_arabic_not_cjk(self) -> None:
        # Arabic should not be detected as CJK
        assert not is_cjk_text("مرحبا بالعالم", threshold=0.5)

    def test_arabic_lang_detect(self) -> None:
        from infomesh.crawler.lang_detect import detect_language

        result = detect_language("هذا نص باللغة العربية للاختبار")
        assert result.language == "ar"
        assert result.script == "Arabic"

    def test_hindi_lang_detect(self) -> None:
        from infomesh.crawler.lang_detect import detect_language

        result = detect_language("यह हिंदी में एक परीक्षण पाठ है")
        assert result.language == "hi"
        assert result.script == "Devanagari"

    def test_thai_lang_detect(self) -> None:
        from infomesh.crawler.lang_detect import detect_language

        result = detect_language("นี่คือข้อความทดสอบภาษาไทย")
        assert result.language == "th"
        assert result.script == "Thai"


class TestNewStopWords:
    """Test stop words for newly added languages."""

    def test_arabic_stop_words(self) -> None:
        from infomesh.search.nlp import get_stop_words

        sw = get_stop_words("ar")
        assert "في" in sw
        assert "من" in sw

    def test_hindi_stop_words(self) -> None:
        from infomesh.search.nlp import get_stop_words

        sw = get_stop_words("hi")
        assert "है" in sw
        assert "और" in sw

    def test_thai_stop_words(self) -> None:
        from infomesh.search.nlp import get_stop_words

        sw = get_stop_words("th")
        assert "ที่" in sw

    def test_turkish_stop_words(self) -> None:
        from infomesh.search.nlp import get_stop_words

        sw = get_stop_words("tr")
        assert "bir" in sw
        assert "ve" in sw

    def test_vietnamese_stop_words(self) -> None:
        from infomesh.search.nlp import get_stop_words

        sw = get_stop_words("vi")
        assert "của" in sw

    def test_indonesian_stop_words(self) -> None:
        from infomesh.search.nlp import get_stop_words

        sw = get_stop_words("id")
        assert "dan" in sw
        assert "yang" in sw
