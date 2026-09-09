from flask import Flask, abort, make_response, render_template, request, url_for
import feedparser

from bs4 import BeautifulSoup
import requests
from news_translation import HTTP_TIMEOUT, NewsTranslator

from collections import OrderedDict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache
from threading import Lock
from urllib.parse import urlencode, urljoin, urlsplit
import calendar
import hashlib
import json
import os
import re
import textwrap
import time
import xml.etree.ElementTree as ET


app = Flask(__name__)
app.config["SITE_MANAGER"] = os.getenv("SITE_MANAGER", "فريق التحرير في NL بالعربي")
app.config["CONTACT_EMAIL"] = os.getenv("CONTACT_EMAIL", "").strip()
app.config["SITE_URL"] = os.getenv("SITE_URL", "https://nl-arabic-news.onrender.com").rstrip("/")


def site_absolute_url(path="/"):
    """Build canonical production URLs without depending on the current host."""
    return f"{app.config['SITE_URL']}/{path.lstrip('/')}"


def seo_context(title, description, canonical, page_type="website", image=None,
                published_time=None, structured_data=None):
    return {
        "seo_title": title,
        "seo_description": description,
        "seo_canonical": canonical,
        "seo_type": page_type,
        "seo_image": image,
        "seo_published_time": published_time,
        "seo_jsonld": structured_data,
    }


def get_amsterdam_timezone():
    try:
        return ZoneInfo("Europe/Amsterdam")
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc

RSS_SOURCES = [
    {
        "name": "NL Times",
        "url": "https://nltimes.nl/rss.xml",
        "language": "en",
    },
    {
        "name": "Rijksoverheid",
        "url": "https://www.rijksoverheid.nl/api/rss?" + urlencode({
            "query": json.dumps({
                "filters": [{"field": "content_type", "values": ["pro:newsDocument"], "type": "all"}],
                "resultSearchTerm": "",
                "pageTitle": "Nieuws",
            }, separators=(",", ":")),
        }),
        "language": "nl",
    },
    {
        "name": "DutchNews",
        "url": "https://www.dutchnews.nl/feed/",
        "language": "en",
    },
    {
        "name": "DW Germany",
        "url": "https://rss.dw.com/rdf/rss-en-all",
        "language": "en",
        "category": "ألمانيا",
        "slug": "germany",
    },
    {
        "name": "BBC Europe",
        "url": "https://feeds.bbci.co.uk/news/world/europe/rss.xml",
        "language": "en",
        "category": "أوروبا",
        "slug": "europe",
    },
]

# Keep a small, balanced archive in memory.  The home page deliberately shows
# only one digest of nine stories; older loaded stories remain available in the
# archive rather than making the front page overwhelming.
NEWS_LIMIT = 15
HOME_PAGE_SIZE = 9
NEWS_CACHE_TTL = 15 * 60
NEWS_RETRY_TTL = 60
ARTICLE_CACHE_LIMIT = 150
_news_cache = None
_news_cache_time = 0.0
_news_cache_sources = None
_news_cache_has_errors = False
_news_status = ""
_source_cache = {}
_article_cache = OrderedDict()
_news_refresh_lock = Lock()
_translation_retry_after = 0.0

categories = {
    "netherlands": "هولندا",
    "germany": "ألمانيا",
    "europe": "أوروبا",
}


fallback_news = [
    {
        "title": "أخبار هولندا وتحديثات تهم المقيمين هذا الأسبوع",
        "summary": "تابع أهم الأخبار والإجراءات التي تمس الحياة اليومية في هولندا.",
        "link": "https://nltimes.nl/",
        "source_name": "NL Times",
    },
    {
        "title": "آخر الأخبار من هولندا",
        "summary": "نغطي الأخبار المحلية والسياسة والاقتصاد والمجتمع في هولندا.",
        "link": "https://www.dutchnews.nl/",
        "source_name": "DutchNews",
    },
]


def clean_news_text(value):
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).replace("\ufffd", "").split())


def is_arabic_text(text):
    letters = [character for character in text if character.isalpha()]
    arabic = sum("\u0600" <= character <= "\u06ff" for character in letters)
    return bool(letters) and arabic / len(letters) >= 0.35


