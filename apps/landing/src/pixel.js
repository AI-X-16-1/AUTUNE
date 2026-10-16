// The Meta Pixel, on this page only (#1356's review: never on the app's
// origin, where a script would run beside a signed-in session).
//
// build.mjs writes this file into dist/ and its <script> tag into the page
// only when LANDING_META_PIXEL_ID is set; the id arrives as data-pixel-id.
//
// - autoConfig off: the pixel reads no button, form field or page metadata
//   on its own. Automatic advanced matching is a setting in Meta's Events
//   Manager and must stay off there as well.
// - One PageView per load; this page has no client-side navigation.
// - One Lead per press of a "시작하기" link (`data-pixel-lead`), so an ad can
//   be measured by who went on to the app. Not the header's "로그인": that is
//   someone who already has an account. The event carries no button text;
//   with autoConfig off the pixel reads nothing from the page.
// - The refusal of privacy policy 제12조: the footer button keeps a mark in
//   this site's localStorage, and with it set Meta's script is never loaded.
(function () {
  var KEY = "autune.metaPixel.optOut";
  var me = document.currentScript;
  var id = me && me.getAttribute("data-pixel-id");

  function refused() {
    try {
      return window.localStorage.getItem(KEY) === "1";
    } catch (e) {
      // Storage refused: the choice cannot be read, so load nothing.
      return true;
    }
  }

  function dropBrowserId() {
    var parts = window.location.hostname.split(".");
    for (var i = 0; i < parts.length - 1; i += 1) {
      document.cookie = "_fbp=; Max-Age=0; path=/; domain=" + parts.slice(i).join(".");
    }
    document.cookie = "_fbp=; Max-Age=0; path=/";
  }

  function load() {
    var fbq = function () {
      if (fbq.callMethod) fbq.callMethod.apply(fbq, arguments);
      else fbq.queue.push(arguments);
    };
    fbq.push = fbq;
    fbq.loaded = true;
    fbq.version = "2.0";
    fbq.queue = [];
    window.fbq = fbq;
    window._fbq = fbq;
    var script = document.createElement("script");
    script.async = true;
    script.src = "https://connect.facebook.net/en_US/fbevents.js";
    document.head.appendChild(script);
    fbq("set", "autoConfig", false, id);
    fbq("init", id);
    fbq("track", "PageView");
    document.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a[data-pixel-lead]");
      if (!link || refused() || !window.fbq) return;
      window.fbq("track", "Lead");
    });
  }

  function showChoice() {
    var box = document.getElementById("pixel-choice");
    if (!box) return;
    var label = box.querySelector("[data-pixel-label]");
    var button = box.querySelector("button");
    if (refused()) {
      label.textContent = "이 브라우저에서 광고 측정(Meta 픽셀)을 거부하였습니다.";
      button.hidden = true;
    }
    box.hidden = false;
    button.addEventListener("click", function () {
      try {
        window.localStorage.setItem(KEY, "1");
      } catch (e) {
        // Nothing kept; refused() already treats a refused store as refused.
      }
      if (window.fbq) window.fbq("consent", "revoke");
      dropBrowserId();
      label.textContent = "이 브라우저에서 광고 측정(Meta 픽셀)을 거부하였습니다.";
      button.hidden = true;
    });
  }

  if (!id) return;
  if (!refused()) load();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", showChoice);
  else showChoice();
})();
