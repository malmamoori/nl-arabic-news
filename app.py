from flask import Flask, abort, render_template
import feedparser

from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import calendar
import hashlib
import time


app = Flask(__name__)


def get_amsterdam_timezone():
    try:
        return ZoneInfo("Europe/Amsterdam")
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc

RSS_URL = "https://nltimes.nl/rss.xml"
NEWS_CACHE_TTL = 15 * 60
_news_cache = None
_news_cache_time = 0.0

categories = {
    "netherlands": "هولندا",
}


fallback_news = [
    {
        "title": "أخبار هولندا وتحديثات تهم المقيمين هذا الأسبوع",
        "summary": "تابع أهم الأخبار والإجراءات التي تمس الحياة اليومية في هولندا.",
        "link": "https://nltimes.nl/",
    },
    {
        "title": "آخر الأخبار من هولندا",
        "summary": "نغطي الأخبار المحلية والسياسة والاقتصاد والمجتمع في هولندا.",
        "link": "https://www.dutchnews.nl/",
    },
]


def translate_to_arabic(text):
    if not text:
        return ""

    try:
        return GoogleTranslator(
            source="auto",
            target="ar"
        ).translate(text)

    except Exception:
        return text


def format_date(item):
    try:
        published = item.get("published_parsed")
        if published:
            timestamp = calendar.timegm(published)
            date = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(
                get_amsterdam_timezone()
            )
        else:
            published_text = item.get("published") or item.get("updated")
            if not published_text:
                return "تاريخ غير متاح"
            else:
                date_parts = published_text.strip().replace(" - ", " ").split()
                if len(date_parts) != 4:
                    raise ValueError("Unsupported RSS date format")
                day, month_name, year = date_parts[:3]
                hour, minute = date_parts[3].split(":")
                month_number = {
                    "January": 1,
                    "February": 2,
                    "March": 3,
                    "April": 4,
                    "May": 5,
                    "June": 6,
                    "July": 7,
                    "August": 8,
                    "September": 9,
                    "October": 10,
                    "November": 11,
                    "December": 12,
                }.get(month_name)
                if month_number is None:
                    raise ValueError("Unknown RSS month")
                date = datetime(
                    int(year),
                    month_number,
                    int(day),
                    int(hour),
                    int(minute),
                    tzinfo=get_amsterdam_timezone(),
                )

        days = {
            0: "الاثنين",
            1: "الثلاثاء",
            2: "الأربعاء",
            3: "الخميس",
            4: "الجمعة",
            5: "السبت",
            6: "الأحد",
        }

        months = {
            1: "يناير",
            2: "فبراير",
            3: "مارس",
            4: "أبريل",
            5: "مايو",
            6: "يونيو",
            7: "يوليو",
            8: "أغسطس",
            9: "سبتمبر",
            10: "أكتوبر",
            11: "نوفمبر",
            12: "ديسمبر",
        }

        return (
            f"{days[date.weekday()]} • "
            f"{date.strftime('%H:%M')} • "
            f"{date.day} {months[date.month]} {date.year}"
        )

    except (TypeError, ValueError, KeyError):
        return "تاريخ غير متاح"


def get_article_id(item):
    source_id = (
        item.get("id")
        or item.get("guid")
        or item.get("link")
        or item.get("title")
        or "news-item"
    )
    return hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:16]


def load_news():
    global _news_cache, _news_cache_time

    now = time.monotonic()
    if _news_cache is not None and now - _news_cache_time < NEWS_CACHE_TTL:
        return _news_cache

    try:
        feed = feedparser.parse(RSS_URL)
    except Exception:
        feed = None

    entries = getattr(feed, "entries", [])[:9] or fallback_news

    accent_colors = [
        "#d95d39",
        "#287c7b",
        "#bc7a2b",
        "#5c6f9d",
    ]

    loaded_news = []

    for index, item in enumerate(entries, start=1):

        original_title = item.get(
            "title",
            "خبر من هولندا"
        )

        raw_summary = item.get(
            "summary",
            "تابع آخر التحديثات من المصادر الرسمية."
        )

        clean_summary = BeautifulSoup(
            raw_summary,
            "html.parser"
        ).get_text(" ", strip=True)

        arabic_title = translate_to_arabic(
            original_title
        )

        arabic_summary = translate_to_arabic(
            clean_summary
        )

        loaded_news.append({
            "id": get_article_id(item),
            "category": "هولندا",
            "slug": "netherlands",
            "title": arabic_title,
            "summary": arabic_summary,
            "body": arabic_summary,
            "link": item.get(
                "link",
                "https://nltimes.nl/"
            ),
            "date": format_date(item),
            
            "read_time": "3 دقائق",
            "accent": accent_colors[
                (index - 1) % len(accent_colors)
            ],
        })

    _news_cache = loaded_news
    _news_cache_time = time.monotonic()
    return _news_cache


@app.route("/")
def home():

    return render_template(
        "index.html",
        news=load_news()
    )


@app.route("/category/<slug>")
def category(slug):
    if slug not in categories:
        abort(404)

    category_news = [
        item for item in load_news()
        if item["slug"] == slug
    ]

    return render_template(
        "index.html",
        news=category_news,
    )


@app.route("/article/<article_id>")
def article(article_id):

    news = load_news()

    selected_article = next(
        (
            item
            for item in news
            if item["id"] == article_id
        ),
        None
    )

    if selected_article is None:
        abort(404)

    related = [
        item
        for item in news
        if item["id"] != article_id
    ][:2]

    return render_template(
        "article.html",
        article=selected_article,
        related=related,
        categories=categories,
    )


if __name__ == "__main__":
    app.run(debug=True)