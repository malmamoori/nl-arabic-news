from flask import Flask, abort, render_template

app = Flask(__name__)

categories = {
    "netherlands": "هولندا",
    "immigration": "الهجرة",
    "housing": "السكن",
    "work": "العمل",
}

news = [
    {
        "id": 1,
        "category": "هولندا",
        "slug": "netherlands",
        "date": "9 سبتمبر 2026",
        "read_time": "3 دقائق",
        "title": "تحديثات جديدة تهم المقيمين في هولندا هذا الأسبوع",
        "summary": "أبرز القرارات والمواعيد والخدمات التي يحتاج المقيمون إلى معرفتها في حياتهم اليومية.",
        "body": "تتغير بعض الإجراءات والخدمات بشكل مستمر، لذلك من المهم متابعة المصادر الرسمية ومعرفة المواعيد الجديدة. نستعرض هنا أهم النقاط العملية التي تساعد المقيمين على التخطيط لأسبوعهم بثقة.",
        "accent": "#d95d39",
    },
    {
        "id": 2,
        "category": "الهجرة",
        "slug": "immigration",
        "date": "8 سبتمبر 2026",
        "read_time": "4 دقائق",
        "title": "دليل مبسط لفهم خطوات تجديد تصريح الإقامة",
        "summary": "ما الوثائق المطلوبة؟ ومتى يجب بدء الطلب؟ نظرة عملية على الخطوات الأساسية.",
        "body": "يفضل البدء بإجراءات التجديد قبل انتهاء التصريح بوقت كاف. جهز وثائق الهوية، وعقد العمل أو السكن عند الحاجة، وتحقق من المتطلبات الحالية لدى الجهة المختصة قبل إرسال الطلب.",
        "accent": "#287c7b",
    },
    {
        "id": 3,
        "category": "السكن",
        "slug": "housing",
        "date": "7 سبتمبر 2026",
        "read_time": "5 دقائق",
        "title": "كيف تقارن بين عقدي الإيجار قبل التوقيع؟",
        "summary": "نقاط مهمة حول مدة العقد، التكاليف الإضافية، والتزامات المستأجر في هولندا.",
        "body": "قبل التوقيع، راجع قيمة الإيجار الأساسية وتكاليف الخدمات وشروط الزيادة ومدة الإشعار. احتفظ بنسخة من العقد وسجل حالة المنزل بالصور عند الاستلام.",
        "accent": "#bc7a2b",
    },
    {
        "id": 4,
        "category": "العمل",
        "slug": "work",
        "date": "6 سبتمبر 2026",
        "read_time": "3 دقائق",
        "title": "نصائح عملية لبناء سيرة ذاتية مناسبة للسوق الهولندي",
        "summary": "اجعل خبرتك واضحة ومختصرة، وركز على المهارات التي يبحث عنها أصحاب العمل.",
        "body": "ابدأ بملخص قصير عن خبرتك، ثم رتب خبراتك من الأحدث إلى الأقدم. أضف اللغات ومستوى إتقانها، واربط كل مهارة بنتيجة أو مسؤولية واضحة.",
        "accent": "#5c6f9d",
    },
    {
        "id": 5,
        "category": "هولندا",
        "slug": "netherlands",
        "date": "5 سبتمبر 2026",
        "read_time": "2 دقائق",
        "title": "خدمات يومية يمكن إنجازها بسهولة عبر الإنترنت",
        "summary": "من المواعيد الحكومية إلى متابعة الرسائل الرسمية، هذه الأدوات توفر الوقت.",
        "body": "تتوفر العديد من الخدمات الحكومية الرقمية بعد تسجيل الدخول الآمن. خصص مكانا منظما لرسائلك الرسمية وتابع المواعيد والتنبيهات حتى لا تفوت أي إجراء.",
        "accent": "#d95d39",
    },
    {
        "id": 6,
        "category": "الهجرة",
        "slug": "immigration",
        "date": "4 سبتمبر 2026",
        "read_time": "4 دقائق",
        "title": "أسئلة شائعة حول لمّ شمل الأسرة",
        "summary": "إجابة عن الأسئلة الأولى التي يطرحها المقيمون عند بدء إجراءات لمّ الشمل.",
        "body": "تختلف الشروط بحسب نوع الإقامة والعلاقة الأسرية. اجمع الوثائق المدنية المطلوبة وترجمها عند الحاجة، ثم راجع الشروط الرسمية الخاصة بحالتك قبل تقديم الطلب.",
        "accent": "#287c7b",
    },
]


def get_article(article_id):
    return next((item for item in news if item["id"] == article_id), None)


@app.route("/")
def home():
    return render_template("index.html", news=news, categories=categories, active_category=None)


@app.route("/category/<slug>")
def category(slug):
    if slug not in categories:
        abort(404)
    category_news = [item for item in news if item["slug"] == slug]
    return render_template(
        "index.html",
        news=category_news,
        categories=categories,
        active_category=categories[slug],
    )


@app.route("/article/<int:article_id>")
def article(article_id):
    selected_article = get_article(article_id)
    if selected_article is None:
        abort(404)
    related = [item for item in news if item["slug"] == selected_article["slug"] and item["id"] != article_id][:2]
    return render_template(
        "article.html",
        article=selected_article,
        related=related,
        categories=categories,
    )
