/**
 * RapidRAG Chat Widget v2.0 — Demo-Ready
 *
 * Usage — paste before </body>:
 * <script>
 *   window.RAPIDRAG_CLIENT  = "your_client_id";
 *   window.RAPIDRAG_API     = "https://your-ngrok-url.ngrok.io";
 *   window.RAPIDRAG_NAME    = "Business Name";
 *   window.RAPIDRAG_COLOR   = "#2563eb";   // primary brand color
 *   window.RAPIDRAG_LOGO    = "";          // logo URL (optional)
 *   window.RAPIDRAG_GREETING = "Hi! How can I help you today?";
 *   window.RAPIDRAG_TAGLINE  = "Typically replies in seconds";
 *   window.RAPIDRAG_AVATAR_INITIALS = "AI"; // fallback if no logo
 * </script>
 * <script src="https://your-server.com/widget.js" async></script>
 */

(function () {
  "use strict";

  const CFG = {
    clientId:  window.RAPIDRAG_CLIENT   || "demo",
    apiBase:   window.RAPIDRAG_API      || "http://localhost:8000",
    name:      window.RAPIDRAG_NAME     || "Assistant",
    color:     window.RAPIDRAG_COLOR    || "#2563eb",
    logo:      window.RAPIDRAG_LOGO     || "",
    greeting:  window.RAPIDRAG_GREETING || "Hi there! 👋 How can I help you today?",
    tagline:   window.RAPIDRAG_TAGLINE  || "Typically replies in seconds",
    initials:  window.RAPIDRAG_AVATAR_INITIALS || "AI",
  };

  // ── Derive darker shade for gradients ──────────────
  function hexToHsl(hex) {
    let r = parseInt(hex.slice(1,3),16)/255,
        g = parseInt(hex.slice(3,5),16)/255,
        b = parseInt(hex.slice(5,7),16)/255;
    let max=Math.max(r,g,b), min=Math.min(r,g,b), h,s,l=(max+min)/2;
    if(max===min){h=s=0;}else{
      let d=max-min; s=l>0.5?d/(2-max-min):d/(max+min);
      switch(max){case r:h=((g-b)/d+(g<b?6:0))/6;break;case g:h=((b-r)/d+2)/6;break;default:h=((r-g)/d+4)/6;}
    }
    return [Math.round(h*360),Math.round(s*100),Math.round(l*100)];
  }
  function darken(hex, amt=15) {
    try {
      let [h,s,l]=hexToHsl(hex);
      return `hsl(${h},${s}%,${Math.max(0,l-amt)}%)`;
    } catch(e){ return hex; }
  }

  const COLOR     = CFG.color;
  const DARK      = darken(COLOR, 12);
  const LIGHT_BG  = (() => {
    try { let [h,s]=hexToHsl(COLOR); return `hsl(${h},${Math.max(20,s-30)}%,97%)`; } catch(e){ return "#f5f7ff"; }
  })();

  // ── CSS ───────────────────────────────────────────
  const css = `
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&display=swap');

    #rrag-root *, #rrag-root *::before, #rrag-root *::after {
      box-sizing: border-box; margin: 0; padding: 0;
      font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }

    /* ── FAB ── */
    #rrag-fab {
      position: fixed; bottom: 28px; right: 28px; z-index: 2147483646;
      width: 62px; height: 62px; border-radius: 50%; border: none; cursor: pointer;
      background: linear-gradient(135deg, ${COLOR}, ${DARK});
      box-shadow: 0 4px 24px ${COLOR}55, 0 2px 8px rgba(0,0,0,0.15);
      display: flex; align-items: center; justify-content: center;
      transition: transform 0.25s cubic-bezier(.34,1.56,.64,1),
                  box-shadow 0.2s ease, opacity 0.2s;
      outline: none;
    }
    #rrag-fab:hover {
      transform: scale(1.1);
      box-shadow: 0 8px 32px ${COLOR}77, 0 4px 12px rgba(0,0,0,0.18);
    }
    #rrag-fab:active { transform: scale(0.95); }
    #rrag-fab svg { transition: transform 0.3s cubic-bezier(.34,1.56,.64,1), opacity 0.2s; position: absolute; }
    #rrag-fab .rrag-icon-chat { opacity: 1; transform: scale(1) rotate(0deg); }
    #rrag-fab .rrag-icon-close { opacity: 0; transform: scale(0.5) rotate(-90deg); }
    #rrag-fab.active .rrag-icon-chat { opacity: 0; transform: scale(0.5) rotate(90deg); }
    #rrag-fab.active .rrag-icon-close { opacity: 1; transform: scale(1) rotate(0deg); }

    /* ── Unread badge ── */
    #rrag-badge {
      position: absolute; top: -2px; right: -2px;
      width: 20px; height: 20px; border-radius: 50%;
      background: #ef4444; color: white;
      font-size: 11px; font-weight: 700;
      display: flex; align-items: center; justify-content: center;
      border: 2px solid white;
      animation: rrag-badge-pop 0.4s cubic-bezier(.34,1.56,.64,1);
      z-index: 1;
    }
    #rrag-badge.hidden { display: none; }
    @keyframes rrag-badge-pop { from { transform: scale(0); } to { transform: scale(1); } }

    /* ── Panel ── */
    #rrag-panel {
      position: fixed; bottom: 104px; right: 28px; z-index: 2147483645;
      width: 380px; max-width: calc(100vw - 32px);
      height: 560px; max-height: calc(100vh - 130px);
      background: #ffffff;
      border-radius: 24px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.14), 0 4px 20px rgba(0,0,0,0.08);
      display: flex; flex-direction: column; overflow: hidden;
      pointer-events: none; opacity: 0;
      transform: translateY(16px) scale(0.96);
      transform-origin: bottom right;
      transition: opacity 0.22s ease, transform 0.28s cubic-bezier(.34,1.56,.64,1);
    }
    #rrag-panel.open {
      opacity: 1; transform: translateY(0) scale(1); pointer-events: all;
    }

    /* ── Header ── */
    #rrag-header {
      background: linear-gradient(135deg, ${COLOR} 0%, ${DARK} 100%);
      padding: 18px 20px 16px;
      display: flex; align-items: center; gap: 14px;
      flex-shrink: 0; position: relative; overflow: hidden;
    }
    #rrag-header::before {
      content: ''; position: absolute; inset: 0;
      background: radial-gradient(circle at 80% -20%, rgba(255,255,255,0.18) 0%, transparent 60%);
      pointer-events: none;
    }
    #rrag-avatar {
      width: 44px; height: 44px; border-radius: 14px;
      background: rgba(255,255,255,0.22);
      display: flex; align-items: center; justify-content: center;
      font-weight: 700; font-size: 15px; color: white;
      overflow: hidden; flex-shrink: 0;
      border: 1.5px solid rgba(255,255,255,0.3);
      backdrop-filter: blur(4px);
    }
    #rrag-avatar img { width: 100%; height: 100%; object-fit: cover; }
    #rrag-hinfo { flex: 1; min-width: 0; }
    #rrag-hname {
      color: white; font-weight: 600; font-size: 15px;
      letter-spacing: -0.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    #rrag-hstatus {
      color: rgba(255,255,255,0.82); font-size: 12px;
      display: flex; align-items: center; gap: 6px; margin-top: 2px;
    }
    .rrag-pulse {
      width: 8px; height: 8px; border-radius: 50%; background: #4ade80;
      position: relative; flex-shrink: 0;
    }
    .rrag-pulse::after {
      content: ''; position: absolute; inset: -3px; border-radius: 50%;
      background: #4ade8055; animation: rrag-pulse 2s infinite;
    }
    @keyframes rrag-pulse { 0%,100%{transform:scale(1);opacity:1;} 50%{transform:scale(1.6);opacity:0;} }

    /* ── Messages ── */
    #rrag-msgs {
      flex: 1; overflow-y: auto; padding: 20px 16px 12px;
      display: flex; flex-direction: column; gap: 4px;
      scroll-behavior: smooth;
      background: #fafafa;
    }
    #rrag-msgs::-webkit-scrollbar { width: 3px; }
    #rrag-msgs::-webkit-scrollbar-thumb { background: #e0e0e0; border-radius: 4px; }
    #rrag-msgs::-webkit-scrollbar-track { background: transparent; }

    /* Day divider */
    .rrag-divider {
      text-align: center; font-size: 11px; color: #b0b0b0;
      margin: 8px 0; font-weight: 500; letter-spacing: 0.04em;
    }

    /* Message row */
    .rrag-row { display: flex; align-items: flex-end; gap: 8px; margin-bottom: 2px; }
    .rrag-row.user { flex-direction: row-reverse; }
    .rrag-row.bot + .rrag-row.user,
    .rrag-row.user + .rrag-row.bot { margin-top: 8px; }

    .rrag-row-avatar {
      width: 28px; height: 28px; border-radius: 9px;
      background: linear-gradient(135deg, ${COLOR}, ${DARK});
      flex-shrink: 0; display: flex; align-items: center; justify-content: center;
      font-size: 11px; font-weight: 700; color: white;
      overflow: hidden;
    }
    .rrag-row-avatar img { width: 100%; height: 100%; object-fit: cover; }
    .rrag-row.user .rrag-row-avatar { display: none; }

    .rrag-bubble-wrap { display: flex; flex-direction: column; max-width: 78%; gap: 2px; }
    .rrag-row.user .rrag-bubble-wrap { align-items: flex-end; }

    .rrag-bubble {
      padding: 10px 14px; border-radius: 18px;
      font-size: 14px; line-height: 1.55; word-break: break-word;
      animation: rrag-msg-in 0.22s cubic-bezier(.34,1.4,.64,1);
    }
    @keyframes rrag-msg-in {
      from { opacity: 0; transform: translateY(8px) scale(0.96); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }
    .rrag-row.bot .rrag-bubble {
      background: white; color: #1a1a1a;
      border-bottom-left-radius: 5px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.07);
    }
    .rrag-row.user .rrag-bubble {
      background: linear-gradient(135deg, ${COLOR}, ${DARK});
      color: white; border-bottom-right-radius: 5px;
    }
    .rrag-time {
      font-size: 10px; color: #c0c0c0; padding: 0 2px; font-weight: 500;
    }

    /* Typing indicator */
    #rrag-typing-row { display: flex; align-items: flex-end; gap: 8px; }
    .rrag-typing {
      background: white; padding: 12px 16px;
      border-radius: 18px; border-bottom-left-radius: 5px;
      display: flex; align-items: center; gap: 5px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.07);
      animation: rrag-msg-in 0.22s ease;
    }
    .rrag-typing span {
      width: 7px; height: 7px; border-radius: 50%; background: #c8c8c8;
      animation: rrag-dot 1.3s infinite ease-in-out;
    }
    .rrag-typing span:nth-child(2) { animation-delay: 0.18s; }
    .rrag-typing span:nth-child(3) { animation-delay: 0.36s; }
    @keyframes rrag-dot {
      0%,80%,100%{ transform: scale(1); background: #c8c8c8; }
      40%{ transform: scale(1.35); background: ${COLOR}; }
    }

    /* ── Input ── */
    #rrag-footer {
      border-top: 1px solid #f0f0f0;
      padding: 12px 14px; background: white; flex-shrink: 0;
    }
    #rrag-input-row {
      display: flex; align-items: flex-end; gap: 8px;
      background: #f5f5f5; border-radius: 14px;
      padding: 8px 8px 8px 14px;
      transition: background 0.15s, box-shadow 0.15s;
      border: 1.5px solid transparent;
    }
    #rrag-input-row:focus-within {
      background: white;
      border-color: ${COLOR}44;
      box-shadow: 0 0 0 3px ${COLOR}14;
    }
    #rrag-input {
      flex: 1; background: transparent; border: none; outline: none;
      font-size: 14px; color: #1a1a1a; line-height: 1.5;
      resize: none; max-height: 90px; font-family: inherit;
    }
    #rrag-input::placeholder { color: #aaa; }
    #rrag-send-btn {
      width: 36px; height: 36px; border-radius: 10px;
      background: linear-gradient(135deg, ${COLOR}, ${DARK});
      border: none; cursor: pointer; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      transition: transform 0.15s, filter 0.15s, opacity 0.15s;
      outline: none;
    }
    #rrag-send-btn:hover { filter: brightness(1.08); transform: scale(1.05); }
    #rrag-send-btn:active { transform: scale(0.92); }
    #rrag-send-btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
    #rrag-send-btn svg { width: 17px; height: 17px; fill: white; }

    #rrag-footer-note {
      text-align: center; font-size: 10.5px; color: #c8c8c8;
      margin-top: 8px; font-weight: 500; letter-spacing: 0.01em;
    }

    /* ── Suggestion chips ── */
    #rrag-chips {
      display: flex; flex-wrap: wrap; gap: 7px;
      padding: 0 16px 12px;
      background: #fafafa;
    }
    .rrag-chip {
      font-size: 12.5px; font-weight: 500; color: ${COLOR};
      background: ${LIGHT_BG}; border: 1px solid ${COLOR}33;
      border-radius: 20px; padding: 5px 12px; cursor: pointer;
      transition: background 0.15s, transform 0.12s, border-color 0.15s;
      white-space: nowrap; font-family: inherit;
      animation: rrag-chip-in 0.3s cubic-bezier(.34,1.4,.64,1) backwards;
    }
    .rrag-chip:hover {
      background: ${COLOR}18; border-color: ${COLOR}66;
      transform: translateY(-1px);
    }
    .rrag-chip:active { transform: scale(0.96); }
    @keyframes rrag-chip-in { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:none} }

    /* ── Mobile ── */
    @media (max-width: 430px) {
      #rrag-panel { width: calc(100vw - 20px); right: 10px; bottom: 88px; }
      #rrag-fab { right: 18px; bottom: 18px; }
    }
  `;

  const styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ── DOM ───────────────────────────────────────────
  const root = document.createElement("div");
  root.id = "rrag-root";

  const avatarHtml = CFG.logo
    ? `<img src="${CFG.logo}" alt="${CFG.name}">`
    : CFG.initials;

  root.innerHTML = `
    <button id="rrag-fab" aria-label="Open chat assistant">
      <span id="rrag-badge" class="hidden">1</span>
      <svg class="rrag-icon-chat" width="26" height="26" viewBox="0 0 24 24" fill="none">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" fill="white"/>
      </svg>
      <svg class="rrag-icon-close" width="22" height="22" viewBox="0 0 24 24" fill="none">
        <path d="M18 6L6 18M6 6l12 12" stroke="white" stroke-width="2.5" stroke-linecap="round"/>
      </svg>
    </button>

    <div id="rrag-panel" role="dialog" aria-label="${CFG.name} chat">
      <div id="rrag-header">
        <div id="rrag-avatar">${avatarHtml}</div>
        <div id="rrag-hinfo">
          <div id="rrag-hname">${CFG.name}</div>
          <div id="rrag-hstatus">
            <span class="rrag-pulse"></span>
            <span>${CFG.tagline}</span>
          </div>
        </div>
      </div>

      <div id="rrag-msgs"></div>
      <div id="rrag-chips"></div>

      <div id="rrag-footer">
        <div id="rrag-input-row">
          <textarea id="rrag-input" placeholder="Ask me anything…" rows="1" aria-label="Message input"></textarea>
          <button id="rrag-send-btn" aria-label="Send">
            <svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2v7z"/></svg>
          </button>
        </div>
        <div id="rrag-footer-note">Powered by AI · Instant responses</div>
      </div>
    </div>
  `;
  document.body.appendChild(root);

  // ── Refs ──────────────────────────────────────────
  const fab       = document.getElementById("rrag-fab");
  const badge     = document.getElementById("rrag-badge");
  const panel     = document.getElementById("rrag-panel");
  const msgsEl    = document.getElementById("rrag-msgs");
  const chipsEl   = document.getElementById("rrag-chips");
  const inputEl   = document.getElementById("rrag-input");
  const sendBtn   = document.getElementById("rrag-send-btn");

  // ── State ─────────────────────────────────────────
  const SESSION_ID = Math.random().toString(36).slice(2, 11);
  let isOpen    = false;
  let isBusy    = false;
  let hasOpened = false;

  // ── Helpers ───────────────────────────────────────
  function fmt(d) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function addMsg(text, role) {
    // Remove chips when user starts talking
    if (role === "user") chipsEl.innerHTML = "";

    const row = document.createElement("div");
    row.className = `rrag-row ${role}`;

    const miniAvatar = CFG.logo
      ? `<img src="${CFG.logo}" alt="">`
      : CFG.initials;

    row.innerHTML = `
      ${role === "bot" ? `<div class="rrag-row-avatar">${miniAvatar}</div>` : ""}
      <div class="rrag-bubble-wrap">
        <div class="rrag-bubble">${escHtml(text).replace(/\n/g,"<br>")}</div>
        <span class="rrag-time">${fmt(new Date())}</span>
      </div>
    `;
    msgsEl.appendChild(row);
    scrollBottom();
    return row;
  }

  function escHtml(s) {
    return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  function showTyping() {
    const row = document.createElement("div");
    row.id = "rrag-typing-row";
    row.className = "rrag-typing-row";
    const miniAvatar = CFG.logo ? `<img src="${CFG.logo}" alt="">` : CFG.initials;
    row.innerHTML = `
      <div class="rrag-row-avatar">${miniAvatar}</div>
      <div class="rrag-typing"><span></span><span></span><span></span></div>
    `;
    msgsEl.appendChild(row);
    scrollBottom();
  }

  function hideTyping() {
    const t = document.getElementById("rrag-typing-row");
    if (t) t.remove();
  }

  function scrollBottom() {
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function addChips(questions) {
    chipsEl.innerHTML = "";
    questions.forEach((q, i) => {
      const btn = document.createElement("button");
      btn.className = "rrag-chip";
      btn.textContent = q;
      btn.style.animationDelay = `${i * 0.07}s`;
      btn.addEventListener("click", () => {
        inputEl.value = q;
        sendMsg();
      });
      chipsEl.appendChild(btn);
    });
  }

  // ── Toggle ────────────────────────────────────────
  fab.addEventListener("click", () => {
    isOpen = !isOpen;
    fab.classList.toggle("active", isOpen);
    panel.classList.toggle("open", isOpen);
    badge.classList.add("hidden");

    if (isOpen && !hasOpened) {
      hasOpened = true;
      
      // Fetch dynamic config from backend
      fetch(`${CFG.apiBase}/api/v1/${CFG.clientId}/widget-config`)
        .then(res => res.json())
        .then(data => {
            const dynamicGreeting = data.greeting || CFG.greeting;
            const dynamicChips = data.chips || [
              "What services do you offer?",
              "How much does it cost?",
              "How do I get started?",
            ];
            
            // Greeting with a short delay for polish
            setTimeout(() => {
              addMsg(dynamicGreeting, "bot");
              // Show suggestion chips after greeting
              setTimeout(() => {
                addChips(dynamicChips);
              }, 400);
            }, 200);
        })
        .catch(err => {
            console.error("Failed to load widget config:", err);
            // Fallback to static config
            setTimeout(() => {
              addMsg(CFG.greeting, "bot");
              setTimeout(() => {
                addChips([
                  "What services do you offer?",
                  "How much does it cost?",
                  "How do I get started?",
                ]);
              }, 400);
            }, 200);
        });
    }

    if (isOpen) setTimeout(() => inputEl.focus(), 280);
  });

  // Show badge after 4 seconds if user hasn't opened
  setTimeout(() => {
    if (!hasOpened) {
      badge.classList.remove("hidden");
    }
  }, 4000);

  // ── Send ──────────────────────────────────────────
  async function sendMsg() {
    const text = inputEl.value.trim();
    if (!text || isBusy) return;

    isBusy = true;
    sendBtn.disabled = true;
    inputEl.value = "";
    inputEl.style.height = "auto";

    addMsg(text, "user");
    showTyping();

    try {
      const res = await fetch(`${CFG.apiBase}/api/v1/${CFG.clientId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: text, session_id: SESSION_ID }),
      });

      hideTyping();

      if (!res.ok) {
        addMsg("I'm having a little trouble right now. Please try again in a moment.", "bot");
        return;
      }

      const data = await res.json();
      addMsg(data.reply || "I'm not sure about that — please reach out to us directly.", "bot");

    } catch (err) {
      hideTyping();
      addMsg("Connection issue. Please check your internet and try again.", "bot");
    } finally {
      isBusy = false;
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  sendBtn.addEventListener("click", sendMsg);
  inputEl.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); }
  });
  inputEl.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 90) + "px";
  });

})();