@lru_cache(maxsize=256)
def cached_arabic_translation(text, source_language):
    global _translation_retry_after
    if time.monotonic() < _translation_retry_after:
        raise ValueError("Translation service is temporarily unavailable")
    if source_language == "en":
        # English reporting often leaves this Dutch term untranslated.
        text = re.sub(r"\binburgering\b", "civic integration", text, flags=re.IGNORECASE)
    text = textwrap.shorten(text, width=4900, placeholder="…")
    for language in dict.fromkeys((source_language, "auto")):
        try:
            result = clean_news_text(NewsTranslator(
                source=language, target="ar"
            ).translate(text))
            if result and is_arabic_text(result):
                return result
        except requests.RequestException:
            _translation_retry_after = time.monotonic() + NEWS_RETRY_TTL
            raise
        except Exception:
            continue
    raise ValueError("No usable Arabic translation")


def translate_to_arabic(text, source_language="auto"):
    text = clean_news_text(text)
    if not text:
        return ""
    if is_arabic_text(text):
        return text
    try:
        # Keep below the translation service's character limit.
        return cached_arabic_translation(
            textwrap.shorten(text, width=4900, placeholder="…"), source_language
        )
    except Exception:
        # Failures are not cached, so the next news refresh can retry.
        return None


def metadata_objects(value):
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def safe_news_url(value, base_url=""):
    if not isinstance(value, str) or not value.strip():
        return None
    if any(ord(character) < 32 for character in value):
        return None
    try:
        url = urljoin(base_url, value.strip())
        parsed = urlsplit(url)
        if (parsed.scheme in ("https", "http") and parsed.hostname
                and not parsed.username and not parsed.password):
            parsed.port  # Validate malformed port numbers as well.
            return url
    except (ValueError, TypeError):
        pass
    return None


def get_news_image(item):
    base_url = safe_news_url(item.get("link")) or safe_news_url(item.get("source_url")) or ""

    def image_url(value):
        return safe_news_url(value, base_url)

    for field in ("media_content", "media_thumbnail", "enclosures"):
        for media in metadata_objects(item.get(field)):
            media_type = media.get("type") or ""
            if not isinstance(media_type, str):
                continue
            media_type = media_type.lower()
            if media_type and not media_type.startswith("image/"):
                continue
            if media.get("medium") not in (None, "image"):
                continue
            url = image_url(media.get("url") or media.get("href"))
            if field == "enclosures" and not media_type and url:
                if not urlsplit(url).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
                    continue
            if url:
                return url

    for link in metadata_objects(item.get("links")):
        link_type = str(link.get("type") or "").lower()
        if link_type.startswith("image/"):
            url = image_url(link.get("href") or link.get("url"))
            if url:
                return url

    entry_image = item.get("image")
    if isinstance(entry_image, Mapping):
        url = image_url(entry_image.get("href") or entry_image.get("url"))
        if url:
            return url

    html_blocks = [item.get("summary", ""), item.get("description", "")]
    html_blocks.extend(
        content.get("value", "") for content in metadata_objects(item.get("content"))
    )
    for html in html_blocks:
        if not isinstance(html, str):
            continue
        for image in BeautifulSoup(html, "html.parser").find_all("img"):
            if any(str(image.get(dimension, "")) in ("0", "1") for dimension in ("width", "height")):
                continue
            url = image_url(image.get("data-src")) or image_url(image.get("src"))
            if url:
                return url
    return None


def get_alert_topics(*texts):
    """Classify a story for the reader's optional, local alert preferences."""
    haystack = " ".join(text for text in texts if isinstance(text, str)).casefold()
    topics = {
        "transport": (
            "strike", "strikes", "rail", "train", "public transport", "ns ",
            "ov ", "staking", "trein", "vervoer", "إضراب", "قطار", "النقل",
        ),
        "weather": (
            "weather", "storm", "heavy rain", "wind warning", "code orange",
            "code red", "weer", "storm", "waarschuwing", "طقس", "عاصفة",
            "أمطار", "تحذير جوي",
        ),
        "residency": (
            "residence", "residency", "asylum", "immigration", "inburgering",
            "visa", "permit", "verblijf", "asiel", "immigratie", "إقامة",
            "لجوء", "اندماج", "تأشيرة",
        ),
        "government": (
            "government", "cabinet", "parliament", "ministry", "rijksoverheid",
            "new law", "laws", "wet", "wetsvoorstel", "حكومة", "قانون",
            "برلمان", "وزارة",
        ),
    }
    return [topic for topic, keywords in topics.items() if any(word in haystack for word in keywords)]


