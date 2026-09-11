// Encart promotionnel glissé dans le fil de conversation, toutes les N bulles.
//
// Vit dans son propre fichier, et non dans main.js : une campagne a une date de
// fin, et tout se retire en supprimant ce script et sa balise du gabarit.
// Rien n'est envoyé au modèle ni enregistré en base : c'est de l'affichage.

(function () {
    'use strict';

    const CAMPAGNE = {
        // Fuseau explicite : sinon l'échéance glisse d'une heure selon le
        // réglage du navigateur. Fin de soirée : les retardataires comptent.
        fin: new Date('2026-09-15T20:00:00+02:00'),
        toutesLesBulles: 10,
        auteur: 'Tek’TN',
        // Variante de style.css (.promo--vert) ; absente, l'encart prend le thème.
        teinte: 'vert',
        titre: "🐧 Install Party X Reunion Tek'TN",
        points: [
            '📅 Mardi 15 septembre, à partir de 18 h',
            '🤖 18 h — Présentation du club et lancement du pôle robotique pour la Coupe de France de Robotique 2027 : c’est le moment de rejoindre l’équipe',
            '💻 18 h 30 — Install Party : viens avec ton ordi passer à Linux, en dual boot ou en remplacement. Hackin’TN y présente ses outils de cybersécurité',
            '🍕 19 h 30 — Pizzas à 5 €, 2,50 € la demie, sur précommande obligatoire jusqu’au lundi 14 à 18 h',
        ],
        appel: {
            texte: 'Précommander ma pizza sur HelloAsso',
            lien: 'https://www.helloasso.com/associations/cercle-des-eleves-de-telecom-nancy/boutiques/install-party',
            // La billetterie ferme la veille : passé ce délai, le bouton
            // mènerait à une page close.
            fin: new Date('2026-09-14T18:00:00+02:00'),
        },
        pied: 'Bricolment vôtre, l’équipe Tek’TN',
    };

    function campagneOuverte() {
        return Date.now() < CAMPAGNE.fin.getTime();
    }

    // Sans lien, ou billetterie close : l'annonce reste, le bouton non.
    function appelOuvert() {
        return Boolean(CAMPAGNE.appel.lien) && Date.now() < CAMPAGNE.appel.fin.getTime();
    }

    // Espaces insécables à la française : « 18 h 30 », « 5 € » et le « : »
    // ne se retrouvent jamais seuls en début de ligne.
    function insecables(texte) {
        return texte
            .replace(/(\d) (?=h\b|€)/g, '$1\u00a0')
            .replace(/\bh (?=\d)/g, 'h\u00a0')
            .replace(/ ([:;!?])/g, '\u00a0$1');
    }

    function construireAppel() {
        const appel = document.createElement('a');
        appel.className = 'promo-appel';
        appel.href = CAMPAGNE.appel.lien;
        appel.target = '_blank';
        appel.rel = 'noopener';
        appel.textContent = insecables(CAMPAGNE.appel.texte) + '\u00a0→';
        return appel;
    }

    function construireEncart() {
        const encart = document.createElement('aside');
        encart.className = CAMPAGNE.teinte ? 'promo promo--' + CAMPAGNE.teinte : 'promo';
        encart.setAttribute('aria-label', 'Annonce de ' + CAMPAGNE.auteur);

        const titre = document.createElement('div');
        titre.className = 'promo-titre';
        titre.textContent = insecables(CAMPAGNE.titre);

        const points = document.createElement('ul');
        points.className = 'promo-points';
        CAMPAGNE.points.forEach((texte) => {
            const point = document.createElement('li');
            point.textContent = insecables(texte);
            points.appendChild(point);
        });

        const pied = document.createElement('div');
        pied.className = 'promo-pied';
        pied.textContent = insecables(CAMPAGNE.pied);

        encart.append(titre, points);
        if (appelOuvert()) encart.appendChild(construireAppel());
        encart.appendChild(pied);
        return encart;
    }

    // Appelée après chaque bulle ajoutée au fil. Les bulles d'attente et les
    // encarts déjà posés ne comptent pas : seules les vraies bulles rythment.
    function apresBulle(conteneur) {
        if (!campagneOuverte()) return;
        const bulles = conteneur.querySelectorAll('.msg:not(.msg--attente)');
        if (bulles.length === 0 || bulles.length % CAMPAGNE.toutesLesBulles !== 0) return;
        conteneur.appendChild(construireEncart());
    }

    window.TNGPT_PROMO = { apresBulle };
})();
