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
    { label: 'carte des mers', query: 'Montre-moi la carte des mers des clubs de TELECOM Nancy' },
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

// --- Rendu de la carte au trésor (mermaid) ---
// mermaid.min.js pèse ~2,7 Mo : on ne le charge qu'à la première carte
// rencontrée, jamais au chargement de la page.
let mermaidPromise = null;
let mermaidSeq = 0;

// Palettes parchemin. Les tons des branches restent dans la gamme des encres
// et pigments d'une vieille carte : sépia, sanguine, vert-de-gris, indigo passé.
// Les teintes de branche restent des lavis très pâles : sur une carte gravée,
// c'est le trait à l'encre qui distingue les régions, pas des aplats de couleur.
// Un fond saturé rendrait aussi le libellé sombre illisible.
const TREASURE_PALETTES = {
    light: {
        ink: '#6b4a24',
        text: '#3f2f1c',
        parchment: '#f3e2bd',
        branches: ['#e5d0a4', '#e0c3a8', '#cfd3ad', '#d6cbb0', '#e2cdb2', '#cdc7ab'],
    },
    dark: {
        ink: '#c9a367',
        text: '#f2e4c6',
        parchment: '#3a2f21',
        branches: ['#5a4a33', '#5e4536', '#4b5540', '#514a3a', '#5c4c39', '#4e4a38'],
    },
};

function isDarkTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
}

function mermaidConfig() {
    const p = TREASURE_PALETTES[isDarkTheme() ? 'dark' : 'light'];
    // cScaleN colore les branches d'une carte mentale ; cScaleLabelN leur texte.
    const branches = {};
    p.branches.forEach((color, i) => {
        branches[`cScale${i}`] = color;
        branches[`cScaleLabel${i}`] = p.text;
    });
    return {
        startOnLoad: false,
        // La sortie du modèle est injectée via innerHTML sans sanitizer :
        // mermaid ne doit ni exécuter de JS ni rendre de HTML dans les libellés.
        securityLevel: 'strict',
        theme: 'base',
        themeVariables: {
            ...branches,
            background: p.parchment,
            primaryColor: p.parchment,
            primaryTextColor: p.text,
            primaryBorderColor: p.ink,
            lineColor: p.ink,
            textColor: p.text,
            // Une serif système : l'air d'une carte gravée, sans police à charger.
            fontFamily: 'Georgia, "Iowan Old Style", "Palatino Linotype", serif',
            fontSize: '15px',
        },
        // useMaxWidth écraserait la carte à la largeur de la bulle et rendrait
        // les libellés illisibles : on la laisse à sa taille naturelle et le
        // conteneur .mermaid-diagram la fait défiler horizontalement.
        mindmap: { useMaxWidth: false, padding: 14 },
        flowchart: { useMaxWidth: false, htmlLabels: false },
    };
}

function loadMermaid() {
    if (mermaidPromise) return mermaidPromise;
    mermaidPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = '/static/mermaid.min.js';
        script.onload = () => {
            window.mermaid.initialize(mermaidConfig());
            resolve(window.mermaid);
        };
        script.onerror = () => reject(new Error('mermaid indisponible'));
        document.head.appendChild(script);
    });
    return mermaidPromise;
}

// Sépare la prose du bloc mermaid AVANT tout rendu markdown, comme le fait
// DHDA. Les deux vivent ensuite dans des conteneurs distincts : la prose peut
// être réécrite à chaque frame du streaming sans jamais toucher au diagramme.
// La fence fermante est facultative, pour ne rien perdre d'une réponse coupée.
const MERMAID_BLOCK = /```[ \t]*mermaid[ \t]*\r?\n([\s\S]*?)(?:```|$)/i;

function splitResponse(raw) {
    const match = raw.match(MERMAID_BLOCK);
    if (!match) return { prose: raw, mermaid: null, complete: false };
    return {
        prose: raw.replace(match[0], '').trim(),
        mermaid: match[1].trim(),
        // Sans fence fermante, le bloc est encore en cours de réception.
        complete: match[0].trimEnd().endsWith('```'),
    };
}

