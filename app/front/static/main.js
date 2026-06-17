const THINKING_PHRASES = [
    "Je consulte mes neurones...",
    "Hmm, laisse-moi réfléchir...",
    "Je cherche dans ma grande sagesse...",
    "J'interroge l'oracle intérieur...",
    "Je mobilise mes deux neurones...",
    "Calcul quantique en cours...",
    "Je demande à GPT (non je déconne)...",
    "Accès à la connaissance infinie...",
    "Je lis le manuel très vite...",
    "Une seconde, je googlegooglegoogle...",
    "Analyse de la situation...",
    "Je fais semblant de comprendre...",
];

const WRITING_PHRASES = [
    "En train d'écrire...",
    "Je rédige mon chef-d'œuvre...",
    "J'essaie d'être intelligent...",
    "Frappe de clavier intensifiée...",
    "Mise en mots en cours...",
    "Presque là !",
];

function randomFrom(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('chat-form');
    const input = document.getElementById('inp');
    const messagesContainer = document.getElementById('messages');
    const sendBtn = document.getElementById('sbtn');
    const duckyImg = document.getElementById('sidebar-ducky');

    // Gestion des clics sur les "chips" (suggestions)
    document.querySelectorAll('.chip').forEach(chip => {
        chip.addEventListener('click', () => {
            input.value = chip.dataset.query;
            form.dispatchEvent(new Event('submit'));
        });
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = input.value.trim();
        if (!text) return;

        // Nettoyage interface
        const emptyState = document.getElementById('empty-state');
        if (emptyState) emptyState.remove();

        // Afficher message utilisateur
        appendMessage('user', text);
        input.value = '';
        sendBtn.disabled = true;

        // État "Réflexion" : message en italique dans le chat
        duckyImg.classList.add('spinning');
        const thinkingDiv = appendThinking(randomFrom(THINKING_PHRASES));

        let textContainer = null;

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

                if (firstChunk) {
                    thinkingDiv.remove();
                    duckyImg.classList.remove('spinning');
                    const assistantMsgDiv = appendMessage('assistant', '');
                    textContainer = assistantMsgDiv.querySelector('.msg-text');
                    firstChunk = false;
                }

                textContainer.textContent += decoder.decode(value);
                scrollToBottom();
            }
        } catch (err) {
            thinkingDiv.remove();
            appendMessage('assistant', "Désolé, j'ai eu un bug de transmission. Réessaie ?");
        } finally {
            duckyImg.classList.remove('spinning');
            sendBtn.disabled = false;
            input.focus();
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
        const msgDiv = document.createElement('div');
        msgDiv.className = `msg ${role}`;
        msgDiv.innerHTML = `
            <div class="msg-who">${role === 'user' ? 'vous' : 'TN-GPT'}</div>
            <div class="msg-text">${content}</div>
        `;
        messagesContainer.appendChild(msgDiv);
        scrollToBottom();
        return msgDiv;
    }

    function scrollToBottom() {
        messagesContainer.scrollTo({
            top: messagesContainer.scrollHeight,
            behavior: 'smooth'
        });
    }
});