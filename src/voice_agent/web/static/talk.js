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
  var agentP = null;

  // --- Siri-like orb: the state picks the animation, the voice volume sets --level ---
  var orb = document.querySelector(".orb");
  var calm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var meters = { mic: null, agent: null };
  var level = 0;
  var raf = 0;
  function meterFor(track) {
    var make = window.LivekitClient && LivekitClient.createAudioAnalyser;
    if (!make || !track) return null;
    try { return make(track); } catch (e) { return null; }
  }
  function dropMeter(which) {
    var m = meters[which];
    meters[which] = null;
    if (m) { try { m.cleanup(); } catch (e) {} }
  }
  // The local analyser is quick to react; LiveKit's audioLevel is a slower fallback.
  function readLevel(which, p) {
    var v = 0;
    if (meters[which]) { try { v = meters[which].calculateVolume() || 0; } catch (e) {} }
    return Math.max(v, (p && p.audioLevel) || 0);
  }
  function tick() {
    var s = orb.getAttribute("data-state");
    var target = 0;
    if (room && s === "speaking") target = readLevel("agent", agentP);
    else if (room && s === "listening") target = readLevel("mic", room.localParticipant);
    target = Math.min(1, Math.sqrt(target) * 1.6);
    level += (target - level) * (target > level ? 0.3 : 0.08); // rise fast, settle slowly
    orb.style.setProperty("--level", level.toFixed(3));
    raf = requestAnimationFrame(tick);
  }
  function setOrb(state) {
    orb.setAttribute("data-state", state);
    var live = state === "listening" || state === "thinking" || state === "speaking";
    orb.classList.toggle("in-call", live);
    if (live) {
      if (!raf && !calm) raf = requestAnimationFrame(tick);
      return;
    }
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    level = 0;
    orb.style.setProperty("--level", "0");
    dropMeter("mic"); dropMeter("agent");
  }

  var INSECURE_MSG = "This page needs a secure https:// link to use the microphone. " +
    "Please ask your family to check the setup.";
  var MIC_MSG = "Please allow the microphone, then tap to try again.";

  function setStatus(t) { statusEl.textContent = t; }
  // Browsers only allow the microphone on https:// pages (or localhost).
  function micUnavailable() {
    return !window.isSecureContext || !navigator.mediaDevices ||
      !navigator.mediaDevices.getUserMedia;
  }
  function isMicError(e) {
    var n = e && e.name;
    return n === "NotAllowedError" || n === "NotFoundError" || n === "SecurityError";
  }
  function idle() {
    inCall = false;
    btn.disabled = false;
    btn.classList.remove("in-call");
    btn.textContent = "Talk to " + agentName;
    agentP = null;
    setOrb("idle");
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
    if (l) { setStatus(l); setOrb(s); }
  }

  function startAttempt() {
    attempt += 1;
    btn.disabled = true;
    setStatus("Connecting\u2026");
    setOrb("connecting");
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
    // Mic denied or missing: retrying cannot help, so stop and ask (no auto-retry).
    function micFailed() {
      if (room !== r) return; // this attempt was already given up or superseded
      done = true;
      stopTimer();
      room = null; // our Disconnected handler now ignores this room
      inCall = false;
      attempt = 0;
      try { r.disconnect(); } catch (e) {}
      idle();
      setStatus(MIC_MSG);
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
            if (!calm) { dropMeter("agent"); meters.agent = meterFor(track); }
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
          agentP = p;
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
          .then(function () {
            return r.localParticipant.setMicrophoneEnabled(true).then(function () {
              if (room === r && !calm) {
                var pub = r.localParticipant.getTrackPublication(LivekitClient.Track.Source.Microphone);
                dropMeter("mic");
                meters.mic = meterFor(pub && pub.track);
              }
              r.remoteParticipants.forEach(function (p) { onAgent(p); });
            }, function (e) {
              if (isMicError(e)) { micFailed(); return; }
              throw e;
            });
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
    if (micUnavailable()) { setStatus(INSECURE_MSG); return; }
    attempt = 0;
    userEnded = false;
    startAttempt();
  });

  fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
    if (!s.configured) { window.location.href = "/setup"; return; }
    agentName = s.agent_name;
    idle();
    if (micUnavailable()) setStatus(INSECURE_MSG);
  }).catch(function () { setStatus("Couldn’t load. Please reload."); });
})();
