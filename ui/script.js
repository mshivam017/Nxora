/* ════════════════════════════════════════════
   NXORA — script.js  (V8 Desktop)
════════════════════════════════════════════ */

/* ─── Bridge ─── */
let backend = null;
new QWebChannel(qt.webChannelTransport, ch => { backend = ch.objects.backend; });
function _send(t) { if (t && backend) backend.receive_text(t); }

/* ─── Greeting ─── */
(function () {
  const h = new Date().getHours();
  const el = document.getElementById('greetingText');
  if (!el) return;
  if (h < 12) el.textContent = 'Good Morning, Boss.';
  else if (h < 17) el.textContent = 'Good Afternoon, Boss.';
  else el.textContent = 'Good Evening, Boss.';
})();

/* ─── View routing ─── */
const VIEWS = ['home', 'chat', 'voice', 'history', 'settings'];

function showView(name) {
  VIEWS.forEach(v => {
    const el = document.getElementById('view-' + v);
    const sn = document.getElementById('sn-' + v);
    if (el) el.classList.toggle('active', v === name);
    if (sn) sn.classList.toggle('active', v === name);
  });

  const titles = { home: 'Home', chat: 'Smart Chat', voice: 'Voice Analysis', history: 'History', settings: 'Settings' };
  const el = document.getElementById('topbarTitle');
  if (el) el.textContent = titles[name] || name;
}

/* ─── Home quick sends ─── */
function sendQuick(text) {
  showView('chat');
  setTimeout(() => { _appendUserBubble(text); _send(text); setOrbState('processing'); }, 280);
}

/* ─── Keyboard shortcuts ─── */
document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'k') { e.preventDefault(); showView('voice'); }
  if (e.key === 'Escape') showView('home');
});

/* ─── Textarea auto resize ─── */
const chatInput = document.getElementById('chatInput');
chatInput.addEventListener('input', function () {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 130) + 'px';
});
chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitChat(); }
});

document.getElementById('sendBtn').addEventListener('click', submitChat);

function submitChat() {
  const txt = chatInput.value.trim();
  if (!txt) return;
  showView('chat');
  _appendUserBubble(txt);
  _send(txt);
  setOrbState('processing');
  chatInput.value = '';
  chatInput.style.height = 'auto';
}

/* ─── Voice panel ─── */
let voiceOn = false;

function vSpeak() {
  if (backend) backend.trigger_voice();
  voiceOn = !voiceOn;
  const btn = document.getElementById('vMicBtn');
  btn.classList.toggle('recording', voiceOn);
  const dots = document.getElementById('vDots');
  const stTxt = document.getElementById('vStatusText');
  if (voiceOn) { stTxt.textContent = 'Listening...'; dots.style.opacity = '1'; }
  else { stTxt.textContent = 'Ready to listen'; dots.style.opacity = '0.4'; }
}

function vPause() {
  voiceOn = false;
  document.getElementById('vMicBtn').classList.remove('recording');
  document.getElementById('vStatusText').textContent = 'Paused';
  document.getElementById('vDots').style.opacity = '0.4';
}

/* ─── Orb state ─── */
function setOrbState(state) {
  const mo = document.getElementById('miniOrb');
  const ml = document.getElementById('miniOrbLabel');
  const sp = document.getElementById('vSphere');
  const gw = document.getElementById('vGlow');
  const pill = document.getElementById('statusPill');
  const pillTxt = document.getElementById('pillText');
  const typingRow = document.getElementById('typingRow');

  if (!mo) return;
  mo.className = 'mini-orb';
  pill.className = 'status-pill';

  if (state === 'listening') {
    mo.classList.add('listen');
    ml.textContent = 'Listening';
    pill.classList.add('active');
    pillTxt.textContent = 'LISTENING';
    if (sp) sp.style.animationDuration = '2s';
    if (gw) gw.style.background = 'radial-gradient(circle, rgba(139,92,246,0.45) 0%, transparent 70%)';
    typingRow.classList.remove('show');
    showView('voice');
    voiceOn = true;
    document.getElementById('vMicBtn').classList.add('recording');
    document.getElementById('vStatusText').textContent = 'Listening...';
  } else if (state === 'processing') {
    mo.classList.add('think');
    ml.textContent = 'Thinking';
    pill.classList.add('active');
    pillTxt.textContent = 'PROCESSING';
    if (sp) sp.style.animationDuration = '1.5s';
    if (gw) gw.style.background = 'radial-gradient(circle, rgba(255,85,0,0.3) 0%, transparent 70%)';
    typingRow.classList.add('show');
    _scrollFeed();
  } else if (state === 'online') {
    mo.classList.add('online');
    ml.textContent = 'Online';
    pill.classList.add('active');
    pillTxt.textContent = 'ONLINE';
    if (sp) sp.style.animationDuration = '5s';
    if (gw) gw.style.background = 'radial-gradient(circle, rgba(139,92,246,0.22) 0%, transparent 70%)';
    typingRow.classList.remove('show');
  } else {
    ml.textContent = 'Standby';
    pillTxt.textContent = 'Standby';
    typingRow.classList.remove('show');
    if (sp) sp.style.animationDuration = '5s';
  }
}

