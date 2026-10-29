(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var unlockForm = $("unlock-form"), setupForm = $("setup-form");
  var FIELDS = ["parent_name", "parent_nickname", "agent_role", "agent_name", "personal_details", "summary_time", "timezone"];
  var LOCKED = "Too many wrong tries. Please wait 5 minutes.";
  var BADPIN = "That PIN isn’t right.";
  var savedLanguage = "en";
  var configured = false;

  $("timezone").value = Intl.DateTimeFormat().resolvedOptions().timeZone || "";

  function fillForm(p) {
    FIELDS.forEach(function (f) { if (p[f] != null) $(f).value = p[f]; });
    $("child_emails").value = (p.child_emails || []).join(", ");
    savedLanguage = p.language || "en";
  }

  function friendly422(data) {
    var detail = data && data.detail;
    if (!Array.isArray(detail)) return "Please check your answers.";
    var msgs = [];
    detail.forEach(function (d) {
      var loc = (d.loc || []).join(".");
      var m;
      if (loc.indexOf("child_emails") >= 0) m = "Please check the email addresses";
      else if (loc.indexOf("timezone") >= 0) m = "Please pick a valid time zone";
      else if (loc.indexOf("summary_time") >= 0) m = "Time must be HH:MM";
      else if (loc.indexOf("pin") >= 0) m = "PIN must be 4–8 digits";
      else if (loc.indexOf("parent_name") >= 0 || loc.indexOf("parent_nickname") >= 0 || loc.indexOf("agent_name") >= 0) m = "Please fill in all the names";
      else m = "Please check your answers";
      if (msgs.indexOf(m) < 0) msgs.push(m);
    });
    return msgs.join(". ") + ".";
  }

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return null; }).then(function (d) { return { status: r.status, data: d }; }); });
  }

  unlockForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var pin = $("unlock-pin").value;
    $("unlock-error").textContent = "";
    post("/api/setup/unlock", { pin: pin }).then(function (r) {
      if (r.status === 200) {
        fillForm(r.data);
        $("pin").value = pin;
        unlockForm.hidden = true; setupForm.hidden = false;
        $("pin-hint").textContent = "(needed to save changes)";
      } else if (r.status === 401) $("unlock-error").textContent = BADPIN;
      else if (r.status === 423) $("unlock-error").textContent = LOCKED;
      else $("unlock-error").textContent = "Something went wrong. Please try again.";
    }).catch(function () { $("unlock-error").textContent = "Couldn’t reach the server."; });
  });

  setupForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var err = $("setup-error");
    err.textContent = "";
    var profile = { language: savedLanguage };
    FIELDS.forEach(function (f) { profile[f] = $(f).value.trim(); });
    profile.personal_details = $("personal_details").value;
    profile.child_emails = $("child_emails").value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    $("save-btn").disabled = true;
    post("/api/setup", { pin: $("pin").value, profile: profile }).then(function (r) {
      if (r.status === 200) { window.location.href = "/talk"; return; }
      $("save-btn").disabled = false;
      if (r.status === 401) err.textContent = BADPIN;
      else if (r.status === 423) err.textContent = LOCKED;
      else if (r.status === 422) err.textContent = friendly422(r.data);
      else err.textContent = "Something went wrong. Please try again.";
    }).catch(function () { $("save-btn").disabled = false; err.textContent = "Couldn’t reach the server."; });
  });

  fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
    configured = s.configured;
    if (configured) unlockForm.hidden = false; else setupForm.hidden = false;
  }).catch(function () { setupForm.hidden = false; });
})();
