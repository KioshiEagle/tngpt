// Encart promotionnel glissé dans le fil de conversation, toutes les N bulles.
//
// Vit dans son propre fichier, et non dans main.js : une campagne a une date de
// fin, et tout se retire en supprimant ce script et sa balise du gabarit.
// Rien n'est envoyé au modèle ni enregistré en base : c'est de l'affichage.

(function () {
    'use strict';

    const CAMPAGNE = {
        // Fuseau explicite : sinon l'échéance glisse d'une heure selon le
        // réglage du navigateur, et la promo survit au tirage.
        fin: new Date('2026-09-09T19:00:00+02:00'),
        toutesLesBulles: 10,
        lien: 'https://www.helloasso.com/associations/cercle-des-eleves-de-telecom-nancy/evenements/tombola',
        titre: "L'Intombola est ouverte 🎟️",
        points: [
            '🎁 Plus de 50 lots à gagner — télé, chaise gaming, LEGO, vélos, places au Gala…',
            '✅ Un ticket est déjà compris dans ton Pack Inté',
            '🍀 Tirage mercredi 9 septembre',
        ],
        appel: 'Prendre des tickets sur HelloAsso',
        pied: 'Que la chance et Toutatis soient avec toi ⚔️',
    };

    function campagneOuverte() {
        return Date.now() < CAMPAGNE.fin.getTime();
    }

    function construireEncart() {
        const encart = document.createElement('aside');
        encart.className = 'promo';
        encart.setAttribute('aria-label', 'Annonce du BDE');

        const titre = document.createElement('div');
        titre.className = 'promo-titre';
        titre.textContent = CAMPAGNE.titre;

        const points = document.createElement('ul');
        points.className = 'promo-points';
        CAMPAGNE.points.forEach((texte) => {
            const point = document.createElement('li');
            point.textContent = texte;
            points.appendChild(point);
        });

        const appel = document.createElement('a');
        appel.className = 'promo-appel';
        appel.href = CAMPAGNE.lien;
        appel.target = '_blank';
        appel.rel = 'noopener';
        appel.textContent = CAMPAGNE.appel + ' →';

        const pied = document.createElement('div');
        pied.className = 'promo-pied';
        pied.textContent = CAMPAGNE.pied;

        encart.append(titre, points, appel, pied);
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
