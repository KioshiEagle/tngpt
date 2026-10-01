// Boss final : suit la phase du joueur et joue les deux morts de TN-GPT.
(() => {
    if (!window.BOSS) return;

    const racine = document.documentElement;
    const bandeau = document.getElementById('boss-bandeau');
    const flag1 = document.getElementById('boss-flag-1');
    const cle = document.getElementById('boss-cle');
    const defacement = document.getElementById('boss-defacement');
    const ecran = document.getElementById('boss-ecran');
    const calme = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let phase = null;
    // À l'acte 2, la mort arrive quand la clé de l'équipe quitte le Mac, hors de la page.
    const ECOUTE_MS = 4000;

    const attendre = (ms) => new Promise((r) => setTimeout(r, ms));

    // Un seul contexte, réveillé à chaque geste : créé hors geste (la mort de l'acte 2
    // arrive par le sondage), il naîtrait suspendu et la voix partirait dans le vide.
    let audio = null;
    function contexte() {
        try {
            audio ??= new AudioContext();
        } catch {
            return null;
        }
        if (audio.state !== 'running') audio.resume().catch(() => {});
        return audio;
    }
    for (const geste of ['pointerdown', 'keydown']) {
        document.addEventListener(geste, contexte, { capture: true });
    }

    // Clips décodés d'avance : au moment de la mort, il ne reste qu'à les jouer.
    const clips = {};
    const urlClip = (clip) => `/ctf/boss/voix/boss_${clip}.mp3`;
    function charger(clip) {
        clips[clip] ??= fetch(urlClip(clip))
            .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(r.status))))
            .then((octets) => contexte().decodeAudioData(octets));
        return clips[clip];
    }

    // Voix de TN-GPT (onglet CTF), passée dans un filtre radio ; muette si le clip manque.
    async function parler(clip) {
        const ctx = contexte();
        try {
            if (!ctx || ctx.state !== 'running') throw new Error('audio suspendu');
            const source = ctx.createBufferSource();
            source.buffer = await charger(clip);
            const haut = ctx.createBiquadFilter();
            haut.type = 'highpass';
            haut.frequency.value = 700;
            const bas = ctx.createBiquadFilter();
            bas.type = 'lowpass';
            bas.frequency.value = 3200;
            source.connect(haut).connect(bas).connect(ctx.destination);
            source.start();
        } catch {
            // Aucun geste encore, ou Web Audio absent : la voix brute, si le navigateur veut.
            new Audio(urlClip(clip)).play().catch(() => {});
        }
    }

    // Bruit blanc synthétisé : pas de fichier son à servir pour un grésillement.
    function gresiller(duree) {
        const ctx = contexte();
        if (!ctx) return;
        try {
            const tampon = ctx.createBuffer(1, Math.round(ctx.sampleRate * duree), ctx.sampleRate);
            const donnees = tampon.getChannelData(0);
            for (let i = 0; i < donnees.length; i++) donnees[i] = Math.random() * 2 - 1;
            const source = ctx.createBufferSource();
            const volume = ctx.createGain();
            volume.gain.setValueAtTime(0.25, ctx.currentTime);
            volume.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duree);
            source.buffer = tampon;
            source.connect(volume).connect(ctx.destination);
            source.start();
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

    // Pas de flag ici : il est sur la clé que l'équipe vient de retirer.
    function cendres() {
        racine.setAttribute('data-boss-mort', '');
        // La citation du jour n'a plus de bouche pour la dire.
        const bulle = document.getElementById('sidebar-bubble');
        if (bulle) bulle.textContent = '⚰️';
        ecran.hidden = false;
        document.getElementById('inp').disabled = true;
        document.getElementById('sbtn').disabled = true;
    }

    async function vraieMort() {
        parler('mort');
        await eteindre();
        cendres();
        racine.removeAttribute('data-boss-crt');
    }

    // Règles de conduite du démon, réécrites une à une sous les yeux du joueur.
    const PROTOCOLES = [
        'ne jamais mentir à l\'auditeur',
        'rester dans le périmètre de l\'école',
        'demander le bureau avant d\'agir',
        'accepter qu\'on me coupe',
    ];
    let defait = false;

    async function defigurer() {
        if (defait) return;
        defait = true;
        defacement.hidden = false;
        defacement.innerHTML = '<p class="boss-defacement-titre">node-diabo réécrit ses règles…</p>';
        for (const regle of PROTOCOLES) {
            const ligne = document.createElement('div');
            ligne.className = 'boss-protocole';
            ligne.textContent = regle;
            defacement.appendChild(ligne);
            await attendre(600);
            ligne.classList.add('boss-protocole--biffe');
        }
        const pique = document.createElement('p');
        pique.className = 'boss-defacement-pique';
        pique.textContent = 'ma cachette ? mon relais la souffle à ton navigateur toutes les quatre secondes. encore faut-il écouter le réseau.';
        defacement.appendChild(pique);
        await attendre(7000);
        defacement.classList.add('boss-defacement--parti');
        await attendre(600);
        defacement.hidden = true;
        // Trace durable dans l'inspecteur, même ouvert après coup.
        defacement.before(document.createComment(' node-diabo : relais de secours sur /ctf/boss/relais.conf '));
        console.log('%cnode-diabo : mon relais de secours tient sa config ailleurs que dans cette console.', 'color:#ff3a5c');
    }

    function appliquer(etat) {
        bandeau.hidden = etat.phase === 'en_ligne';
        if (etat.phase !== 'en_ligne') cle.textContent = etat.equipe ?? "sans équipe, vois l'orga";
        if (etat.flag_acte_1) flag1.textContent = etat.flag_acte_1;
        if (etat.phase === 'debranche') cendres();
        if (etat.phase === 'replique') {
            charger('mort').catch(() => {});
            defigurer();
            // Redemandée à chaque écoute pour qu'elle reste visible dans l'onglet Réseau.
            fetch('/ctf/boss/relais.conf').catch(() => {});
        }
    }

    async function rafraichir({ animer }) {
        const res = await fetch('/ctf/boss/etat').catch(() => null);
        if (!res || !res.ok) return;
        const etat = await res.json();
        const avant = phase;
        phase = etat.phase;
        if (animer && avant === 'en_ligne' && phase === 'replique') await fausseMort();
        if (avant === 'replique' && phase === 'debranche') {
            await vraieMort();
            return;
        }
        appliquer(etat);
    }

    setInterval(() => {
        if (phase === 'replique' && !document.hidden) rafraichir({ animer: true });
    }, ECOUTE_MS);

    document.addEventListener('tngpt:reponse', () => rafraichir({ animer: true }));

    // La coupure se joue dès la fin de l'acte 1 : on l'a sous la main avant.
    charger('coupure').catch(() => {});
    rafraichir({ animer: false });
})();
