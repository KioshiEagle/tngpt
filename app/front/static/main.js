document.addEventListener('DOMContentLoaded', function () {

  const form     = document.getElementById('chat-form');
  const input    = document.getElementById('inp');
  const messages = document.getElementById('messages');
  const typing   = document.getElementById('typing');
  const sendBtn  = document.getElementById('sbtn');

  form.addEventListener('submit', async function (e) {
    e.preventDefault();

    const message = input.value.trim();
    if (!message) return;

    // Vider l'input + désactiver le bouton
    input.value = '';
    sendBtn.disabled = true;

    // Supprimer l'empty state s'il est encore là
    const empty = document.querySelector('.empty');
    if (empty) empty.remove();

    // Ajouter le message utilisateur
    appendMessage('user', message);

    // Afficher l'indicateur typing
    typing.style.display = 'block';
    scrollToBottom();

    // Préparer la bulle assistant vide
    const assistantBubble = appendMessage('assistant', '');

    try {
      const response = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
      });

      if (!response.ok) throw new Error('Erreur serveur');

      // Cacher le typing dès que le stream commence
      typing.style.display = 'none';

      // Lire le stream chunk par chunk
      const reader  = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        assistantBubble.textContent += decoder.decode(value);
        scrollToBottom();
      }

    } catch (err) {
      typing.style.display = 'none';
      assistantBubble.textContent = 'Erreur : impossible de contacter le serveur.';
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  });

  function appendMessage(role, text) {
    const wrapper = document.createElement('div');
    wrapper.className = 'msg ' + role;

    const who = document.createElement('div');
    who.className = 'msg-who';
    who.textContent = role === 'user' ? 'vous' : 'TN-GPT';

    const bubble = document.createElement('div');
    bubble.className = 'msg-text';
    bubble.textContent = text;

    wrapper.appendChild(who);
    wrapper.appendChild(bubble);
    messages.appendChild(wrapper);

    scrollToBottom();
    return bubble;
  }

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

});