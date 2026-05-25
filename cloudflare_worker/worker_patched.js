/**
 * Cloudflare Worker for serving photobooth photos from R2.
 *
 * - Serves photos via random unguessable URLs
 * - Serves gallery pages for photo sessions
 * - Auto-deletes photos older than EXPIRY_MINUTES
 * - Cron trigger cleans up unaccessed photos every 5 minutes
 * - Per-session branding text via customMetadata.branding_text
 *
 * R2 bucket is bound as `PHOTOS` in wrangler.toml
 */

const EXPIRY_MINUTES = 30;

export default {
  /**
   * Handle HTTP requests - serve photos, galleries, or return 404/expired.
   */
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.slice(1); // Remove leading /

    // Health check
    if (!path || path === "health") {
      return new Response("OK", { status: 200 });
    }

    // Only allow GET requests
    if (request.method !== "GET") {
      return new Response("Method not allowed", { status: 405 });
    }

    // Gallery routes: /gallery/{session_id} or /gallery/{session_id}/{filename}
    if (path.startsWith("gallery/")) {
      const parts = path.slice("gallery/".length).split("/");
      const sessionId = parts[0];
      const filename = parts.slice(1).join("/");

      if (!sessionId) {
        return expiredPage();
      }

      // Serve individual file from session: /gallery/{session_id}/{filename}
      if (filename) {
        const key = `${sessionId}/${filename}`;
        const object = await env.PHOTOS.get(key);

        if (!object) {
          return expiredPage();
        }

        // Check expiry
        const uploadedAt = object.customMetadata?.uploaded_at;
        if (uploadedAt) {
          const ageMinutes = (Date.now() / 1000 - parseInt(uploadedAt)) / 60;
          if (ageMinutes > EXPIRY_MINUTES) {
            await deleteSession(env, sessionId);
            return expiredPage();
          }
        }

        // Determine content type from filename
        const contentType = getContentType(filename);
        const headers = new Headers();
        headers.set("Content-Type", object.httpMetadata?.contentType || contentType);
        headers.set("Content-Disposition", `inline; filename="${filename}"`);
        headers.set("Cache-Control", "no-store");
        headers.set("Access-Control-Allow-Origin", "*");

        return new Response(object.body, { headers });
      }

      // Gallery page: /gallery/{session_id}
      return handleGallery(env, sessionId, url.origin);
    }

    // Legacy single-file serving: /{key}
    const object = await env.PHOTOS.get(path);

    if (!object) {
      return expiredPage();
    }

    // Check expiry via metadata
    const uploadedAt = object.customMetadata?.uploaded_at;
    if (uploadedAt) {
      const ageMinutes = (Date.now() / 1000 - parseInt(uploadedAt)) / 60;
      if (ageMinutes > EXPIRY_MINUTES) {
        await env.PHOTOS.delete(path);
        return expiredPage();
      }
    }

    // Serve the photo with inline headers
    const headers = new Headers();
    headers.set("Content-Type", object.httpMetadata?.contentType || "image/jpeg");
    headers.set("Content-Disposition", `inline; filename="photobooth-foto.jpg"`);
    headers.set("Cache-Control", "no-store");
    headers.set("Access-Control-Allow-Origin", "*");

    return new Response(object.body, { headers });
  },

  /**
   * Cron trigger - clean up expired photos every 5 minutes.
   */
  async scheduled(event, env, ctx) {
    const now = Date.now() / 1000;
    let deleted = 0;
    let cursor = undefined;

    // List all objects and delete expired ones
    do {
      // include: ['customMetadata'] zorgt dat customMetadata.uploaded_at
      // beschikbaar is in de list-respons (anders niet, en valt cleanup
      // terug op object.uploaded timestamp).
      const listed = await env.PHOTOS.list({
        cursor,
        limit: 500,
        include: ["customMetadata"],
      });

      for (const object of listed.objects) {
        const uploadedAt = object.customMetadata?.uploaded_at;
        if (uploadedAt) {
          const ageMinutes = (now - parseInt(uploadedAt)) / 60;
          if (ageMinutes > EXPIRY_MINUTES) {
            await env.PHOTOS.delete(object.key);
            deleted++;
          }
        } else {
          // No timestamp metadata - delete if older than expiry based on uploaded date
          const objectAge = (now * 1000 - object.uploaded.getTime()) / 1000 / 60;
          if (objectAge > EXPIRY_MINUTES) {
            await env.PHOTOS.delete(object.key);
            deleted++;
          }
        }
      }

      cursor = listed.truncated ? listed.cursor : undefined;
    } while (cursor);

    if (deleted > 0) {
      console.log(`[CLEANUP] ${deleted} verlopen foto's verwijderd`);
    }
  },
};