def select_news_entries(entries):
    # Choose fairly from each source, then present the selection newest first.
    by_source = {}
    seen_titles = set()
    for item in sorted(entries, key=get_news_timestamp, reverse=True):
        title = clean_news_text(item.get("title")).casefold()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        by_source.setdefault(item.get("source_name", ""), []).append(item)

    selected = []
    while by_source and len(selected) < NEWS_LIMIT:
        for source in list(by_source):
            selected.append(by_source[source].pop(0))
            if not by_source[source]:
                del by_source[source]
            if len(selected) == NEWS_LIMIT:
                break
    return sorted(selected, key=get_news_timestamp, reverse=True)


def get_news_datetime(item):
    for field in ("published", "updated"):
        parsed_field = f"{field}_parsed"
        published = item.get(parsed_field) if parsed_field in item else None
        if published:
            try:
                return datetime.fromtimestamp(
                    calendar.timegm(published), tz=timezone.utc
                )
            except (TypeError, ValueError, OverflowError, OSError, IndexError):
                pass

        # NL Times uses Amsterdam local time that feedparser cannot parse.
        published_text = item.get(field) if field in item else None
        if not isinstance(published_text, str):
            continue

        try:
            day, month_name, year, clock = (
                published_text.strip().replace(" - ", " ").split()
            )
            hour, minute = clock.split(":")
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
            }[month_name]
            return datetime(
                int(year), month_number, int(day), int(hour), int(minute),
                tzinfo=get_amsterdam_timezone(),
            )
        except (TypeError, ValueError, KeyError, OverflowError):
            continue

    return None


def format_date(item):
    try:
        date = get_news_datetime(item)
        if date is None:
            return "تاريخ غير متاح"
        date = date.astimezone(get_amsterdam_timezone())

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

    except (TypeError, ValueError, KeyError, OverflowError, OSError):
        return "تاريخ غير متاح"


def get_article_id(item):
    source_name = item.get("source_name", "NL Times")
    item_identity = (
        item.get("id")
        or item.get("guid")
        or item.get("link")
        or item.get("title")
        or "news-item"
    )
    source_id = f"{source_name}:{item_identity}"
    return hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:16]


def get_news_timestamp(item):
    date = get_news_datetime(item)
    return date.timestamp() if date is not None else 0


def news_source_key():
    return (NEWS_LIMIT, tuple(
        (source["name"], source["url"], source.get("language", "auto"))
        for source in RSS_SOURCES
    ))


def news_cache_is_fresh(source_key):
    ttl = NEWS_RETRY_TTL if _news_cache_has_errors else NEWS_CACHE_TTL
    return (
        _news_cache is not None
        and source_key == _news_cache_sources
        and time.monotonic() - _news_cache_time < ttl
    )


def fetch_news(url):
    with requests.get(
        url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "NLArabicNews/1.0"}
    ) as response:
        response.raise_for_status()
        return feedparser.parse(response.content, response_headers=dict(response.headers))


def fetch_source_entries(source):
    try:
        feed = fetch_news(source["url"])
        items = metadata_objects(getattr(feed, "entries", None))
        if items:
            return [dict(item) for item in items if clean_news_text(item.get("title"))] or None
    except Exception as error:
        app.logger.warning("Could not refresh %s: %s", source["name"], type(error).__name__)
    return None


def load_news():
    source_key = news_source_key()
    if news_cache_is_fresh(source_key):
        return _news_cache

    acquired = _news_refresh_lock.acquire(blocking=False)
    if not acquired:
        if _news_cache is not None and source_key == _news_cache_sources:
            return _news_cache
        _news_refresh_lock.acquire()
    try:
        if news_cache_is_fresh(source_key):
            return _news_cache
        return refresh_news(source_key)
    finally:
        _news_refresh_lock.release()


