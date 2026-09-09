document.querySelectorAll(".news-image img").forEach((image) => {
    const hideFailedImage = () => {
        image.closest(".news-image").hidden = true;
    };
    image.addEventListener("error", hideFailedImage, { once: true });
    if (image.complete && image.naturalWidth === 0) {
        hideFailedImage();
    }
});
