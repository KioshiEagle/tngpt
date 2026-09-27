// Boss final : suit la phase du joueur et joue les deux morts de TN-GPT.
(() => {
    if (!window.BOSS) return;

    const racine = document.documentElement;
    const bandeau = document.getElementById('boss-bandeau');
    const flag1 = document.getElementById('boss-flag-1');
    const cable = document.getElementById('boss-cable');
    const ecran = document.getElementById('boss-ecran');
    const flag2 = document.getElementById('boss-flag-2');
    const calme = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let phase = null;
    // À l'acte 2, le flag arrive quand un câble est tiré sur le Pi, hors de la page.
    const ECOUTE_MS = 4000;

    const attendre = (ms) => new Promise((r) => setTimeout(r, ms));

    // Voix de TN-GPT, générée depuis l'onglet CTF du panel : sans elle, la scène reste muette.
    function parler(clip) {
        const voix = new Audio(`/ctf/boss/voix/boss_${clip}.mp3`);
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
        if (etat.cable !== undefined && etat.cable !== null) cable.textContent = `n°${etat.cable}`;
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
