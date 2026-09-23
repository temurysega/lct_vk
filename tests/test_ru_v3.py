from training.prepare_ru_v3 import (
    build_duplicates,
    is_code,
    is_russian,
    is_url_like,
    on_canvas,
    pick_content,
    rejection,
    role_for,
)

MEDIA = {"hidden": False, "picture_ratio": 0.0, "chart_ratio": 0.0, "drawings": 0}


def element(text, top, size=0, placeholder=""):
    return {
        "top": top,
        "left": 0.5,
        "width": 8,
        "height": 0.5,
        "placeholder_type": placeholder,
        "paragraphs": [{"text": text, "font": {"size_pt": size}}],
    }


def test_short_heading_above_a_paragraph_becomes_the_title():
    paragraph = "В ClickHouse таблица разделена на несколько партиций. " * 3
    slide = {
        "text_elements": [
            element("Архитектура ClickHouse", 0.4, 14),
            element(paragraph, 1.2, 24),
        ]
    }
    title, bullets, corrected = pick_content(slide, 7.5, set())
    assert title == "Архитектура ClickHouse" and corrected
    assert bullets == [paragraph.strip()]


def test_running_labels_are_not_statements():
    slide = {
        "text_elements": [
            element("BI Tableau over ClickHouse", 0.5, 12),
            element("Как это работает", 2.5, 32),
        ]
    }
    title, bullets, _ = pick_content(slide, 7.5, set(), {"bi tableau over clickhouse"})
    assert title == "Как это работает" and bullets == []
    assert role_for(["section", "content"], bullets) == "section"


def test_code_urls_and_language_rules():
    assert is_code("SELECT Referer, count(*) FROM hits")
    assert is_code("pod/chi-demo-01 1/1 Running")
    assert not is_code("Апрель 2017:")
    assert is_url_like("https://github.com/yandex/clickhouse-odbc/releases/latest")
    assert not is_url_like("Кросс-ДЦ репликация")
    assert is_russian("Заголовок", [], "section")
    assert is_russian("VK Tech", ["Разработчик ПО"], "cover", brand=True)
    assert not is_russian(
        "CloudFlare (США)", ["Kafka + Go + Citus", "Spark Streaming"], "content"
    )


def test_picture_driven_and_hidden_slides_are_rejected():
    screenshot = {**MEDIA, "picture_ratio": 0.29}
    assert (
        rejection(
            "Как это выглядит",
            ["Карты", "Боксплоты", "Пайчарты"],
            "content",
            screenshot,
        )
        == "picture_or_chart_driven"
    )
    assert (
        rejection(
            "Цель",
            [
                "Оперативное принятие взвешенных решений, основанных на данных",
                "Доступ к отчётам для всех подразделений компании",
                "Единая точка правды для метрик и справочников",
            ],
            "content",
            screenshot,
        )
        is None
    )
    assert (
        rejection("Цель", ["Пункт"], "content", {**MEDIA, "hidden": True})
        == "hidden_slide"
    )


def test_only_neighbouring_builds_are_duplicates():
    def row(slide, bullets):
        return {
            "source_slide": slide,
            "slide": {"title": "Как выполнить запрос быстро?", "bullets": bullets},
        }

    rows = [
        row(6, ["Быстро читаем"]),
        row(7, ["Быстро читаем", "Сжатие"]),
        row(20, ["Быстро читаем"]),
    ]
    assert build_duplicates(rows) == {0}


def test_text_parked_off_the_canvas_is_ignored():
    parked = {**element("vkontakte", -3.4), "left": 2.0}
    assert not on_canvas(parked, 13.33, 7.5)
    assert on_canvas(element("Спасибо! Вопросы?", 2.4), 13.33, 7.5)
