document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('chat-form');
    const input = document.getElementById('inp');
    const messagesContainer = document.getElementById('messages');
    const sendBtn = document.getElementById('sbtn');
    const duckyImg = document.getElementById('sidebar-ducky');
    const sidebarBubble = document.getElementById('sidebar-bubble');

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

        // État "Réflexion"
        duckyImg.classList.add('spinning');
        sidebarBubble.textContent = "Je réfléchis...";

        // Préparer bulle assistant
        const assistantMsgDiv = appendMessage('assistant', '');
        const textContainer = assistantMsgDiv.querySelector('.msg-text');

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text })
            });

            if (!response.ok) throw new Error();

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            
            // Premier chunk reçu : on change le statut
            let firstChunk = true;

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                if (firstChunk) {
                    duckyImg.classList.remove('spinning');
                    sidebarBubble.textContent = "En train d'écrire...";
                    firstChunk = false;
                }

                textContainer.textContent += decoder.decode(value);
                scrollToBottom();
            }
        } catch (err) {
            textContainer.textContent = "Désolé, j'ai eu un bug de transmission. Réessaie ?";
        } finally {
            duckyImg.classList.remove('spinning');
            sidebarBubble.textContent = "ici ça bz.";
            sendBtn.disabled = false;
            input.focus();
        }
    });

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