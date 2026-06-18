const THINKING_PHRASES = [
    "Je consulte mes neurones...",
    "Hmm, laisse-moi réfléchir...",
    "Je mobilise mes deux neurones non connectés...",
    "Calcul quantique en cours...",
    "Je demande à ChatGPT...",
    "Lecture des archives du MiniTel...",
    "Je demande à Tek les outils nécessaires...",
    "J'emprunte des balles au BDS...",
    "Je demande du café à TNS...",
    "Je cours chercher une flutte au BDA...",
    "Je fais semblant de comprendre...",
];

const SLOW_PHRASE = "Ouh hihi ha, ouh hihi ha...";

const WRITING_PHRASES = [
    "En train d'écrire...",
    "Je demande à Gemini d'écrire le prompt à ma place...",
    "je tape avec la machine à écrire du MiniTel...",
    "je demande au BDE s'ils ont la réponse...pas sûr...",
    "Presque là !",
];

const ALL_CHIPS = [
    { label: 'salles libres', query: 'Salles libres maintenant' },
    { label: 'drive 1A', query: 'Infos drive 1A' },
    { label: 'lore TN', query: 'Lore de TELECOM Nancy' },
    { label: 'menu du self', query: 'Menu du self cette semaine' },
    { label: 'clubs & assos', query: 'Liste des clubs et associations à TELECOM Nancy' },
    { label: 'agenda BDE', query: "Quels sont les prochains événements du BDE ?" },
    { label: 'jobs & stages', query: "Comment trouver un stage ou une alternance depuis TN ?" },
    { label: 'imprimer à TN', query: "Où et comment imprimer à TELECOM Nancy ?" },
    { label: 'options 3A', query: "Quelles sont les options disponibles en 3ème année ?" },
    { label: 'wifi campus', query: "Comment se connecter au wifi de TELECOM Nancy ?" },
    { label: 'histoire de TN', query: "Quelle est l'histoire de TELECOM Nancy ?" },
];

