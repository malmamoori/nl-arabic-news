(() => {
    "use strict";

    const storageKey = "nl-bilarabi-saved-articles-v1";
    const alertStorageKey = "nl-bilarabi-alert-preferences-v1";
    const notifiedAlertStorageKey = "nl-bilarabi-notified-alerts-v1";

    document.querySelectorAll(".news-image img").forEach((image) => {
        const hideFailedImage = () => {
            const container = image.closest(".news-image");
            if (container) container.hidden = true;
        };
        image.addEventListener("error", hideFailedImage, { once: true });
        if (image.complete && image.naturalWidth === 0) hideFailedImage();
    });

    const readSavedArticles = () => {
        try {
            const saved = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
            return new Set(Array.isArray(saved) ? saved : []);
        } catch (_) {
            return new Set();
        }
    };

    const saveSavedArticles = (saved) => {
        try {
            window.localStorage.setItem(storageKey, JSON.stringify([...saved]));
        } catch (_) {
            // Browsers in private mode can disable storage. The page remains usable.
        }
    };

    const readAlertPreferences = () => {
        try {
            const preferences = JSON.parse(window.localStorage.getItem(alertStorageKey) || "[]");
            return Array.isArray(preferences) ? preferences : [];
        } catch (_) {
            return [];
        }
    };

    const savedArticles = readSavedArticles();
    const updateSaveButtons = () => {
        document.querySelectorAll("[data-save-article]").forEach((button) => {
            const isSaved = savedArticles.has(button.dataset.saveArticle);
            button.setAttribute("aria-pressed", String(isSaved));
            button.classList.toggle("is-saved", isSaved);
            button.textContent = isSaved
                ? (button.classList.contains("secondary-button") ? "إزالة الحفظ" : "محفوظ")
                : (button.classList.contains("secondary-button") ? "حفظ الخبر" : "حفظ");
        });
    };

    document.querySelectorAll("[data-save-article]").forEach((button) => {
        button.addEventListener("click", () => {
            const articleId = button.dataset.saveArticle;
            if (savedArticles.has(articleId)) savedArticles.delete(articleId);
            else savedArticles.add(articleId);
            saveSavedArticles(savedArticles);
            updateSaveButtons();
        });
    });
    updateSaveButtons();

    const copyShareLink = async (url) => {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(url);
            return true;
        }
        const field = document.createElement("textarea");
        field.value = url;
        field.setAttribute("readonly", "");
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.appendChild(field);
        field.select();
        const copied = document.execCommand("copy");
        field.remove();
        return copied;
    };

    document.querySelectorAll(".share-button").forEach((button) => {
        button.addEventListener("click", async () => {
            const shareData = {
                title: button.dataset.shareTitle || document.title,
                text: button.dataset.shareTitle || document.title,
                url: button.dataset.shareUrl || window.location.href,
            };
            try {
                if (navigator.share) {
                    await navigator.share(shareData);
                    return;
                }
                if (await copyShareLink(shareData.url)) {
                    const previous = button.textContent;
                    button.textContent = "تم نسخ الرابط";
                    window.setTimeout(() => { button.textContent = previous; }, 1800);
                }
            } catch (_) {
                // Dismissing the native share sheet is not an error the reader needs to see.
            }
        });
    });

    document.querySelectorAll(".language-button").forEach((button) => {
        button.addEventListener("click", () => {
            const scope = button.closest(".news-card, .article-content");
            if (!scope) return;
            const language = button.dataset.language;
            scope.querySelectorAll("[data-language-content]").forEach((content) => {
                content.hidden = content.dataset.languageContent !== language;
            });
            scope.querySelectorAll(".language-button").forEach((control) => {
                control.classList.toggle("active", control === button);
            });
        });
    });

    const searchInput = document.getElementById("newsSearch");
    const sourceButtons = document.querySelectorAll(".source-btn");
    const newsCards = document.querySelectorAll(".news-card");
    const newsResultsStatus = document.querySelector("[data-news-results]");
    let activeSource = "all";

    const normalizeSearchText = (value) => (value || "")
        .toLocaleLowerCase()
        .replace(/[\u064B-\u065F\u0670]/g, "")
        .replace(/\s+/g, " ")
        .trim();

    const filterNews = () => {
        const searchValue = normalizeSearchText(searchInput?.value);
        let visibleCount = 0;
        newsCards.forEach((card) => {
            const text = normalizeSearchText(card.textContent);
            const source = normalizeSearchText(card.dataset.source);
            const matchesSearch = !searchValue || text.includes(searchValue);
            const matchesSource = activeSource === "all" || source === normalizeSearchText(activeSource);
            const isVisible = matchesSearch && matchesSource;
            card.hidden = !isVisible;
            if (isVisible) visibleCount += 1;
        });
        if (newsResultsStatus) {
            newsResultsStatus.textContent = visibleCount
                ? `${visibleCount} خبر متاح`
                : "لا توجد أخبار مطابقة لبحثك.";
            newsResultsStatus.hidden = visibleCount > 0;
        }
    };

    if (searchInput) searchInput.addEventListener("input", filterNews);
    sourceButtons.forEach((button) => {
        button.addEventListener("click", () => {
            sourceButtons.forEach((control) => control.classList.remove("active"));
            button.classList.add("active");
            activeSource = button.dataset.source;
            filterNews();
        });
    });
    filterNews();

    const notifyRelevantStory = () => {
        if (!("Notification" in window) || Notification.permission !== "granted") return false;
        const preferences = readAlertPreferences();
        if (!preferences.length) return false;
        const story = [...document.querySelectorAll(".news-card[data-alert-topics]")].find((card) => {
            const topics = card.dataset.alertTopics.split(",").filter(Boolean);
            return topics.some((topic) => preferences.includes(topic));
        });
        if (!story) return false;
        const articleId = story.dataset.articleId;
        try {
            const notified = JSON.parse(window.localStorage.getItem(notifiedAlertStorageKey) || "[]");
            if (Array.isArray(notified) && notified.includes(articleId)) return false;
            const headline = story.querySelector("[data-language-content='arabic']")?.innerText || "خبر مهم جديد";
            new Notification("تنبيه مهم من NL بالعربي", { body: headline, tag: articleId });
            const updated = [articleId, ...(Array.isArray(notified) ? notified : [])].slice(0, 30);
            window.localStorage.setItem(notifiedAlertStorageKey, JSON.stringify(updated));
            return true;
        } catch (_) {
            return false;
        }
    };

    const alertsForm = document.getElementById("alertsForm");
    const alertsStatus = document.getElementById("alertsStatus");
    if (alertsForm) {
        const saved = readAlertPreferences();
        alertsForm.querySelectorAll("input[name='alert-topic']").forEach((input) => {
            input.checked = saved.includes(input.value);
        });

        alertsForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const topics = [...alertsForm.querySelectorAll("input[name='alert-topic']:checked")]
                .map((input) => input.value);
            if (!topics.length) {
                alertsStatus.textContent = "اختر نوع تنبيه واحدًا على الأقل.";
                return;
            }
            try {
                window.localStorage.setItem(alertStorageKey, JSON.stringify(topics));
            } catch (_) {
                // Permission may still be requested even when storage is unavailable.
            }
            if (!("Notification" in window)) {
                alertsStatus.textContent = "حفظنا اختياراتك؛ هذا المتصفح لا يدعم التنبيهات.";
                return;
            }
            const permission = await Notification.requestPermission();
            const sentAlert = permission === "granted" && notifyRelevantStory();
            alertsStatus.textContent = permission === "granted"
                ? (sentAlert ? "تم التفعيل وأرسلنا أحدث تنبيه مطابق لاختياراتك." : "تم تفعيل التفضيلات. ستظهر التنبيهات المطابقة عند فتح الموقع.")
                : "حفظنا اختياراتك. يمكنك السماح بالتنبيهات من إعدادات المتصفح لاحقًا.";
        });
    }
    notifyRelevantStory();
})();
