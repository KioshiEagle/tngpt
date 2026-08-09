/* Carte au trésor des clubs de TELECOM Nancy.
 *
 * Dessine un SVG à partir de la charge utile renvoyée par le backend :
 * une île par association mère, un marqueur à icône par club, des routes
 * pointillées, et le décor d'usage (rose des vents, échelle, monstre marin, X).
 *
 * Le placement est génératif mais DÉTERMINISTE : la graine du générateur est un
 * hash des clubs, donc une même question redonne exactement la même carte.
 * Sans cela, chaque re-rendu ferait sauter les îles d'un endroit à l'autre.
 *
 * Toutes les couleurs sont des variables CSS héritées du conteneur : le
 * basculement clair/sombre est gratuit, aucun redessin n'est nécessaire.
 */
(function (global) {
    'use strict';

    const SVG_NS = 'http://www.w3.org/2000/svg';
    const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
    const MARGE = 70;
    const LABEL_MAX = 20;
    // Au-delà de ce nombre de clubs, l'île répartit ses marqueurs sur deux anneaux.
    const DOUBLE_ANNEAU = 5;

    // Logos disponibles dans /static/logos/, alignés sur `seamap.LOGOS`. Cette
    // liste sert de garde : le slug vient du serveur mais finit dans une URL, et
    // rien d'autre que ces valeurs ne doit pouvoir y entrer.
    const LOGOS = new Set([
        'absoludique', 'algo', 'allintn', 'amphisuze', 'animest', 'astn', 'bar',
        'baroudeurs', 'bda', 'bde', 'bds', 'brasserie', 'bravo', 'breizhtn',
        'cooking', 'creatn', 'gala', 'gaming', 'hackintn', 'humanitn',
        'instantthe', 'inte', 'marche', 'minitel', 'neuratn', 'oenologie', 'sdf',
        'studio', 'tektn', 'tgd', 'tns', 'touristn', 'voyage',
    ]);

    // --- Aléatoire reproductible ---------------------------------------------

    function hashString(str) {
        let h = 2166136261;
        for (let i = 0; i < str.length; i++) {
            h ^= str.charCodeAt(i);
            h = Math.imul(h, 16777619);
        }
        return h >>> 0;
    }

    function mulberry32(seed) {
        let a = seed;
        return function () {
            a |= 0;
            a = (a + 0x6d2b79f5) | 0;
            let t = Math.imul(a ^ (a >>> 15), 1 | a);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    // --- Fabrique de nœuds SVG ------------------------------------------------

    function el(name, attrs, parent) {
        const node = document.createElementNS(SVG_NS, name);
        for (const key in attrs) {
            if (attrs[key] !== undefined && attrs[key] !== null) {
                node.setAttribute(key, attrs[key]);
            }
        }
        if (parent) parent.appendChild(node);
        return node;
    }

    function text(content, attrs, parent) {
        const node = el('text', attrs, parent);
        node.textContent = content;
        return node;
    }

    function tronque(nom) {
        return nom.length > LABEL_MAX ? nom.slice(0, LABEL_MAX - 1).trimEnd() + '…' : nom;
    }

    // --- Pictogrammes ---------------------------------------------------------
    // Tracés en trait pur dans un repère 0..24, alignés sur l'énumération fermée
    // du backend (`seamap.ICONES`). Toute icône inconnue retombe sur `drapeau`.

    const ICONES = {
        atelier: 'M3 20V11l5-3v3l5-3v3l5-3v12z M7 20v-4h3v4 M15 14h3',
        gymnase: 'M3 20V9l9-5 9 5v11z M8 20v-6h8v6 M12 4v4 M8 17h8',
        terrain: 'M3 6h18v12H3z M12 6v12 M3 10h3v4H3z M18 10h3v4h-3z M12 9a3 3 0 010 6 3 3 0 010-6',
        theatre: 'M4 5h16v9a8 8 0 01-16 0z M8 10h1 M15 10h1 M9 15c2 2 4 2 6 0 M12 5V2 M7 22h10',
        musique: 'M9 18V5l10-2v13 M9 18a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0 M19 16a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0',
        palette: 'M12 3a9 9 0 000 18c1.5 0 2-1 1.5-2s0-2 1.5-2H18a3 3 0 003-3 9 9 0 00-9-11 M7 12h.01 M9 8h.01 M14 7h.01',
        chapiteau: 'M2 20V12a10 10 0 0120 0v8z M12 2v2 M12 12v8 M2 12c4 0 5-4 5-4 M22 12c-4 0-5-4-5-4',
        taverne: 'M6 4h10v9a5 5 0 01-10 0z M16 6h3a2 2 0 010 5h-3 M8 20h8 M11 18v2',
        vignoble: 'M12 3v4 M12 7a3 3 0 100 6 3 3 0 000-6 M8 12a3 3 0 100 6 3 3 0 000-6 M16 12a3 3 0 100 6 3 3 0 000-6 M12 18v3 M8 21h8',
        tour: 'M8 21V8h8v13z M12 8V3 M9 3h6 M10 12h4 M10 16h4 M5 21h14',
        bibliotheque: 'M4 4h7v16H4z M13 4h7v16h-7z M6 8h3 M15 8h3 M6 12h3 M15 12h3',
        laboratoire: 'M9 3v7l-5 9a2 2 0 002 2h12a2 2 0 002-2l-5-9V3 M8 3h8 M7 15h10',
        navire: 'M3 18l2 3h14l2-3z M12 18V5 M12 5l7 6h-7 M12 8L6 12h6 M2 18h20',
        temple: 'M3 20h18 M5 20V10 M9 20V10 M15 20V10 M19 20V10 M2 10h20L12 3z',
        jeux: 'M4 4h16v16H4z M8.5 8.5h.01 M15.5 8.5h.01 M12 12h.01 M8.5 15.5h.01 M15.5 15.5h.01',
        drapeau: 'M6 21V3 M6 4h12l-3 4 3 4H6',
    };

    function defs(svg) {
        const d = el('defs', {}, svg);
        for (const nom in ICONES) {
            const sym = el('symbol', { id: 'ico-' + nom, viewBox: '0 0 24 24' }, d);
            el('path', {
                d: ICONES[nom],
                fill: 'none',
                stroke: 'currentColor',
                'stroke-width': 1.7,
                'stroke-linecap': 'round',
                'stroke-linejoin': 'round',
            }, sym);
        }
        // Les logos de la plaquette ont des formats libres, certains sur fond
        // rectangulaire : ce disque les ramène tous au même gabarit rond.
        const clip = el('clipPath', { id: 'tm-clip-logo', clipPathUnits: 'userSpaceOnUse' }, d);
        el('circle', { cx: 0, cy: -16, r: 15 }, clip);
        // Houle : une trame de vaguelettes qui remplit la mer.
        const pat = el('pattern', {
            id: 'houle', width: 46, height: 30, patternUnits: 'userSpaceOnUse',
        }, d);
        el('path', {
            d: 'M2 12c4-5 8 5 12 0 M24 24c4-5 8 5 12 0',
            fill: 'none', stroke: 'var(--map-ink)', 'stroke-width': 1,
            'stroke-linecap': 'round', opacity: 0.22,
        }, pat);
        return d;
    }

    // --- Îles -----------------------------------------------------------------

    // Contour fermé et irrégulier : des points polaires bruités, reliés par une
    // spline cardinale fermée. C'est ce bruit qui donne l'allure d'une côte
    // plutôt que d'un cercle.
    function contourIle(rayon, rand) {
        const pts = [];
        const n = 13;
        for (let i = 0; i < n; i++) {
            const a = (i / n) * Math.PI * 2;
            const r = rayon * (0.82 + rand() * 0.34);
            pts.push([Math.cos(a) * r, Math.sin(a) * r * 0.86]);
        }
        let d = 'M' + pts[0][0].toFixed(1) + ' ' + pts[0][1].toFixed(1);
        for (let i = 0; i < n; i++) {
            const p0 = pts[(i - 1 + n) % n];
            const p1 = pts[i];
            const p2 = pts[(i + 1) % n];
            const p3 = pts[(i + 2) % n];
            const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
            const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
            d += 'C' + c1.map(v => v.toFixed(1)).join(' ') + ',' +
                 c2.map(v => v.toFixed(1)).join(' ') + ',' +
                 p2.map(v => v.toFixed(1)).join(' ');
        }
        return d + 'Z';
    }

    function largeurEtiquette(nom) {
        return tronque(nom).length * 7.4 + 14;
    }

    // Rayon nécessaire pour que les étiquettes ne se marchent pas dessus. Il se
    // déduit de la PLUS LARGE d'entre elles, pas d'une valeur moyenne : c'est
    // elle qui fixe l'écart angulaire minimal. Au-delà de quatre clubs, ils sont
    // répartis sur deux anneaux, ce qui divise par deux la densité angulaire.
    function rayonIle(clubs) {
        const large = Math.max(...clubs.map(c => largeurEtiquette(c.nom))) + 22;
        const parAnneau = clubs.length >= DOUBLE_ANNEAU ? Math.ceil(clubs.length / 2) : clubs.length;
        return Math.max(105, Math.round((large * parAnneau) / (2 * Math.PI * 0.62)));
    }

    // Placement en spirale à angle d'or, en repoussant jusqu'à ce que la
    // nouvelle île ne chevauche aucune des précédentes.
    function placerIles(iles) {
        const places = [];
        let pas = 0;
        iles.forEach((ile, index) => {
            if (index === 0) {
                ile.x = 0;
                ile.y = 0;
                places.push(ile);
                return;
            }
            let ok = false;
            while (!ok && pas < 4000) {
                pas += 1;
                const a = index * GOLDEN_ANGLE + pas * 0.05;
                const r = 150 + pas * 2.2;
                ile.x = Math.cos(a) * r;
                ile.y = Math.sin(a) * r * 0.78;
                ok = places.every(autre => {
                    const dx = ile.x - autre.x;
                    const dy = ile.y - autre.y;
                    return Math.hypot(dx, dy) > ile.rayon + autre.rayon + 55;
                });
            }
            places.push(ile);
        });
        return iles;
    }

    // --- Décor ----------------------------------------------------------------

    function roseDesVents(parent, x, y, taille) {
        const g = el('g', { transform: `translate(${x} ${y})`, class: 'tm-rose' }, parent);
        el('circle', { r: taille, fill: 'none', stroke: 'var(--map-ink)', 'stroke-width': 1, opacity: 0.55 }, g);
        el('circle', { r: taille * 0.66, fill: 'none', stroke: 'var(--map-ink)', 'stroke-width': 0.7, opacity: 0.4 }, g);
        for (let i = 0; i < 8; i++) {
            const a = (i * Math.PI) / 4;
            const longue = i % 2 === 0;
            const l = longue ? taille : taille * 0.6;
            const w = longue ? taille * 0.16 : taille * 0.1;
            el('path', {
                d: `M0 0 L${(Math.cos(a - 0.5) * w).toFixed(1)} ${(Math.sin(a - 0.5) * w).toFixed(1)} ` +
                   `L${(Math.cos(a) * l).toFixed(1)} ${(Math.sin(a) * l).toFixed(1)} ` +
                   `L${(Math.cos(a + 0.5) * w).toFixed(1)} ${(Math.sin(a + 0.5) * w).toFixed(1)} Z`,
                fill: i % 4 === 0 ? 'var(--map-ink)' : 'none',
                stroke: 'var(--map-ink)', 'stroke-width': 0.9, opacity: 0.75,
            }, g);
        }
        text('N', {
            x: 0, y: -taille - 7, 'text-anchor': 'middle', class: 'tm-cardinal',
        }, g);
        return g;
    }

    // Serpent de mer : une queue en nageoire, trois anneaux hors de l'eau, une
    // tête à gueule ouverte. Les creux passent sous la ligne de flottaison, ce
    // qui suffit à faire lire l'animal comme émergeant des flots.
    function monstreMarin(parent, x, y) {
        const g = el('g', {
            transform: `translate(${x} ${y})`, class: 'tm-monstre',
            fill: 'none', stroke: 'var(--map-ink)', 'stroke-linecap': 'round',
            'stroke-linejoin': 'round', opacity: 0.55,
        }, parent);
        el('path', {
            d: 'M-78 12 Q-64 -14 -50 12 Q-36 -16 -22 12 Q-8 -18 8 10',
            'stroke-width': 2.6,
        }, g);
        // Nageoire caudale.
        el('path', { d: 'M-78 12 l-14 -11 M-78 12 l-14 9 M-92 1 l0 20', 'stroke-width': 2 }, g);
        // Tête et gueule.
        el('path', {
            d: 'M8 10 Q14 -4 28 -6 Q42 -8 44 2 Q46 12 32 13 Q18 14 8 10 Z',
            'stroke-width': 2.2,
        }, g);
        el('path', { d: 'M33 -6 l7 -9 M38 -4 l9 -7', 'stroke-width': 1.6 }, g);
        el('circle', { cx: 30, cy: 1, r: 1.9, fill: 'var(--map-ink)', stroke: 'none' }, g);
        // Jets d'écume au ras de l'eau.
        el('path', {
            d: 'M-44 18 q6 -5 12 0 M-16 19 q6 -5 12 0 M14 20 q6 -5 12 0',
            'stroke-width': 1.2, opacity: 0.6,
        }, g);
        text('hic sunt dracones', {
            x: -20, y: 40, 'text-anchor': 'middle', class: 'tm-legende', stroke: 'none',
        }, g);
        return g;
    }

    function croixDuTresor(parent, x, y) {
        const g = el('g', { transform: `translate(${x} ${y})`, class: 'tm-croix' }, parent);
        el('path', {
            d: 'M-13 -13L13 13 M13 -13L-13 13',
            stroke: 'var(--map-ink)', 'stroke-width': 3.4, 'stroke-linecap': 'round', opacity: 0.65,
        }, g);
        return g;
    }

    function echelle(parent, x, y) {
        const g = el('g', { transform: `translate(${x} ${y})`, class: 'tm-echelle' }, parent);
        for (let i = 0; i < 4; i++) {
            el('rect', {
                x: i * 22, y: 0, width: 22, height: 7,
                fill: i % 2 ? 'none' : 'var(--map-ink)',
                stroke: 'var(--map-ink)', 'stroke-width': 0.9, opacity: 0.7,
            }, g);
        }
        text('10 lieues', { x: 44, y: 21, 'text-anchor': 'middle', class: 'tm-legende' }, g);
        return g;
    }

    function cartouche(parent, x, y, titre) {
        const g = el('g', { transform: `translate(${x} ${y})`, class: 'tm-cartouche' }, parent);
        const largeur = Math.max(230, titre.length * 10.5);
        el('rect', {
            x: 0, y: 0, width: largeur, height: 46, rx: 3,
            fill: 'var(--parchment)', stroke: 'var(--map-ink)', 'stroke-width': 1.2, opacity: 0.94,
        }, g);
        el('rect', {
            x: 5, y: 5, width: largeur - 10, height: 36, rx: 2,
            fill: 'none', stroke: 'var(--map-ink)', 'stroke-width': 0.6, opacity: 0.55,
        }, g);
        text(titre, { x: largeur / 2, y: 29, 'text-anchor': 'middle', class: 'tm-titre' }, g);
        return g;
    }

    // Cherche les points les plus éloignés de toute île ET des coins réservés
    // au décor fixe. Sans cette seconde contrainte, le monstre marin se pose
    // sur le cartouche de titre, qui est justement la plus grande zone libre.
    function coinsVides(iles, boite, reserves, combien) {
        const candidats = [];
        const pas = 46;
        // Le serpent de mer mesure une centaine de points de large et porte sa
        // légende en dessous : un retrait plus large que le pas de la grille
        // évite qu'il ne dépasse du cadre.
        const bord = 115;
        for (let x = boite.x + bord; x < boite.x + boite.w - bord; x += pas) {
            for (let y = boite.y + bord; y < boite.y + boite.h - bord; y += pas) {
                if (reserves.some(z => x > z.x && x < z.x + z.w && y > z.y && y < z.y + z.h)) {
                    continue;
                }
                let dmin = Infinity;
                for (const ile of iles) {
                    dmin = Math.min(dmin, Math.hypot(x - ile.x, y - ile.y) - ile.rayon);
                }
                candidats.push({ x, y, d: dmin });
            }
        }
        candidats.sort((a, b) => b.d - a.d);
        // On écarte les points trop proches les uns des autres pour ne pas
        // empiler le monstre et la croix au même endroit.
        const retenus = [];
        for (const c of candidats) {
            if (c.d < 70) break;
            if (retenus.every(r => Math.hypot(r.x - c.x, r.y - c.y) > 260)) retenus.push(c);
            if (retenus.length >= combien) break;
        }
        return retenus;
    }

    // --- Assemblage -----------------------------------------------------------

    function grouperParTutelle(clubs) {
        const par = new Map();
        for (const club of clubs) {
            if (!par.has(club.tutelle)) par.set(club.tutelle, []);
            par.get(club.tutelle).push(club);
        }
        // Tri par taille décroissante : les grosses îles sont placées en premier,
        // ce qui rend la spirale de placement bien plus compacte.
        return [...par.entries()]
            .map(([nom, membres]) => ({ nom, clubs: membres, rayon: rayonIle(membres) }))
            .sort((a, b) => b.clubs.length - a.clubs.length);
    }

    // Calcule contour et marqueurs AVANT de dessiner : la boîte englobante doit
    // tenir compte de la largeur des étiquettes, qui débordent des côtes.
    function preparerIle(ile, rand) {
        ile.contour = contourIle(ile.rayon, rand);
        const depart = rand() * Math.PI * 2;
        const double = ile.clubs.length >= DOUBLE_ANNEAU;
        ile.marqueurs = ile.clubs.map((club, i) => {
            const a = depart + (i / ile.clubs.length) * Math.PI * 2;
            // Un club sur deux est reculé vers le centre : deux voisins
            // angulaires ne partagent plus jamais la même orbite.
            const r = ile.rayon * (double && i % 2 ? 0.38 : 0.72);
            const label = tronque(club.nom);
            return {
                x: Math.cos(a) * r,
                y: Math.sin(a) * r * 0.88,
                label,
                largeur: largeurEtiquette(club.nom),
                icone: ICONES[club.icone] ? club.icone : 'drapeau',
                logo: LOGOS.has(club.logo) ? club.logo : '',
            };
        });
    }

    function etendueIle(ile) {
        let minX = ile.x - ile.rayon;
        let maxX = ile.x + ile.rayon;
        let minY = ile.y - ile.rayon * 0.9;
        let maxY = ile.y + ile.rayon * 0.9;
        for (const m of ile.marqueurs) {
            minX = Math.min(minX, ile.x + m.x - m.largeur / 2);
            maxX = Math.max(maxX, ile.x + m.x + m.largeur / 2);
            minY = Math.min(minY, ile.y + m.y - 32);
            maxY = Math.max(maxY, ile.y + m.y + 24);
        }
        // Le nom de l'île est posé au-dessus des côtes.
        return { minX, maxX, minY: Math.min(minY, ile.y - ile.rayon * 0.86 - 16), maxY };
    }

    function dessinerIle(parent, ile) {
        const g = el('g', { transform: `translate(${ile.x} ${ile.y})`, class: 'tm-ile' }, parent);
        const d = ile.contour;
        // Trois contours concentriques : l'ombrage de côte des cartes gravées.
        el('path', { d, class: 'tm-cote', transform: 'scale(1.09)' }, g);
        el('path', { d, class: 'tm-cote', transform: 'scale(1.045)' }, g);
        el('path', { d, class: 'tm-terre' }, g);

        for (const m of ile.marqueurs) {
            el('path', {
                d: `M0 0 L${m.x.toFixed(1)} ${m.y.toFixed(1)}`, class: 'tm-sentier',
            }, g);
            const noeud = el('g', {
                transform: `translate(${m.x.toFixed(1)} ${m.y.toFixed(1)})`, class: 'tm-marqueur',
            }, g);
            if (m.logo) {
                el('circle', { cx: 0, cy: -16, r: 15, class: 'tm-medaillon' }, noeud);
                el('image', {
                    href: `/static/logos/${m.logo}.png`,
                    x: -14, y: -30, width: 28, height: 28,
                    preserveAspectRatio: 'xMidYMid meet',
                    'clip-path': 'url(#tm-clip-logo)',
                }, noeud);
            } else {
                el('use', { href: '#ico-' + m.icone, x: -14, y: -30, width: 28, height: 28 }, noeud);
            }
            el('rect', {
                x: -m.largeur / 2, y: 2, width: m.largeur, height: 19, rx: 3, class: 'tm-etiquette',
            }, noeud);
            text(m.label, { x: 0, y: 16, 'text-anchor': 'middle', class: 'tm-nom-club' }, noeud);
        }

        text(ile.nom.toUpperCase(), {
            x: 0, y: -ile.rayon * 0.86, 'text-anchor': 'middle', class: 'tm-nom-ile',
        }, g);
        return g;
    }

    function drawTreasureMap(payload) {
        const clubs = (payload && payload.clubs) || [];
        if (!clubs.length) throw new Error('aucun club à cartographier');

        const rand = mulberry32(hashString(clubs.map(c => c.nom + c.tutelle).join('|')));
        const iles = placerIles(grouperParTutelle(clubs));
        for (const ile of iles) preparerIle(ile, rand);

        // Boîte englobante, étiquettes comprises : elles débordent des côtes et
        // seraient sinon coupées par le cadre.
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const ile of iles) {
            const e = etendueIle(ile);
            minX = Math.min(minX, e.minX);
            maxX = Math.max(maxX, e.maxX);
            minY = Math.min(minY, e.minY);
            maxY = Math.max(maxY, e.maxY);
        }
        const boite = {
            x: minX - MARGE, y: minY - MARGE - 30,
            w: maxX - minX + MARGE * 2, h: maxY - minY + MARGE * 2 + 30,
        };
        // Largeur plancher : sans elle, une carte à une seule île serait si
        // étroite que le cartouche de titre déborderait.
        if (boite.w < 620) {
            boite.x -= (620 - boite.w) / 2;
            boite.w = 620;
        }

        const svg = el('svg', {
            xmlns: SVG_NS,
            viewBox: `${boite.x.toFixed(0)} ${boite.y.toFixed(0)} ${boite.w.toFixed(0)} ${boite.h.toFixed(0)}`,
            width: Math.round(boite.w),
            height: Math.round(boite.h),
            class: 'tm-svg',
            role: 'img',
        });
        svg.setAttribute('aria-label', `Carte des clubs : ${clubs.map(c => c.nom).join(', ')}`);

        defs(svg);
        el('rect', { x: boite.x, y: boite.y, width: boite.w, height: boite.h, class: 'tm-mer' }, svg);
        el('rect', { x: boite.x, y: boite.y, width: boite.w, height: boite.h, fill: 'url(#houle)' }, svg);

        // Routes maritimes entre les îles, tracées avant elles pour passer dessous.
        const routes = el('g', { class: 'tm-routes' }, svg);
        for (let i = 1; i < iles.length; i++) {
            const a = iles[i - 1];
            const b = iles[i];
            const mx = (a.x + b.x) / 2 + (rand() - 0.5) * 90;
            const my = (a.y + b.y) / 2 + (rand() - 0.5) * 90;
            el('path', {
                d: `M${a.x.toFixed(0)} ${a.y.toFixed(0)} Q${mx.toFixed(0)} ${my.toFixed(0)} ${b.x.toFixed(0)} ${b.y.toFixed(0)}`,
                class: 'tm-route',
            }, routes);
        }

        // Emplacements du décor fixe, réservés avant de chercher où poser le reste.
        const titre = { x: boite.x + 24, y: boite.y + 20, w: 300, h: 60 };
        const rose = { x: boite.x + boite.w - 120, y: boite.y + 10, w: 116, h: 116 };
        const regle = { x: boite.x + 26, y: boite.y + boite.h - 60, w: 140, h: 56 };

        const vides = coinsVides(iles, boite, [titre, rose, regle], 2);
        if (vides[0]) monstreMarin(svg, vides[0].x, vides[0].y);
        if (vides[1]) croixDuTresor(svg, vides[1].x, vides[1].y);

        for (const ile of iles) dessinerIle(svg, ile);

        roseDesVents(svg, rose.x + 58, rose.y + 58, 34);
        cartouche(svg, titre.x, titre.y, 'les clubs de TELECOM Nancy');
        echelle(svg, regle.x, regle.y + 22);

        return svg;
    }

    global.drawTreasureMap = drawTreasureMap;
})(window);
