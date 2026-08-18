"""Garde-fou : aucun modèle ne doit s'exécuter localement.

C'est déjà arrivé par dépendance transitive (`pymupdf4llm>=1.27` tirait 118 Mo
de `.onnx`). Ces tests lisent `uv.lock`, qui décide du contenu de l'image.
"""

import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parent.parent
_LOCK = _RACINE / "uv.lock"
_PYPROJECT = _RACINE / "pyproject.toml"

# Paquets qui exécutent un modèle en local, ou qui en tirent un.
_INTERDITS = frozenset(
    {
        "torch",
        "torchvision",
        "torchaudio",
        "onnx",
        "onnxruntime",
        "onnxruntime-gpu",
        "transformers",
        "sentence-transformers",
        "tokenizers",
        "safetensors",
        "huggingface-hub",
        "pymupdf-layout",
        "fastembed",
        "tensorflow",
        "keras",
        "jax",
        "jaxlib",
        "scikit-learn",
        "spacy",
        "llama-cpp-python",
        "ctranslate2",
        "openvino",
        "accelerate",
        "diffusers",
        "timm",
        "vllm",
        "triton",
    }
)

# Tout paquet dont le nom commence par l'un de ces préfixes tire du CUDA.
_PREFIXES_GPU = ("nvidia-", "cuda-", "cudnn", "cublas")


def _paquets_du_lock() -> set[str]:
    """Noms de tous les paquets verrouillés, quel que soit le groupe."""
    return set(re.findall(r'^name = "([^"]+)"', _LOCK.read_text(), re.MULTILINE))


def test_aucun_paquet_de_modele_local_dans_le_lock() -> None:
    """Le lock ne verrouille aucune brique d'inférence locale."""
    presents = sorted(_paquets_du_lock() & _INTERDITS)
    assert not presents, (
        f"Paquets d'inférence locale dans uv.lock : {presents}. "
        "TN-GPT doit rester 100 % API — voir la docstring de ce fichier."
    )


def test_aucun_paquet_gpu_dans_le_lock() -> None:
    """Aucune dépendance CUDA : le serveur de production n'a pas de GPU."""
    gpu = sorted(p for p in _paquets_du_lock() if p.startswith(_PREFIXES_GPU))
    assert not gpu, f"Paquets GPU dans uv.lock : {gpu}."


def test_pymupdf4llm_reste_epingle_sous_la_1_0() -> None:
    """L'épinglage qui tient `onnxruntime` dehors ne doit pas sauter.

    Contrôle la contrainte déclarée, pas la version résolue : c'est elle qui
    survit — ou non — à un `uv lock --upgrade`.
    """
    deps = tomllib.loads(_PYPROJECT.read_text())["project"]["dependencies"]
    contrainte = next((d for d in deps if d.startswith("pymupdf4llm")), None)
    assert contrainte is not None, "pymupdf4llm a disparu des dépendances."
    assert "<1.0" in contrainte, (
        f"pymupdf4llm n'est plus borné sous la 1.0 ({contrainte!r}) : "
        "la 1.27+ retire pymupdf-layout, donc onnxruntime et ses modèles .onnx."
    )


def test_aucun_fichier_de_poids_versionne() -> None:
    """Aucun poids de modèle n'est commité dans le dépôt."""
    suffixes = {".onnx", ".safetensors", ".gguf", ".tflite", ".h5", ".ckpt", ".pt"}
    poids = [
        chemin.relative_to(_RACINE)
        for chemin in _RACINE.rglob("*")
        if chemin.suffix in suffixes
        and ".venv" not in chemin.parts
        and ".git" not in chemin.parts
        and "public" not in chemin.parts
    ]
    assert not poids, f"Fichiers de poids dans le dépôt : {poids}."


@pytest.mark.parametrize(
    "module", ["torch", "onnxruntime", "transformers", "sentence_transformers"]
)
def test_le_module_nest_pas_installe(module: str) -> None:
    """Filet de sécurité sur l'environnement courant, en plus du lock."""
    assert importlib.util.find_spec(module) is None, (
        f"{module} est installé dans cet environnement."
    )