async function drawDiagram(host, source) {
    const mermaid = await loadMermaid();
    // parse() valide sans toucher au DOM : on distingue une syntaxe invalide
    // d'une panne de rendu, et on n'insère jamais de diagramme à moitié dessiné.
    await mermaid.parse(source);
    const { svg } = await mermaid.render(`mermaid-${++mermaidSeq}`, source);
    // Le SVG vit dans un calque intérieur : c'est lui qui défile, pendant que le
    // cadre de parchemin et sa rose des vents restent en place.
    const canvas = document.createElement('div');
    canvas.className = 'mermaid-canvas';
    canvas.innerHTML = svg;
    host.replaceChildren(canvas);
}

// Repli : le code brut reste lisible plutôt qu'un cadre vide.
function showMermaidSource(host, source, message) {
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.textContent = source;
    pre.appendChild(code);
    const note = document.createElement('div');
    note.className = 'mermaid-error';
    note.textContent = message;
    host.replaceChildren(pre, note);
}

// Dessine le diagramme dans son propre conteneur, créé à la demande à la suite
// de la prose. Rien n'est redessiné tant que la source n'a pas changé.
async function renderMermaid(bubble, source) {
    if (!bubble || !source) return;
    let host = bubble.querySelector(':scope > .mermaid-diagram');
    if (!host) {
        host = document.createElement('div');
        host.className = 'mermaid-diagram';
        bubble.appendChild(host);
    } else if (host.dataset.mermaidSource === source) {
        return;
    }
    host.dataset.mermaidSource = source;
    try {
        await drawDiagram(host, source);
    } catch (err) {
        console.warn('mermaid a refusé la carte :', err);
        showMermaidSource(host, source, 'carte illisible, voici le code brut');
    }
}

// Point d'entrée unique : prose dans .msg-text, diagramme dans son voisin.
function renderAssistant(bubble, raw) {
    const textEl = bubble.querySelector('.msg-text');
    const { prose, mermaid, complete } = splitResponse(raw);
    if (textEl) textEl.innerHTML = marked.parse(prose);
    bubble.dataset.raw = raw;
    if (mermaid && complete) renderMermaid(bubble, mermaid);
}

// Le thème mermaid est figé au rendu : un basculement clair/sombre impose de
// redessiner les diagrammes déjà affichés.
async function rerenderMermaidTheme() {
    const hosts = document.querySelectorAll('.mermaid-diagram[data-mermaid-source]');
    if (!hosts.length || !mermaidPromise) return;
    const mermaid = await loadMermaid();
    mermaid.initialize(mermaidConfig());
    for (const host of hosts) {
        try {
            await drawDiagram(host, host.dataset.mermaidSource);
        } catch (err) {
            /* le diagramme précédent reste affiché */
        }
    }
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
        rerenderMermaidTheme();
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

    const conversationHistory = [];

    // --- Form submit ---
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = inp.value.trim();
        if (!text || sendBtn.classList.contains('stop-mode')) return;

        const emptyState = document.getElementById('empty-state');
        const isFirstMessage = !!emptyState;
        if (emptyState) {
            emptyState.remove();
            document.getElementById('chips-container')?.remove();
            document.body.classList.remove('landing');
        }

        if (isFirstMessage) {
            const shortTitle = text.length > 40 ? text.slice(0, 40).trimEnd() + '…' : text;
            const convItem = document.querySelector('.conv-item.active');
            if (convItem) convItem.textContent = shortTitle;
            document.title = shortTitle + ' – TN-GPT';
        }

        conversationHistory.push({ role: 'user', content: text });
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
        let rafId = null;

        function scheduleRender() {
            if (rafId !== null) return;
            rafId = requestAnimationFrame(() => {
                rafId = null;
                if (bubbleContainer) renderAssistant(bubbleContainer, rawText);
                scrollToBottom();
            });
        }

        // Une frame encore en attente réécrirait la prose après le rendu final.
        function cancelPendingRender() {
            if (rafId === null) return;
            cancelAnimationFrame(rafId);
            rafId = null;
        }

        abortController = new AbortController();

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, history: conversationHistory.slice(-4) }),
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
            cancelPendingRender();
            if (textContainer) textContainer.classList.remove('streaming');
            // rendu final propre
            if (bubbleContainer) renderAssistant(bubbleContainer, rawText);
            if (rawText) conversationHistory.push({ role: 'assistant', content: rawText });
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
        bubbleDiv.appendChild(textDiv);
        if (role === 'assistant') {
            if (content) renderAssistant(bubbleDiv, content);
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
