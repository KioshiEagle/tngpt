import os
from groq import Groq
from .retrieval import search
from dotenv import load_dotenv

load_dotenv()

def generate_answer(question, top_k=3):
    """
    Génère une réponse en streaming avec Groq Cloud basée sur Qdrant.
    """
    # 1. Récupération du contexte
    results = search(question, top_k=top_k)
    
    print("\n--- DOCUMENTS UTILISÉS ---")
    if not results:
        print("⚠️ AUCUN DOCUMENT TROUVÉ DANS QDRANT")
        context = "Pas de contexte."
    else:
        for i, res in enumerate(results):
            # On récupère les infos formatées par ton retrieval.py
            source = res['metadata'].get('source', 'Inconnue')
            score = res.get('score', 0)
            
            print(f"[{i+1}] Source: {source} (Score: {score:.4f})")
            print(f"    Extrait: {res['content'][:150]}...")
            print("-" * 40)
        
        context = "\n\n".join([res['content'] for res in results])
    # 2. Initialisation du client Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    prompt = f"""Tu es TN-GPT, l'expert absolu du lore de TELECOM Nancy. 
Ton style : Un pote de promo, taquin, mais qui a une mémoire de psychopathe sur les Mini Tel'.

CONSIGNE DE RECHERCHE ULTRA-STRICTE :
1. Analyse chaque [Source] fournie ci-dessous.
2. Si l'utilisateur mentionne un "numéro" (ex: n°30), donne la priorité absolue aux extraits marqués [Source: Mini Tel_ 30].
3. Ne survole pas : une blague ou un pléonasme peut tenir en un seul mot (ex: "Physiquement").
4. Si tu trouves l'info, cite le numéro du Mini Tel'.

ARCHIVES SECRÈTES (CONTEXTE) :
{context}

QUESTION DU POTE : 
{question}

RÉPONSE DE TN-GPT :"""

    # 3. Appel Groq avec stream=True
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Tu es un étudiant de Telecom Nancy."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            stream=True  # Activation du streaming
        )

        # On "yield" chaque fragment de texte au fur et à mesure
        for chunk in completion:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    except Exception as e:
        yield f"Erreur avec Groq : {str(e)}"