function randomFrom(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

function shuffle(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

function formatTime() {
    const now = new Date();
    return now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0');
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('chat-form');
    const inp = document.getElementById('inp');
    const messagesContainer = document.getElementById('messages');
    const sendBtn = document.getElementById('sbtn');
    const duckyImg = document.getElementById('sidebar-ducky');
    const scrollBtn = document.getElementById('scroll-btn');
    const themeToggle = document.getElementById('theme-toggle');
    const hamburger = document.getElementById('hamburger');
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');

    // --- Theme toggle ---
    function updateThemeLabel() {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        themeToggle.textContent = isDark ? '☀ mode clair' : '☾ mode sombre';
    }
    updateThemeLabel();

    themeToggle.addEventListener('click', () => {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const next = isDark ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
        updateThemeLabel();
    });

    // --- Mobile sidebar ---
    hamburger.addEventListener('click', () => {
        sidebar.classList.add('open');
        backdrop.classList.add('visible');
    });
    backdrop.addEventListener('click', () => {
        sidebar.classList.remove('open');
        backdrop.classList.remove('visible');
    });

    // --- Chips aléatoires ---
    const chipsContainer = document.getElementById('chips-container');
    shuffle(ALL_CHIPS).slice(0, 4).forEach(chip => {
        const btn = document.createElement('button');
        btn.className = 'chip';
        btn.textContent = chip.label;
        btn.addEventListener('click', () => {
            inp.value = chip.query;
            autoResize();
            form.dispatchEvent(new Event('submit'));
        });
        chipsContainer.appendChild(btn);
    });

    // --- Textarea auto-resize ---
    function autoResize() {
        inp.style.height = 'auto';
        inp.style.height = Math.min(inp.scrollHeight, 120) + 'px';
    }
    inp.addEventListener('input', autoResize);

    // Enter = envoyer, Shift+Enter = saut de ligne
    inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (!sendBtn.disabled) form.dispatchEvent(new Event('submit'));
        }
    });

    // --- Scroll indicator ---
    messagesContainer.addEventListener('scroll', () => {
        const distFromBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight;
        scrollBtn.classList.toggle('visible', distFromBottom > 120);
    });
    scrollBtn.addEventListener('click', scrollToBottom);

    // --- Form submit ---
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = inp.value.trim();
        if (!text) return;

        const emptyState = document.getElementById('empty-state');
        const isFirstMessage = !!emptyState;
        if (emptyState) emptyState.remove();

        if (isFirstMessage) {
            const shortTitle = text.length > 40 ? text.slice(0, 40).trimEnd() + '…' : text;
            const convItem = document.querySelector('.conv-item.active');
            if (convItem) convItem.textContent = shortTitle;
            document.title = shortTitle + ' – TN-GPT';
        }

        appendMessage('user', text);
        inp.value = '';
        inp.style.height = 'auto';
        sendBtn.disabled = true;

        duckyImg.classList.add('spinning');
        const thinkingDiv = appendThinking(randomFrom(THINKING_PHRASES));

        let oiiaAudio = null;
        const slowTimer = setTimeout(() => {
            thinkingDiv.querySelector('em').textContent = SLOW_PHRASE;
            oiiaAudio = new Audio('/static/sounds/OIIA_OIIA.mp3');
            oiiaAudio.loop = true;
            oiiaAudio.play();
        }, 3000);

        let rawText = '';
        let textContainer = null;
        let bubbleContainer = null;

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text })
            });

            if (!response.ok) throw new Error();

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let firstChunk = true;

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);

                if (firstChunk) {
                    const trimmed = chunk.trimStart();
                    if (!trimmed) continue;
                    clearTimeout(slowTimer);
                    if (oiiaAudio) { oiiaAudio.pause(); oiiaAudio = null; }
                    duckyImg.classList.remove('spinning');
                    thinkingDiv.querySelector('em').textContent = randomFrom(WRITING_PHRASES);
                    const assistantMsgDiv = appendMessage('assistant', '');
                    textContainer = assistantMsgDiv.querySelector('.msg-text');
                    bubbleContainer = assistantMsgDiv.querySelector('.msg-bubble');
                    rawText = trimmed;
                    textContainer.innerHTML = marked.parse(rawText);
                    firstChunk = false;
                } else {
                    rawText += chunk;
                    textContainer.innerHTML = marked.parse(rawText);
                }
                if (bubbleContainer) bubbleContainer.dataset.raw = rawText;
                scrollToBottom();
            }
        } catch {
            clearTimeout(slowTimer);
            if (oiiaAudio) { oiiaAudio.pause(); oiiaAudio = null; }
            thinkingDiv.remove();
            appendMessage('assistant', "Désolé, j'ai eu un bug de transmission. Réessaie ?");
        } finally {
            thinkingDiv.remove();
            duckyImg.classList.remove('spinning');
            sendBtn.disabled = false;
            inp.focus();
        }
    });

    function appendThinking(phrase) {
        const div = document.createElement('div');
        div.className = 'msg assistant';
        div.innerHTML = `<div class="msg-text thinking-text"><em>${phrase}</em></div>`;
        messagesContainer.appendChild(div);
        scrollToBottom();
        return div;
    }

    function appendMessage(role, content) {
        const time = formatTime();
        const msgDiv = document.createElement('div');
        msgDiv.className = `msg ${role}`;

        const whoDiv = document.createElement('div');
        whoDiv.className = 'msg-who';
        whoDiv.innerHTML = `${role === 'user' ? 'vous' : 'TN-GPT'} <span class="msg-time">${time}</span>`;

        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'msg-bubble';
        if (role === 'user') bubbleDiv.dataset.raw = content;

        const textDiv = document.createElement('div');
        textDiv.className = 'msg-text';
        if (role === 'assistant') {
            textDiv.innerHTML = content ? marked.parse(content) : '';
        } else {
            textDiv.textContent = content;
        }

        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.title = 'Copier';
        copyBtn.textContent = '⎘';
        copyBtn.addEventListener('click', () => {
            const raw = bubbleDiv.dataset.raw || textDiv.textContent;
            navigator.clipboard.writeText(raw).then(() => {
                copyBtn.textContent = '✓';
                setTimeout(() => { copyBtn.textContent = '⎘'; }, 1500);
            });
        });

        bubbleDiv.appendChild(textDiv);
        msgDiv.appendChild(whoDiv);
        msgDiv.appendChild(bubbleDiv);
        msgDiv.appendChild(copyBtn);
        messagesContainer.appendChild(msgDiv);
        scrollToBottom();
        return msgDiv;
    }

    function scrollToBottom() {
        messagesContainer.scrollTo({ top: messagesContainer.scrollHeight, behavior: 'smooth' });
    }
});
