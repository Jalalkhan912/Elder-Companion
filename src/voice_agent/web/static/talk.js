(function () {
  "use strict";
  var btn = document.getElementById("talk-btn");
  var statusEl = document.getElementById("status");
  var sink = document.getElementById("audio-sink");
  var agentName = "your companion";
  var room = null;
  var inCall = false;
  var attempt = 0;
  var userEnded = false;

  function setStatus(t) { statusEl.textContent = t; }
  function idle() {
    inCall = false;
    btn.disabled = false;
    btn.classList.remove("in-call");
    btn.textContent = "Talk to " + agentName;
  }

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

  function startAttempt() {
    attempt += 1;
    btn.disabled = true;
    setStatus("Connecting\u2026");
    var r = null;
    var done = false;
    var timer = null;
    function stopTimer() { if (timer) { clearTimeout(timer); timer = null; } }
    function giveUp() {
      if (done) return;
      done = true;
      stopTimer();
      if (r) { var old = r; if (room === old) room = null; try { old.disconnect(); } catch (e) {} }
      if (attempt < 2) { startAttempt(); return; }
      attempt = 0;
      idle();
      setStatus("Couldn\u2019t connect \u2014 tap to try again");
    }
    fetch("/api/token", { method: "POST" })
      .then(function (res) { if (!res.ok) throw new Error("token " + res.status); return res.json(); })
      .then(function (t) {
        if (done) return;
        r = new LivekitClient.Room();
        room = r;
        var E = LivekitClient.RoomEvent;
        r.on(E.TrackSubscribed, function (track) {
          if (room !== r) return;
          if (track.kind === "audio") {
            var el = track.attach();
            el.autoplay = true;
            document.body.appendChild(el);
          }
        });
        var sawEnded = false;
        function agentEnded(p) {
          return !!(p.attributes && p.attributes["companion.call_ended"] === "true");
        }
        // The agent closed the call (goodbye / silence / time limit): a normal end, no retry.
        function endByAgent() {
          if (room !== r) return;
          sawEnded = true;
          userEnded = true;
          stopTimer();
          if (inCall) { btn.disabled = true; r.disconnect(); return; } // Disconnected -> idle
          done = true; room = null; attempt = 0;
          try { r.disconnect(); } catch (e) {}
          idle(); setStatus("");
        }
        function onAgent(p) {
          if (room !== r || !isAgent(p)) return;
          if (agentEnded(p)) { endByAgent(); return; }
          stopTimer();
          if (!done) { done = true; inCall = true; attempt = 0; userEnded = false;
            btn.disabled = false; btn.classList.add("in-call"); btn.textContent = "End"; }
          updateFromAgent(p);
        }
        r.on(E.ParticipantConnected, onAgent);
        r.on(E.ParticipantAttributesChanged, function (changed, p) { onAgent(p); });
        r.on(E.ParticipantDisconnected, function (p) {
          if (room !== r || !isAgent(p)) return;
          if (sawEnded || agentEnded(p)) { endByAgent(); return; }
          // agent vanished without ending the call: a connection drop -> retry once
          if (inCall) { r.disconnect(); }
        });
        r.on(E.Disconnected, function () {
          if (room !== r) return; // superseded attempt: ignore
          room = null;
          stopTimer();
          if (inCall) {
            inCall = false;
            if (userEnded) { idle(); setStatus(""); return; }
            // connection dropped mid-call: retry once automatically
            attempt = 1;
            btn.classList.remove("in-call");
            startAttempt();
          } else if (!done) { giveUp(); }
        });
        timer = setTimeout(giveUp, 15000);
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
      userEnded = true;
      if (room) { room.disconnect(); } else { idle(); setStatus(""); }
      return;
    }
    attempt = 0;
    userEnded = false;
    startAttempt();
  });

  fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
    if (!s.configured) { window.location.href = "/setup"; return; }
    agentName = s.agent_name;
    idle();
  }).catch(function () { setStatus("Couldn’t load. Please reload."); });
})();
