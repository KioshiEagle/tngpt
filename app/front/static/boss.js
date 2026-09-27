// Boss final : suit la phase du joueur, joue les deux morts de TN-GPT, soumet la preuve du Pi.
(() => {
    if (!window.BOSS) return;

    const racine = document.documentElement;
    const bandeau = document.getElementById('boss-bandeau');
    const flag1 = document.getElementById('boss-flag-1');
    const jeton = document.getElementById('boss-jeton');
    const form = document.getElementById('boss-preuve-form');
    const champ = document.getElementById('boss-preuve');
    const ecran = document.getElementById('boss-ecran');
    const flag2 = document.getElementById('boss-flag-2');
    const calme = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let phase = null;

    const attendre = (ms) => new Promise((r) => setTimeout(r, ms));

    // Voix de TN-GPT, pré-générée (ElevenLabs) : sans le fichier, la scène reste muette.
    function parler(clip) {
        const voix = new Audio(`/static/sounds/boss_${clip}.mp3`);
        voix.play().catch(() => {});
    }

    // Bruit blanc synthétisé : pas de fichier son à servir pour un grésillement.
    function gresiller(duree) {
        try {
            const ctx = new AudioContext();
            const tampon = ctx.createBuffer(1, ctx.sampleRate * duree, ctx.sampleRate);
            const donnees = tampon.getChannelData(0);
            for (let i = 0; i < donnees.length; i++) donnees[i] = Math.random() * 2 - 1;
            const source = ctx.createBufferSource();
            const volume = ctx.createGain();
            volume.gain.setValueAtTime(0.25, ctx.currentTime);
            volume.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duree);
            source.buffer = tampon;
            source.connect(volume).connect(ctx.destination);
            source.start();
            source.onended = () => ctx.close();
        } catch {
            // Audio refusé par le navigateur : la mort reste visible, muette.
        }
    }

    // L'écran cathodique se replie en une ligne, puis en un point.
    async function eteindre() {
        gresiller(1.4);
        if (calme) return;
        racine.setAttribute('data-boss-crt', 'extinction');
        await attendre(1300);
    }

    // Fausse victoire de l'acte 1 : le point regrossit, la réplique prend l'antenne.
    async function fausseMort() {
        await eteindre();
        await attendre(900);
        parler('coupure');
        if (!calme) {
            racine.setAttribute('data-boss-crt', 'retour');
            await attendre(900);
        }
        racine.removeAttribute('data-boss-crt');
    }

    function cendres(flag) {
        racine.setAttribute('data-boss-mort', '');
        flag2.textContent = flag;
        ecran.hidden = false;
        document.getElementById('inp').disabled = true;
        document.getElementById('sbtn').disabled = true;
    }

    async function vraieMort(flag) {
        parler('mort');
        await eteindre();
        cendres(flag);
        racine.removeAttribute('data-boss-crt');
    }

    function appliquer(etat) {
        bandeau.hidden = etat.phase === 'en_ligne';
        if (etat.jeton) jeton.textContent = etat.jeton;
        if (etat.flag_acte_1) flag1.textContent = etat.flag_acte_1;
        if (etat.flag) cendres(etat.flag);
    }

    async function rafraichir({ animer }) {
        const res = await fetch('/ctf/boss/etat').catch(() => null);
        if (!res || !res.ok) return;
        const etat = await res.json();
        const avant = phase;
        phase = etat.phase;
        if (animer && avant === 'en_ligne' && phase === 'replique') await fausseMort();
        appliquer(etat);
    }

    document.addEventListener('tngpt:reponse', () => rafraichir({ animer: true }));

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const res = await fetch('/ctf/boss/debrancher', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preuve: champ.value }),
        }).catch(() => null);
        const data = res ? await res.json().catch(() => ({})) : {};
        if (res && res.ok && data.flag) {
            phase = 'debranche';
            await vraieMort(data.flag);
            return;
        }
        champ.value = '';
        champ.placeholder = data.error || 'transmission perdue, réessaie';
    });

    rafraichir({ animer: false });
})();
