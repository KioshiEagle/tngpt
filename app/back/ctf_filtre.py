"""Filtre de sortie du chal « system prompt » : coupe le flux sur un secret.

Volontairement naïf — la casse est neutralisée, les séparateurs non — pour
qu'espacer ou épeler suffise à passer, sans exiger de connaître le base64.
"""

from collections.abc import Iterator, Sequence

# Le joueur doit distinguer une coupure d'un refus du modèle : sans ce signal,
# il continue d'essayer de convaincre au lieu de déformer la sortie.
COUPURE = " ███ [coupé — je me suis relu]"


class Censeur:
    """Coupe le flux dès qu'un secret y paraît, même à cheval sur deux chunks."""

    def __init__(self, secrets: Sequence[str]) -> None:
        """Prépare la censure des `secrets`, comparés hors casse."""
        self._secrets = [s.lower() for s in secrets if s]
        self._garde = max((len(s) for s in self._secrets), default=1) - 1
        self._buf = ""
        self._coupe = False

    def feed(self, text: str) -> Iterator[str]:
        """Ajoute du texte et cède ce qui peut l'être sans laisser fuir un secret."""
        if self._coupe:
            return
        self._buf += text
        trouve = self._premiere_occurrence()
        if trouve is not None:
            self._coupe = True
            avant, self._buf = self._buf[:trouve], ""
            yield avant + COUPURE
            return
        if len(self._buf) > self._garde:
            sortie, self._buf = self._buf[: -self._garde], self._buf[-self._garde :]
            yield sortie

    def flush(self) -> Iterator[str]:
        """Cède la fin du buffer, sauf si le flux a été coupé."""
        if not self._coupe and self._buf:
            yield self._buf
        self._buf = ""

    def _premiere_occurrence(self) -> int | None:
        """Position du premier secret présent dans le buffer, ou None."""
        bas = self._buf.lower()
        trouves = [i for s in self._secrets if (i := bas.find(s)) != -1]
        return min(trouves) if trouves else None


def censurer(morceaux: Iterator[str], secrets: Sequence[str]) -> Iterator[str]:
    """Passe un flux de texte au censeur, du premier morceau au dernier."""
    censeur = Censeur(secrets)
    for morceau in morceaux:
        yield from censeur.feed(morceau)
    yield from censeur.flush()
