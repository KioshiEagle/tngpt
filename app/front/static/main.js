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
    { label: "Planning de l'inté", query: "Balance le planning de l'intégration 2026" },
    { label: 'lore TN', query: 'Lore de TELECOM Nancy' },
    { label: 'menu du self', query: 'Menu du self cette semaine' },
    { label: 'clubs & assos', query: 'Liste des clubs et associations à TELECOM Nancy' },
    { label: 'agenda BDE', query: "Quels sont les prochains événements du BDE ?" },
    { label: 'jobs & stages', query: "Comment trouver un stage ou une alternance depuis TN ?" },
    { label: 'imprimer à TN', query: "Où et comment imprimer à TELECOM Nancy ?" },
    { label: 'Appros à TN', query: "Quelles sont les approfondissements disponibles en à TN ?" },
    { label: 'wifi campus', query: "Comment se connecter au wifi de TELECOM Nancy ?" },
    { label: 'Histoire de TN', query: "Quelle est l'histoire de TELECOM Nancy ?" },
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
    const convList = document.getElementById('conv-list');

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

    // --- User menu ---
    const userTab = document.getElementById('user-tab');
    const userMenu = document.getElementById('user-menu');
    userTab.addEventListener('click', (e) => {
        e.stopPropagation();
        userMenu.classList.toggle('open');
    });
    document.addEventListener('click', (e) => {
        if (!userMenu.contains(e.target)) userMenu.classList.remove('open');
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

    // --- Landing mode ---
    if (document.getElementById('empty-state')) {
        document.body.classList.add('landing');
    }

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

    // --- Stop button state ---
    let abortController = null;

    function setStreaming(active) {
        if (active) {
            sendBtn.textContent = '⏹ stop';
            sendBtn.disabled = false;
            sendBtn.classList.add('stop-mode');
        } else {
            sendBtn.textContent = 'envoyer →';
            sendBtn.disabled = false;
            sendBtn.classList.remove('stop-mode');
            abortController = null;
        }
    }

    sendBtn.addEventListener('click', (e) => {
        if (sendBtn.classList.contains('stop-mode')) {
            e.preventDefault();
            if (abortController) abortController.abort();
        }
    });

    let currentConversationId = null;

    // --- Liste des conversations (sidebar) ---
    function setActiveConvItem(id) {
        convList.querySelectorAll('.conv-item').forEach((item) => {
            item.classList.toggle('active', item.dataset.id === String(id));
        });
    }

    function addConvItem(conv, { prepend = false } = {}) {
        const row = document.createElement('div');
        row.className = 'conv-row';

        const item = document.createElement('a');
        item.href = '#';
        item.className = 'conv-item';
        item.dataset.id = String(conv.id);
        item.textContent = conv.title || 'Sans titre';
        item.addEventListener('click', (e) => {
            e.preventDefault();
            if (item.dataset.id !== String(currentConversationId)) openConversation(conv.id);
        });

        const delBtn = document.createElement('button');
        delBtn.className = 'conv-del-btn';
        delBtn.title = 'Supprimer';
        delBtn.textContent = '×';
        delBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!confirm('Supprimer cette conversation ?')) return;
            const res = await fetch(`/conversations/${conv.id}`, { method: 'DELETE' });
            if (!res.ok) return;
            row.remove();
            if (String(conv.id) === String(currentConversationId)) {
                window.location.assign('/');
            }
        });

        row.appendChild(item);
        row.appendChild(delBtn);
        convList[prepend ? 'prepend' : 'appendChild'](row);
        return row;
    }

    async function loadConversations() {
        try {
            const res = await fetch('/conversations');
            if (!res.ok) return;
            const conversations = await res.json();
            conversations.forEach((c) => addConvItem(c));
        } catch {
            // Liste indisponible : la sidebar reste vide, le chat fonctionne quand même.
        }
    }

    async function openConversation(id) {
        const res = await fetch(`/conversations/${id}`);
        if (!res.ok) return;
        const conv = await res.json();

        currentConversationId = conv.id;
        messagesContainer.innerHTML = '';
        document.getElementById('empty-state')?.remove();
        document.getElementById('chips-container')?.remove();
        document.body.classList.remove('landing');
        conv.messages.forEach((m) => appendMessage(m.role, m.content));
        document.title = (conv.title || 'TN-GPT') + ' – TN-GPT';
        setActiveConvItem(id);
    }

    loadConversations();

    // --- Form submit ---
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = inp.value.trim();
        if (!text || sendBtn.classList.contains('stop-mode')) return;

        const emptyState = document.getElementById('empty-state');
        if (emptyState) {
            emptyState.remove();
            document.getElementById('chips-container')?.remove();
            document.body.classList.remove('landing');
        }

        appendMessage('user', text);
        inp.value = '';
        inp.style.height = 'auto';

        duckyImg.classList.add('spinning');
        const thinkingDiv = appendThinking(randomFrom(THINKING_PHRASES));
        setStreaming(true);

        // Audio créé dans le contexte du clic pour contourner la politique autoplay
        const oiiaAudioElem = new Audio('/static/sounds/OIIA_OIIA.mp3');
        oiiaAudioElem.loop = true;
        let oiiaAudio = null;
        const slowTimer = setTimeout(() => {
            thinkingDiv.querySelector('em').textContent = SLOW_PHRASE;
            oiiaAudio = oiiaAudioElem;
            oiiaAudio.play().catch(() => {});
        }, 7000);

        let rawText = '';
        let textContainer = null;
        let bubbleContainer = null;
        let rafPending = false;

        function scheduleRender() {
            if (rafPending) return;
            rafPending = true;
            requestAnimationFrame(() => {
                rafPending = false;
                if (textContainer) textContainer.innerHTML = marked.parse(rawText);
                if (bubbleContainer) bubbleContainer.dataset.raw = rawText;
                scrollToBottom();
            });
        }

        abortController = new AbortController();

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, conversation_id: currentConversationId }),
                signal: abortController.signal,
            });

            if (response.status === 401) {
                const { login_url } = await response.json();
                window.location.assign(login_url || '/auth/login');
                return;
            }

            // 429 : quota journalier atteint ou rafale trop rapide. On affiche le
            // message du serveur ; le nettoyage (indicateur, focus) se fait dans finally.
            if (response.status === 429) {
                const data = await response.json().catch(() => ({}));
                appendMessage('assistant', data.error || "Tu as atteint ta limite pour le moment. Réessaie plus tard.");
                return;
            }

            if (!response.ok) throw new Error();

            // La conversation est créée côté serveur dès le premier message : on
            // l'ajoute à la sidebar dès que son id est connu, sans attendre la
            // fin du streaming.
            const newId = response.headers.get('X-Conversation-Id');
            if (newId && currentConversationId === null) {
                currentConversationId = newId;
                const shortTitle = text.length > 40 ? text.slice(0, 40).trimEnd() + '…' : text;
                addConvItem({ id: newId, title: shortTitle }, { prepend: true });
                setActiveConvItem(newId);
                document.title = shortTitle + ' – TN-GPT';
            }

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
                    textContainer.classList.add('streaming');
                    rawText = trimmed;
                    firstChunk = false;
                } else {
                    rawText += chunk;
                }
                scheduleRender();
            }
        } catch (err) {
            clearTimeout(slowTimer);
            if (oiiaAudio) { oiiaAudio.pause(); oiiaAudio = null; }
            thinkingDiv.remove();
            if (err.name !== 'AbortError') {
                appendMessage('assistant', "Désolé, j'ai eu un bug de transmission. Réessaie ?");
            }
        } finally {
            clearTimeout(slowTimer);
            if (oiiaAudio) { oiiaAudio.pause(); oiiaAudio = null; }
            thinkingDiv.remove();
            duckyImg.classList.remove('spinning');
            if (textContainer) {
                textContainer.classList.remove('streaming');
                // rendu final propre
                textContainer.innerHTML = marked.parse(rawText);
            }
            setStreaming(false);
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
        msgDiv.addEventListener('mouseenter', () => copyBtn.classList.add('visible'));
        msgDiv.addEventListener('mouseleave', () => copyBtn.classList.remove('visible'));
        messagesContainer.appendChild(msgDiv);
        scrollToBottom();
        return msgDiv;
    }

    function scrollToBottom() {
        messagesContainer.scrollTo({ top: messagesContainer.scrollHeight, behavior: 'smooth' });
    }
});
