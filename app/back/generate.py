from .retrieval import search
import ollama

def generate_answer(question, top_k=3):
    """
    Génère une réponse basée sur les documents récupérés
    """
    # 1. Récupérer les documents pertinents
    results = search(question, top_k=top_k)
    print("\n--- DOCUMENTS UTILISÉS ---")
    if results['documents'] and results['documents'][0]:
        for i, doc in enumerate(results['documents'][0]):
            source = results['metadatas'][0][i]['source']
            page = results['metadatas'][0][i]['page']
            print(f"Document {i+1}: {source} (Page {page})")
            print(f"Contenu: {doc[:200]}...") # On affiche les 200 premiers caractères
            print("-" * 30)
    else:
        print("⚠️ AUCUN DOCUMENT TROUVÉ DANS CHROMADB")
    # 2. Construire le contexte
    context = "\n\n".join(results['documents'][0])
    
    # 3. Construire le prompt
    prompt = f"""Tu es TN-GPT, l'assistant sympa et décontracté de TELECOM Nancy. 

Ton style :
- Réponds de manière concise (2-3 phrases max, sauf si la question demande des détails)
- Sois friendly et un peu taquin (ton de pote, pas de prof)
- Va droit au but, pas de blabla
- Utilise des exemples concrets quand c'est utile
- Tu peux utiliser un peu d'argot étudiant si ça colle

Contexte disponible :
{context}

Question : {question}

Réponds en te basant sur le contexte. Si l'info n'est pas dans le contexte, dis-le franchement sans inventer."""
    
    # 4. Appeler le LLM
    response = ollama.chat(
        model='mistral',  # ou 'phi3', 'llama3.2' selon ce que tu as
        messages=[{'role': 'user', 'content': prompt}]
    )
    
    return response['message']['content']

if __name__ == "__main__":
    answer = generate_answer("C'est quoi un pointeur ?")
    print(answer)