def refresh_news(source_key):
    global _news_cache, _news_cache_time, _news_cache_sources
    global _news_cache_has_errors, _news_status

    entries = []
    failed_sources = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(fetch_source_entries, RSS_SOURCES))
    for source, items in zip(RSS_SOURCES, results):
        if items is None:
            failed_sources.append(source["name"])
            items = _source_cache.get(source["url"], [])
        else:
            _source_cache[source["url"]] = items
        for item in items:
            entries.append(dict(item, source_name=source["name"], source_url=source["url"],
                                source_language=source.get("language", "auto"),
                                category=source.get("category", "هولندا"),
                                slug=source.get("slug", "netherlands")))

    using_fallback = not entries
    entries = select_news_entries(entries) or fallback_news
    with ThreadPoolExecutor(max_workers=4) as executor:
        loaded_news = list(executor.map(build_news_item, entries, range(len(entries))))

    if using_fallback:
        _news_status = "تعذّر تحميل الأخبار حاليًا. نعرض روابط عامة للمصادر حتى يعود التحديث."
    elif failed_sources:
        _news_status = "تعذّر تحديث بعض المصادر؛ نعرض آخر الأخبار المتاحة وسنحاول مجددًا قريبًا."
    else:
        _news_status = ""

    if not using_fallback:
        for item in loaded_news:
            _article_cache[item["id"]] = item
            _article_cache.move_to_end(item["id"])
        while len(_article_cache) > ARTICLE_CACHE_LIMIT:
            _article_cache.popitem(last=False)

    _news_cache = loaded_news
    _news_cache_time = time.monotonic()
    _news_cache_sources = source_key
    _news_cache_has_errors = bool(failed_sources) or any(item["translation_notice"] for item in loaded_news)
    return _news_cache


def build_news_item(item, index):
    original_title = clean_news_text(item.get("title")) or "خبر من هولندا"
    clean_summary = clean_news_text(item.get("summary"))
    if not clean_summary:
        clean_summary = next((
            text for content in metadata_objects(item.get("content"))
            if (text := clean_news_text(content.get("value")))
        ), "")
    clean_summary = textwrap.shorten(clean_summary, width=1800, placeholder="…")
    language = item.get("source_language", "auto")
    arabic_title = translate_to_arabic(original_title, source_language=language)
    arabic_summary = translate_to_arabic(clean_summary, source_language=language)
    title_failed = not arabic_title
    summary_failed = bool(clean_summary) and not arabic_summary
    if title_failed and summary_failed:
        translation_notice = "الترجمة غير متاحة حاليًا؛ العنوان بلغته الأصلية."
    elif title_failed:
        translation_notice = "العنوان بلغته الأصلية؛ تعذّرت ترجمته حاليًا."
    elif summary_failed:
        translation_notice = "تعذّرت ترجمة الملخص حاليًا."
    else:
        translation_notice = ""
    summary = arabic_summary or (
        "يمكنك متابعة التفاصيل من رابط المصدر الأصلي، أو الاطلاع على النص الأصلي داخل الخبر."
        if clean_summary else "لم يوفّر المصدر ملخصًا لهذا الخبر. اقرأ التفاصيل من المصدر الأصلي."
    )
    source_url = safe_news_url(item.get("source_url")) or "https://nltimes.nl/"
    link = safe_news_url(item.get("link"), source_url) or urljoin(source_url, "/")
    published_datetime = get_news_datetime(item)
    displayed_title = arabic_title or original_title
    displayed_body = summary
    source_language = language if language in ("en", "nl") else ""
    has_original_variant = bool(source_language and (
        original_title.casefold() != displayed_title.casefold()
        or (clean_summary and clean_summary.casefold() != displayed_body.casefold())
    ))
    return {
        "id": get_article_id(item),
        "category": item.get("category", "هولندا"),
        "slug": item.get("slug", "netherlands"),
        "title": displayed_title,
        "original_title": original_title,
        "original_summary": clean_summary,
        "summary": textwrap.shorten(summary, width=320, placeholder="…"),
        "body": displayed_body,
        "translation_notice": translation_notice,
        "summary_translation_failed": summary_failed,
        "image": get_news_image(item),
        "link": link,
        "source": item.get("source_name", "NL Times"),
        "source_language": source_language,
        "has_original_variant": has_original_variant,
        "alert_topics": get_alert_topics(
            original_title, clean_summary, arabic_title or "", arabic_summary or ""
        ),
        "date": format_date(item),
        "date_published": published_datetime.isoformat() if published_datetime else None,
        "read_time": "3 دقائق",
        "accent": ("#d95d39", "#287c7b", "#bc7a2b", "#5c6f9d")[index % 4],
    }