/**
 * Delete all R2 objects for a session.
 */
async function deleteSession(env, sessionId) {
  let cursor = undefined;
  do {
    const listed = await env.PHOTOS.list({ prefix: `${sessionId}/`, cursor, limit: 500 });
    for (const object of listed.objects) {
      await env.PHOTOS.delete(object.key);
    }
    cursor = listed.truncated ? listed.cursor : undefined;
  } while (cursor);
}

/**
 * Determine content type from filename.
 */
function getContentType(filename) {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".gif")) return "image/gif";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".mp4")) return "video/mp4";
  return "image/jpeg";
}

/**
 * Escape HTML special characters to prevent injection when rendering
 * user-supplied branding text.
 */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Render branding text safely: URL-decode (booth uploader percent-encodes the
 * value because HTTP headers don't allow newlines / non-ASCII), then
 * HTML-escape, then turn newlines into <br>.
 */
function formatBranding(text) {
  let decoded = text;
  try {
    decoded = decodeURIComponent(text);
  } catch (e) {
    // Fall back to raw value if it wasn't percent-encoded
    decoded = text;
  }
  return escapeHtml(decoded).replace(/\r?\n/g, "<br>");
}

/**
 * Handle gallery route: list session objects and build gallery HTML.
 */
async function handleGallery(env, sessionId, origin) {
  // List all objects with session prefix. include: ['customMetadata'] zorgt
  // dat we branding_text + uploaded_at uit metadata kunnen lezen — anders
  // zijn die velden undefined in een gewone list() respons.
  const allObjects = [];
  let cursor = undefined;
  do {
    const listed = await env.PHOTOS.list({
      prefix: `${sessionId}/`,
      cursor,
      limit: 500,
      include: ["customMetadata"],
    });
    allObjects.push(...listed.objects);
    cursor = listed.truncated ? listed.cursor : undefined;
  } while (cursor);

  // No objects found
  if (allObjects.length === 0) {
    return expiredPage();
  }

  // Check expiry on the first object
  const firstObj = allObjects[0];
  const uploadedAt = firstObj.customMetadata?.uploaded_at;
  if (uploadedAt) {
    const ageMinutes = (Date.now() / 1000 - parseInt(uploadedAt)) / 60;
    if (ageMinutes > EXPIRY_MINUTES) {
      await deleteSession(env, sessionId);
      return expiredPage();
    }
  }

  // Pak branding-text uit metadata van het eerste object van deze sessie.
  // De booth-app stuurt deze waarde mee als de QR-toggle + branding-toggle
  // aan staan. Leeg/ontbrekend = fallback naar "Powered by Bootharoo".
  const brandingText = firstObj.customMetadata?.branding_text || "";

  // Sort objects: strip first, then photo_1, photo_2, ..., boomerang last
  const sorted = sortSessionFiles(allObjects, sessionId);

  // Build slides data
  const slides = sorted.map((obj) => {
    const filename = obj.key.slice(sessionId.length + 1); // Remove prefix
    const label = getSlideLabel(filename);
    const isGif = filename.toLowerCase().endsWith(".gif");
    const src = `/gallery/${sessionId}/${filename}`;
    return { filename, label, isGif, src };
  });

  const html = buildGalleryHTML(slides, sessionId, brandingText);

  return new Response(html, {
    status: 200,
    headers: {
      "Content-Type": "text/html;charset=UTF-8",
      "Cache-Control": "no-store",
    },
  });
}

/**
 * Sort session files: strip first, photos in numeric order, boomerang last.
 */
function sortSessionFiles(objects, sessionId) {
  function sortKey(obj) {
    const filename = obj.key.slice(sessionId.length + 1).toLowerCase();
    if (filename.startsWith("strip")) return [0, 0];
    if (filename.startsWith("boomerang")) return [2, 0];
    // photo_1, photo_2, etc.
    const match = filename.match(/photo_(\d+)/);
    if (match) return [1, parseInt(match[1])];
    // Unknown files go between photos and boomerang
    return [1, 999];
  }

  return [...objects].sort((a, b) => {
    const ka = sortKey(a);
    const kb = sortKey(b);
    if (ka[0] !== kb[0]) return ka[0] - kb[0];
    return ka[1] - kb[1];
  });
}

/**
 * Get a human-readable label for a slide.
 */
function getSlideLabel(filename) {
  const lower = filename.toLowerCase();
  if (lower.startsWith("strip")) return "Fotostrip";
  if (lower.startsWith("boomerang")) return "Boomerang";
  const match = lower.match(/photo_(\d+)/);
  if (match) return `Foto ${match[1]}`;
  return filename;
}

/**
 * Build the self-contained gallery HTML page.
 *
 * @param slides       Array of slide-objects (filename/label/isGif/src).
 * @param sessionId    R2 session prefix (for safety / future use).
 * @param brandingText Multi-line tekst die in de footer komt te staan.
 *                     Leeg = fallback naar "Powered by Bootharoo".
 */
function buildGalleryHTML(slides, sessionId, brandingText) {
  const slidesJSON = JSON.stringify(slides);
  const total = slides.length;
  const footerHTML = brandingText
    ? formatBranding(brandingText)
    : 'Powered by <strong>Bootharoo</strong>';

  return `<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Jouw Foto's! - Bootharoo</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }

    html, body {
      height: 100%;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #F7F5F1;
      color: #53565A;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
      -webkit-user-select: none;
    }

    .gallery-container {
      display: flex;
      flex-direction: column;
      height: 100%;
      max-width: 600px;
      margin: 0 auto;
    }

    /* Header */
    .gallery-header {
      text-align: center;
      padding: 16px 16px 8px;
      flex-shrink: 0;
    }

    .gallery-header h1 {
      font-size: 22px;
      font-weight: 700;
      color: #53565A;
    }

    .slide-counter {
      font-size: 13px;
      color: #A8A9AC;
      margin-top: 4px;
    }

    /* Slideshow area */
    .slideshow {
      flex: 1;
      position: relative;
      overflow: hidden;
      min-height: 0;
    }

    .slides-track {
      display: flex;
      height: 100%;
      transition: transform 0.35s cubic-bezier(0.25, 0.46, 0.45, 0.94);
      will-change: transform;
    }

    .slides-track.dragging {
      transition: none;
    }

    .slide {
      min-width: 100%;
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 8px 16px;
    }

    .slide-label {
      font-size: 14px;
      font-weight: 600;
      color: #D6C29B;
      margin-bottom: 8px;
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }

    .slide-card {
      background: #EFEDE8;
      border-radius: 16px;
      padding: 12px;
      max-height: calc(100% - 60px);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 12px rgba(83, 86, 90, 0.08);
      position: relative;
      width: 100%;
    }

    .slide-card img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      border-radius: 8px;
      display: block;
    }

    .download-btn {
      margin-top: 10px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 18px;
      background: #4A9B6E;
      color: #fff;
      border: none;
      border-radius: 24px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      transition: background 0.2s;
    }

    .download-btn:hover { background: #3d8a5e; }

    .download-btn svg {
      width: 16px;
      height: 16px;
      fill: currentColor;
    }

    .share-btn {
      margin-top: 10px;
      display: none;
      align-items: center;
      gap: 6px;
      padding: 8px 18px;
      background: #25D366;
      color: #fff;
      border: none;
      border-radius: 24px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
      box-shadow: 0 2px 10px rgba(37,211,102,0.30);
    }

    .share-btn:hover { background: #1da851; }
    .share-btn svg { width: 16px; height: 16px; fill: currentColor; }

    .btn-row {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: center;
      margin-top: 10px;
    }

    /* Arrow buttons */
    .arrow {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: rgba(214, 194, 155, 0.85);
      border: none;
      color: #fff;
      font-size: 20px;
      cursor: pointer;
      z-index: 10;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s;
      box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    }

    .arrow:hover { background: rgba(214, 194, 155, 1); }
    .arrow.left { left: 8px; }
    .arrow.right { right: 8px; }
    .arrow.hidden { display: none; }

    /* Dots */
    .dots {
      display: flex;
      justify-content: center;
      gap: 8px;
      padding: 10px 0;
      flex-shrink: 0;
    }

    .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: #D6C29B;
      opacity: 0.35;
      transition: opacity 0.3s, transform 0.3s;
      cursor: pointer;
      border: none;
    }

    .dot.active {
      opacity: 1;
      transform: scale(1.25);
    }

    /* Footer */
    .gallery-footer {
      text-align: center;
      padding: 6px 16px 10px;
      flex-shrink: 0;
    }

    .download-all-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 10px 24px;
      background: #D6C29B;
      color: #53565A;
      border: none;
      border-radius: 24px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      transition: background 0.2s;
      margin-bottom: 8px;
    }

    .download-all-btn:hover { background: #c9b58c; }

    .download-all-btn svg {
      width: 18px;
      height: 18px;
      fill: currentColor;
    }

    .powered-by {
      font-size: 11px;
      color: #A8A9AC;
      line-height: 1.45;
      max-width: 90%;
      margin: 0 auto;
    }

    .powered-by strong {
      color: #D6C29B;
      font-weight: 700;
    }
  </style>
</head>
<body>
  <div class="gallery-container">
    <div class="gallery-header">
      <h1>Your photo's</h1>
      <div class="slide-counter" id="counter">1 / ${total}</div>
    </div>

    <div class="slideshow" id="slideshow">
      <button class="arrow left hidden" id="arrowLeft" aria-label="Vorige">&#8249;</button>
      <button class="arrow right${total <= 1 ? " hidden" : ""}" id="arrowRight" aria-label="Next">&#8250;</button>

      <div class="slides-track" id="track">
        ${slides
          .map(
            (s, i) => `
        <div class="slide">
          <div class="slide-label">${s.label}</div>
          <div class="slide-card">
            <img src="${s.src}" alt="${s.label}" loading="${i === 0 ? "eager" : "lazy"}">
          </div>
          <div class="btn-row">
            <a class="download-btn" href="${s.src}" download="${s.filename}">
              <svg viewBox="0 0 24 24"><path d="M12 16l-5-5h3V4h4v7h3l-5 5zm-7 4h14v-2H5v2z"/></svg>
              Download
            </a>
            <button class="share-btn" data-src="${s.src}" data-label="${s.label}" data-filename="${s.filename}">
              <svg viewBox="0 0 24 24"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92s2.92-1.31 2.92-2.92-1.31-2.92-2.92-2.92z"/></svg>
              Share
            </button>
          </div>
        </div>`
          )
          .join("")}
      </div>
    </div>

    <div class="dots" id="dots">
      ${slides.map((_, i) => `<button class="dot${i === 0 ? " active" : ""}" data-i="${i}" aria-label="Slide ${i + 1}"></button>`).join("")}
    </div>

    <div class="gallery-footer">
      <button class="download-all-btn" id="downloadAll">
        <svg viewBox="0 0 24 24"><path d="M12 16l-5-5h3V4h4v7h3l-5 5zm-7 4h14v-2H5v2z"/></svg>
        Download all
      </button>
      <div class="powered-by">${footerHTML}</div>
    </div>
  </div>

  <script>
    (function() {
      var slides = ${slidesJSON};
      var current = 0;
      var total = slides.length;
      var track = document.getElementById('track');
      var counter = document.getElementById('counter');
      var arrowLeft = document.getElementById('arrowLeft');
      var arrowRight = document.getElementById('arrowRight');
      var dotsContainer = document.getElementById('dots');
      var dots = dotsContainer.querySelectorAll('.dot');
      var slideshow = document.getElementById('slideshow');

      function goTo(idx) {
        if (idx < 0) idx = 0;
        if (idx >= total) idx = total - 1;
        current = idx;
        track.style.transform = 'translateX(-' + (current * 100) + '%)';
        counter.textContent = (current + 1) + ' / ' + total;
        arrowLeft.classList.toggle('hidden', current === 0);
        arrowRight.classList.toggle('hidden', current === total - 1);
        for (var i = 0; i < dots.length; i++) {
          dots[i].classList.toggle('active', i === current);
        }
      }

      arrowLeft.addEventListener('click', function() { goTo(current - 1); });
      arrowRight.addEventListener('click', function() { goTo(current + 1); });

      dotsContainer.addEventListener('click', function(e) {
        var dot = e.target.closest('.dot');
        if (dot && dot.dataset.i !== undefined) {
          goTo(parseInt(dot.dataset.i));
        }
      });

      // Keyboard
      document.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowLeft') goTo(current - 1);
        else if (e.key === 'ArrowRight') goTo(current + 1);
      });

      // Touch / swipe
      var startX = 0, startY = 0, distX = 0, swiping = false;

      slideshow.addEventListener('touchstart', function(e) {
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        distX = 0;
        swiping = true;
        track.classList.add('dragging');
      }, { passive: true });

      slideshow.addEventListener('touchmove', function(e) {
        if (!swiping) return;
        distX = e.touches[0].clientX - startX;
        var distY = e.touches[0].clientY - startY;
        // Only swipe horizontally
        if (Math.abs(distX) > Math.abs(distY)) {
          var base = -(current * 100);
          var offset = (distX / slideshow.offsetWidth) * 100;
          track.style.transform = 'translateX(' + (base + offset) + '%)';
        }
      }, { passive: true });

      slideshow.addEventListener('touchend', function() {
        if (!swiping) return;
        swiping = false;
        track.classList.remove('dragging');
        if (Math.abs(distX) > 50) {
          if (distX < 0) goTo(current + 1);
          else goTo(current - 1);
        } else {
          goTo(current);
        }
      });

      // Share via Web Share API
      if (navigator.share) {
        var shareBtns = document.querySelectorAll('.share-btn');
        for (var i = 0; i < shareBtns.length; i++) {
          shareBtns[i].style.display = 'inline-flex';
          shareBtns[i].addEventListener('click', function() {
            var src = this.getAttribute('data-src');
            var label = this.getAttribute('data-label');
            var filename = this.getAttribute('data-filename');
            fetch(src)
              .then(function(r) { return r.blob(); })
              .then(function(blob) {
                var file = new File([blob], filename, { type: blob.type });
                if (navigator.canShare && navigator.canShare({ files: [file] })) {
                  return navigator.share({ title: label + ' - Bootharoo', files: [file] });
                } else {
                  return navigator.share({ title: label + ' - Bootharoo', text: 'Bekijk mijn foto van de photobooth!', url: window.location.href });
                }
              })
              .catch(function(err) {
                if (err.name !== 'AbortError') {
                  navigator.share({ title: 'Mijn foto - Bootharoo', text: 'Bekijk mijn foto van de photobooth!', url: window.location.href }).catch(function() {});
                }
              });
          });
        }
      }

      // Download all
      document.getElementById('downloadAll').addEventListener('click', function() {
        slides.forEach(function(s, i) {
          setTimeout(function() {
            var a = document.createElement('a');
            a.href = s.src;
            a.download = s.filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
          }, i * 300);
        });
      });
    })();
  </script>
</body>
</html>`;
}

/**
 * Return a friendly "photo expired" page.
 */
function expiredPage() {
  const html = `<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Foto niet meer beschikbaar</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #F7F5F1;
      color: #53565A;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      padding: 20px;
    }
    .container {
      text-align: center;
      max-width: 400px;
    }
    .icon { font-size: 64px; margin-bottom: 20px; }
    h1 { font-size: 24px; margin-bottom: 12px; color: #53565A; }
    p { font-size: 16px; color: #A8A9AC; line-height: 1.5; }
  </style>
</head>
<body>
  <div class="container">
    <div class="icon">📷</div>
    <h1>Foto niet meer beschikbaar</h1>
    <p>Deze foto is verlopen en automatisch verwijderd voor je privacy.</p>
    <p style="margin-top: 16px; font-size: 14px;">Scan de QR-code opnieuw bij de photobooth voor een nieuwe foto!</p>
  </div>
</body>
</html>`;

  return new Response(html, {
    status: 404,
    headers: { "Content-Type": "text/html;charset=UTF-8" },
  });
}
