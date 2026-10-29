(function () {
  "use strict";
  var btn = document.getElementById("talk-btn");
  var statusEl = document.getElementById("status");
  var sink = document.getElementById("audio-sink");
  var agentName = "your companion";
  var room = null;
  var inCall = false;
  var attempt = 0;
  var agentTimer = null;
  var failed = false;

  function setStatus(t) { statusEl.textContent = t; }
  function idle() {
    inCall = false;
    btn.disabled = false;
    btn.classList.remove("in-call");
    btn.textContent = "Talk to " + agentName;
  }
  function clearTimer() { if (agentTimer) { clearTimeout(agentTimer); agentTimer = null; } }

  function agentLabel(state) {
    if (state === "listening") return agentName + " is listening";
    if (state === "speaking") return agentName + " is speaking";
    if (state === "thinking") return "…";
    return null;
  }
  function isAgent(p) {
    if (p.isAgent === true) return true;
    var K = window.LivekitClient && LivekitClient.ParticipantKind;
    return !!(K && p.kind === K.AGENT);
  }
  function updateFromAgent(p) {
    var s = p.attributes && p.attributes["lk.agent.state"];
    var l = agentLabel(s);
    if (l) setStatus(l);
  }

  function fail(r) {
    clearTimer();
    if (r) { try { r.disconnect(); } catch (e) {} }
    if (room === r) room = null;
  }

  function startAttempt() {
    attempt += 1;
    failed = false;
    btn.disabled = true;
    setStatus("Connecting…");
    var r = null;
    var done = false;
    function giveUp() {
      if (done) return;
      done = true;
      failed = true;
      fail(r);
      if (attempt < 2) { startAttempt(); return; }
      attempt = 0;
      idle();
      setStatus("Couldn’t connect — tap to try again");
    }
    fetch("/api/token", { method: "POST" })
      .then(function (res) { if (!res.ok) throw new Error("token " + res.status); return res.json(); })
      .then(function (t) {
        if (done) return;
        r = new LivekitClient.Room();
        room = r;
        var E = LivekitClient.RoomEvent;
        r.on(E.TrackSubscribed, function (track) {
          if (track.kind === "audio") {
            var el = track.attach();
            el.autoplay = true;
            document.body.appendChild(el);
          }
        });
        function onAgent(p) {
          if (!isAgent(p)) return;
          clearTimer();
          if (!done) { done = true; inCall = true; attempt = 0;
            btn.disabled = false; btn.classList.add("in-call"); btn.textContent = "End"; }
          updateFromAgent(p);
        }
        r.on(E.ParticipantConnected, onAgent);
        r.on(E.ParticipantAttributesChanged, function (changed, p) { onAgent(p); });
        r.on(E.Disconnected, function () {
          clearTimer();
          if (room === r) room = null;
          if (inCall) { idle(); setStatus(""); }
          else if (!done) { giveUp(); }
        });
        agentTimer = setTimeout(giveUp, 15000);
        return r.connect(t.url, t.token)
          .then(function () { return r.localParticipant.setMicrophoneEnabled(true); })
          .then(function () {
            r.remoteParticipants.forEach(function (p) { onAgent(p); });
          });
      })
      .catch(function () { giveUp(); });
  }

  btn.addEventListener("click", function () {
    if (inCall) {
      btn.disabled = true;
      if (room) { room.disconnect(); } else { idle(); setStatus(""); }
      return;
    }
    attempt = 0;
    startAttempt();
  });

  fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
    if (!s.configured) { window.location.href = "/setup"; return; }
    agentName = s.agent_name;
    idle();
  }).catch(function () { setStatus("Couldn’t load. Please reload."); });
})();
