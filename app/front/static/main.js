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

// --- Rendu de la carte au trésor ---
// La prose et la carte sont séparées AVANT tout rendu markdown, comme le fait
// DHDA. Elles vivent ensuite dans des conteneurs distincts : la prose peut être
// réécrite à chaque frame du streaming sans jamais toucher à la carte.
// La fence fermante est facultative, pour ne rien perdre d'une réponse coupée.
const CARTE_BLOCK = /```[ \t]*tngpt-carte[ \t]*\r?\n([\s\S]*?)(?:```|$)/i;

function splitResponse(raw) {
    const match = raw.match(CARTE_BLOCK);
    if (!match) return { prose: raw, carte: null, complete: false };
    return {
        prose: raw.replace(match[0], '').trim(),
        carte: match[1].trim(),
        // Sans fence fermante, la charge utile est encore en cours de réception.
        complete: match[0].trimEnd().endsWith('```'),
    };
}

// Repli : la liste des clubs reste lisible plutôt qu'un cadre vide.
function showMapFallback(host, payload, message) {
    const liste = document.createElement('ul');
    for (const club of (payload && payload.clubs) || []) {
        const li = document.createElement('li');
        li.textContent = `${club.nom} — ${club.tutelle}`;
        liste.appendChild(li);
    }
    const note = document.createElement('div');
    note.className = 'map-error';
    note.textContent = message;
    host.replaceChildren(liste.children.length ? liste : note, note);
}

// Dessine la carte dans son propre conteneur, créé à la demande à la suite de
// la prose. Rien n'est redessiné tant que la charge utile n'a pas changé.
function renderTreasureMap(bubble, source) {
    if (!bubble || !source) return;
    let host = bubble.querySelector(':scope > .treasure-map');
    if (!host) {
        host = document.createElement('div');
        host.className = 'treasure-map';
        bubble.appendChild(host);
    } else if (host.dataset.mapSource === source) {
        return;
    }
    host.dataset.mapSource = source;

    let payload = null;
    try {
        payload = JSON.parse(source);
        const canvas = document.createElement('div');
        canvas.className = 'treasure-map-canvas';
        canvas.appendChild(drawTreasureMap(payload));
        host.replaceChildren(canvas);
    } catch (err) {
        console.warn('carte non dessinée :', err);
        showMapFallback(host, payload, "j'ai pas réussi à dessiner la carte");
    }
}

// --- Raisonnement dépliable (chal RAG) ---
// Le back émet le raisonnement du modèle dans une fence `tngpt-reflexion`, avant
// la réponse. On l'extrait comme la carte, pour le loger dans un <details>
// replié plutôt que de le noyer dans la prose. Fence fermante facultative : le
// raisonnement streame d'abord, encore ouvert.
const REFLEXION_BLOCK = /```[ \t]*tngpt-reflexion[ \t]*\r?\n([\s\S]*?)(?:```|$)/i;

function splitReflexion(raw) {
    const match = raw.match(REFLEXION_BLOCK);
    if (!match) return { reflexion: null, rest: raw };
    return { reflexion: match[1].trim(), rest: raw.replace(match[0], '').trim() };
}

// Zone dépliable placée au-dessus de la réponse, repliée par défaut : le
// raisonnement ne s'affiche qu'au clic, comme une chaîne de pensée.
function renderReflexion(bubble, text) {
    let box = bubble.querySelector(':scope > .msg-reasoning');
    if (!box) {
        box = document.createElement('details');
        box.className = 'msg-reasoning';
        const summary = document.createElement('summary');
        summary.textContent = 'raisonnement de tn-gpt';
        const body = document.createElement('pre');
        body.className = 'msg-reasoning-body';
        box.append(summary, body);
        bubble.insertBefore(box, bubble.firstChild);
    }
    box.querySelector('.msg-reasoning-body').textContent = text;
}

// Point d'entrée unique : raisonnement dans son <details>, prose dans .msg-text,
// carte dans son voisin.
function renderAssistant(bubble, raw) {
    const textEl = bubble.querySelector('.msg-text');
    const { reflexion, rest } = splitReflexion(raw);
    if (reflexion !== null) renderReflexion(bubble, reflexion);
    const { prose, carte, complete } = splitResponse(rest);
    if (textEl) textEl.innerHTML = marked.parse(prose);
    bubble.dataset.raw = raw;
    if (carte && complete) renderTreasureMap(bubble, carte);
}

// Un rendu qui échoue ne doit jamais emporter le flux avec lui : la réponse
// s'affiche alors en texte brut, et l'erreur part en console pour être vue.
function safeRenderAssistant(bubble, raw) {
    try {
        renderAssistant(bubble, raw);
    } catch (err) {
        console.error('Rendu de la réponse impossible', err);
        const textEl = bubble.querySelector('.msg-text');
        if (textEl) textEl.textContent = raw;
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
    const brainrotToggle = document.getElementById('brainrot-toggle');
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
        // La carte peint avec des variables CSS : elle suit le thème sans
        // qu'on ait à la redessiner.
    });

    // --- Mode brainrot ---
    // Persisté comme le thème : le mode survit à la navigation entre convs.
    let brainrot = localStorage.getItem('brainrot') === 'on';

    function updateBrainrotLabel() {
        if (!brainrotToggle) return;
        brainrotToggle.textContent = brainrot ? '🧠 brainrot : ON' : '🧠 brainrot : off';
        brainrotToggle.setAttribute('aria-pressed', String(brainrot));
        brainrotToggle.classList.toggle('active', brainrot);
    }
    updateBrainrotLabel();

    brainrotToggle?.addEventListener('click', () => {
        brainrot = !brainrot;
        localStorage.setItem('brainrot', brainrot ? 'on' : 'off');
        updateBrainrotLabel();
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
            // requestSubmit() et non dispatchEvent(new Event('submit')) :
            // l'événement synthétique non annulable rechargeait la page sous Firefox.
            form.requestSubmit();
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
            // requestSubmit() émet un événement submit annulable, là où
            // dispatchEvent(new Event('submit')) en produit un non-annulable :
            // sous Firefox, le preventDefault du handler était alors ignoré et
            // la page se rechargeait.
            if (!sendBtn.disabled) form.requestSubmit();
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
        let rafId = null;

        function scheduleRender() {
            if (rafId !== null) return;
            rafId = requestAnimationFrame(() => {
                rafId = null;
                if (bubbleContainer) safeRenderAssistant(bubbleContainer, rawText);
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
            const response = await fetch(window.CHAT_ENDPOINT || '/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, conversation_id: currentConversationId, brainrot }),
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

            // Flux entièrement blanc : aucune bulle n'a été créée plus haut, et
            // sans ce cas l'écran resterait muet, sans erreur ni trace.
            if (firstChunk) {
                appendMessage('assistant', "Je n'ai rien réussi à répondre là. Reformule ?");
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

            // Le retour à l'état « prêt » passe avant le rendu final : une
            // exception de `renderAssistant` sauterait sinon `setStreaming`,
            // et le bouton resterait bloqué sur « stop » — plus aucun message
            // ne pourrait partir, sans autre trace qu'une erreur en console.
            // L'historique n'est plus tenu ici : il vit en base depuis que
            // `chat` le relit sur la conversation (voir routes.py).
            setStreaming(false);
            inp.focus();

            // rendu final propre ; à défaut, le texte brut vaut mieux qu'une
            // bulle vide.
            if (bubbleContainer) safeRenderAssistant(bubbleContainer, rawText);
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
            if (content) safeRenderAssistant(bubbleDiv, content);
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