/* ─── Message rendering ─── */
function _appendUserBubble(text) {
  const feed = document.getElementById('chatFeed');
  const typingRow = document.getElementById('typingRow');

  const row = document.createElement('div');
  row.className = 'msg-row user-row';

  const av = document.createElement('div');
  av.className = 'user-av-chat';
  av.innerHTML = '<i class="fa-regular fa-user"></i>';

  const bub = document.createElement('div');
  bub.className = 'bubble user-bubble';
  bub.textContent = text;

  row.appendChild(av);
  row.appendChild(bub);
  feed.insertBefore(row, typingRow);

  gsap.fromTo(row, { opacity: 0, x: 30 }, { opacity: 1, x: 0, duration: 0.3, ease: 'power2.out' });

  // Update voice transcript
  const vt = document.getElementById('vTranscript');
  if (vt) vt.innerHTML = `<span class="v-tr-muted">"</span>${text}<span class="v-tr-muted">"</span>`;

  feed.appendChild(typingRow);
  _scrollFeed();
}

function _appendAIBubble(text) {
  const feed = document.getElementById('chatFeed');
  const typingRow = document.getElementById('typingRow');

  const row = document.createElement('div');
  row.className = 'msg-row ai-row';

  const av = document.createElement('div');
  av.className = 'ai-av';
  av.innerHTML = '<i class="fa-solid fa-bolt"></i>';

  const bub = document.createElement('div');
  bub.className = 'bubble ai-bubble';

  if (text.includes('```')) {
    const parts = text.split('```');
    for (let i = 0; i < parts.length; i++) {
      if (i % 2 === 1) {
        const lines = parts[i].split('\n');
        const lang = lines[0].trim() || 'code';
        const code = (lang !== 'code' ? lines.slice(1) : lines).join('\n').trim();
        const wrap = document.createElement('div'); wrap.className = 'code-wrap';
        const top = document.createElement('div'); top.className = 'code-top';
        const ls = document.createElement('span'); ls.textContent = lang.toUpperCase();
        const cb = document.createElement('button'); cb.className = 'copy-btn';
        cb.innerHTML = '<i class="fa-regular fa-clipboard"></i> Copy';
        cb.onclick = () => { navigator.clipboard.writeText(code); cb.innerHTML = '<i class="fa-solid fa-check"></i>'; setTimeout(() => { cb.innerHTML = '<i class="fa-regular fa-clipboard"></i> Copy'; }, 2000); };
        top.appendChild(ls); top.appendChild(cb);
        const pre = document.createElement('pre'); const cEl = document.createElement('code');
        if (lang !== 'code') cEl.className = 'language-' + lang;
        cEl.textContent = code; pre.appendChild(cEl); wrap.appendChild(top); wrap.appendChild(pre);
        bub.appendChild(wrap);
        setTimeout(() => hljs.highlightElement(cEl), 0);
      } else if (parts[i].trim()) {
        const sp = document.createElement('span');
        sp.style.cssText = 'white-space:pre-wrap;display:block;margin-bottom:4px';
        sp.textContent = parts[i]; bub.appendChild(sp);
      }
    }
  } else {
    bub.textContent = text;
  }

  row.appendChild(av);
  row.appendChild(bub);
  feed.insertBefore(row, typingRow);

  gsap.fromTo(row, { opacity: 0, x: -20 }, { opacity: 1, x: 0, duration: 0.4, ease: 'power3.out' });
  setOrbState('online');

  // Update voice transcript
  const vt = document.getElementById('vTranscript');
  if (vt) {
    const preview = text.length > 90 ? text.slice(0, 90) + '...' : text;
    vt.innerHTML = `<span class="v-tr-muted">${preview}</span>`;
  }

  feed.appendChild(typingRow);
  _scrollFeed();
}

function _scrollFeed() {
  const feed = document.getElementById('chatFeed');
  setTimeout(() => feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' }), 60);
}

/* ─── Clear session ─── */
function clearSession() {
  const feed = document.getElementById('chatFeed');
  feed.innerHTML = `
    <div class="msg-row ai-row" id="welcomeMsg">
      <div class="ai-av"><i class="fa-solid fa-bolt"></i></div>
      <div class="bubble ai-bubble">Hi Boss! How can I assist you today? 👋<br>
        <small style="color:#9999c0;font-size:11px">Try saying: "what is my CPU usage" or "tell me a joke"</small>
      </div>
    </div>
    <div class="typing-row" id="typingRow">
      <div class="ai-av"><i class="fa-solid fa-bolt"></i></div>
      <div class="typing-bub">
        <div class="tdot"></div><div class="tdot"></div><div class="tdot"></div>
      </div>
    </div>`;
  setOrbState('standby');
  document.getElementById('vTranscript').innerHTML = `<span class="v-tr-muted">What would you like me to do, </span><strong>Boss?</strong>`;
}

/* ─── Python Bridge ─── */
function appendMessage(sender, text, type) {
  if (type === 'system') { return; }
  if (sender === 'You') { return; } // rendered locally
  showView('chat');
  _appendAIBubble(text);
}

function updateStatus(statusText, colorConfig) {
  setOrbState(colorConfig);
}