@app.route("/robots.txt")
def robots():
    response = make_response(
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {site_absolute_url('sitemap.xml')}\n"
    )
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/sitemap.xml")
def sitemap():
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", namespace)
    urlset = ET.Element(f"{{{namespace}}}urlset")

    def add_url(path, change_frequency="daily", priority="0.7", last_modified=None):
        entry = ET.SubElement(urlset, f"{{{namespace}}}url")
        ET.SubElement(entry, f"{{{namespace}}}loc").text = site_absolute_url(path)
        if last_modified:
            ET.SubElement(entry, f"{{{namespace}}}lastmod").text = last_modified
        ET.SubElement(entry, f"{{{namespace}}}changefreq").text = change_frequency
        ET.SubElement(entry, f"{{{namespace}}}priority").text = priority

    add_url("/", "hourly", "1.0")
    add_url("/archive", "daily", "0.7")
    add_url("/transparency", "monthly", "0.4")
    for slug in categories:
        add_url(url_for("category", slug=slug), "hourly", "0.8")

    for item in load_news():
        add_url(
            url_for("article", article_id=item["id"]),
            "daily",
            "0.8",
            item.get("date_published"),
        )

    xml = ET.tostring(urlset, encoding="unicode")
    response = make_response(f'<?xml version="1.0" encoding="UTF-8"?>\n{xml}')
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    response.headers["Cache-Control"] = "public, max-age=900"
    return response


@app.route("/")
def home():
    news = load_news()
    return render_template(
        "index.html",
        news=news[:HOME_PAGE_SIZE],
        total_news=len(news),
        news_status=_news_status,
        **seo_context(
            "أخبار هولندا بالعربية | NL بالعربي",
            "أحدث أخبار هولندا وتحديثات عملية بالعربية للعرب المقيمين في هولندا.",
            site_absolute_url(url_for("home")),
        ),
    )


@app.route("/archive")
def archive():
    news = load_news()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1

    total_pages = max((len(news) + HOME_PAGE_SIZE - 1) // HOME_PAGE_SIZE, 1)
    page = min(page, total_pages)
    start = (page - 1) * HOME_PAGE_SIZE

    return render_template(
        "archive.html",
        news=news[start:start + HOME_PAGE_SIZE],
        news_status=_news_status,
        current_page=page,
        total_pages=total_pages,
        total_news=len(news),
        **seo_context(
            "أرشيف أخبار هولندا بالعربية | NL بالعربي",
            "أرشيف الأخبار الهولندية المترجمة إلى العربية مع روابط المصادر الأصلية.",
            site_absolute_url(url_for("archive", page=page)) if page > 1 else site_absolute_url(url_for("archive")),
        ),
    )


@app.route("/transparency")
def transparency():
    return render_template(
        "transparency.html",
        categories=categories,
        site_manager=app.config["SITE_MANAGER"],
        contact_email=app.config["CONTACT_EMAIL"],
        **seo_context(
            "الشفافية والتواصل | NL بالعربي",
            "تعرف إلى مصادر NL بالعربي ومنهجية الترجمة والتواصل مع فريق التحرير.",
            site_absolute_url(url_for("transparency")),
        ),
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
        news=category_news[:HOME_PAGE_SIZE],
        total_news=len(category_news),
        news_status=_news_status,
        **seo_context(
            f"أخبار {categories[slug]} بالعربية | NL بالعربي",
            f"آخر أخبار {categories[slug]} وتحديثاتها بالعربية للعرب المقيمين في هولندا.",
            site_absolute_url(url_for("category", slug=slug)),
        ),
    )


@app.route("/article/<article_id>")
def article(article_id):

    # Keep links from an already displayed page usable after a feed refresh.
    selected_article = _article_cache.get(article_id)
    news = (_news_cache or []) if selected_article is not None else load_news()

    selected_article = selected_article or next(
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

    article_url = site_absolute_url(url_for("article", article_id=selected_article["id"]))
    article_description = selected_article["summary"] or selected_article["title"]
    structured_data = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": selected_article["title"],
        "description": article_description,
        "inLanguage": "ar",
        "mainEntityOfPage": {"@type": "WebPage", "@id": article_url},
        "author": {"@type": "Organization", "name": "NL بالعربي"},
        "publisher": {"@type": "Organization", "name": "NL بالعربي"},
        "datePublished": selected_article.get("date_published"),
        "url": article_url,
    }
    if selected_article.get("image"):
        structured_data["image"] = [selected_article["image"]]

    return render_template(
        "article.html",
        article=selected_article,
        related=related,
        categories=categories,
        **seo_context(
            f"{selected_article['title']} | NL بالعربي",
            article_description,
            article_url,
            page_type="article",
            image=selected_article.get("image"),
            published_time=selected_article.get("date_published"),
            structured_data=structured_data,
        ),




    )


if __name__ == "__main__":
    app.run(debug=True)
