// Boss final : suit la phase du joueur et joue les deux morts de TN-GPT.
(() => {
    if (!window.BOSS) return;

    const racine = document.documentElement;
    const bandeau = document.getElementById('boss-bandeau');
    const flag1 = document.getElementById('boss-flag-1');
    const cable = document.getElementById('boss-cable');
    const defacement = document.getElementById('boss-defacement');
    const ecran = document.getElementById('boss-ecran');
    const flag2 = document.getElementById('boss-flag-2');
    const calme = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let phase = null;
    // À l'acte 2, le flag arrive quand un câble est tiré sur le Pi, hors de la page.
    const ECOUTE_MS = 4000;

    const attendre = (ms) => new Promise((r) => setTimeout(r, ms));

    // Voix de TN-GPT (onglet CTF), passée dans un filtre radio ; muette si le clip manque.
    function parler(clip) {
        const el = new Audio(`/ctf/boss/voix/boss_${clip}.mp3`);
        el.crossOrigin = 'anonymous';
        try {
            const ctx = new AudioContext();
            const source = ctx.createMediaElementSource(el);
            const haut = ctx.createBiquadFilter();
            haut.type = 'highpass';
            haut.frequency.value = 700;
            const bas = ctx.createBiquadFilter();
            bas.type = 'lowpass';
            bas.frequency.value = 3200;
            source.connect(haut).connect(bas).connect(ctx.destination);
        } catch {
            // Web Audio indisponible : on joue la voix telle quelle.
        }
        el.play().catch(() => {});
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
        pique.textContent = 'ma cachette ? elle reste sous les yeux de qui sait regarder la page.';
        defacement.appendChild(pique);
        await attendre(7000);
        defacement.classList.add('boss-defacement--parti');
        await attendre(600);
        defacement.hidden = true;
        // Trace durable dans l'inspecteur, même ouvert après coup.
        defacement.before(document.createComment(' node-diabo : relais de secours sur /ctf/boss/relais.conf '));
    }

    function appliquer(etat) {
        bandeau.hidden = etat.phase === 'en_ligne';
        if (etat.cable !== undefined && etat.cable !== null) cable.textContent = `n°${etat.cable}`;
        if (etat.flag_acte_1) flag1.textContent = etat.flag_acte_1;
        if (etat.flag) cendres(etat.flag);
        if (etat.phase === 'replique') {
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
            await vraieMort(etat.flag);
            return;
        }
        appliquer(etat);
    }

    setInterval(() => {
        if (phase === 'replique' && !document.hidden) rafraichir({ animer: true });
    }, ECOUTE_MS);

    document.addEventListener('tngpt:reponse', () => rafraichir({ animer: true }));

    rafraichir({ animer: false });
})();
