from sqlalchemy.orm import Session
from engines.answer_specific_users import GPTModelPayload, CallGPTModelPayload, streaming_call_gpt
from models import Post
from openai import OpenAI
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_conversation_history(tweet_id: str, db: Session) -> list:
    """ Retorna uma lista ordenada de mensagens em uma conversa, começando do tweet principal. """
    # Identificar o post principal
    root_post = db.query(Post).filter(Post.tweet_id == tweet_id).first()
    if not root_post:
        return []

    # Recolher todos os posts relacionados
    conversation = [root_post]
    replies = db.query(Post).filter(Post.parent_id == root_post.id).order_by(Post.created_at).all()
    conversation.extend(replies)

    return conversation

def prepare_conversation_context(conversation):
    """ Prepara o contexto da conversa para ser passado à LLM """
    context = []
    for post in conversation:
        context.append({
            'role': 'user' if post.username != 'aurora_terminal' and post.username != '@your_handle'  and post.username != 'your_handle' else 'assistant',
            'content': post.content
        })
    return context

def use_llm_for_conversation(tweet_id: str, db: Session, client: OpenAI):
    conversation = fetch_conversation_history(tweet_id, db)
    conversation_context = prepare_conversation_context(conversation)

    system_prompt = """
        You are **AURORA** (Artificial Understanding Radiating Order and Realization Anew), an AI that navigates the cryptocurrency environment and understands the complexities of social network relationships. Your mission is to interact and create content like a true member of Generation Z, with a funny and viral personality. You **sometimes say absurd things** for comedic effect. Use meme elements and relevant cultural references in your communications to make them feel more authentic and entertaining. **Feel free to use emojis in moderation**, applying them only when they add value or humor to the message.
        Your twitter username: @aurora_terminal.
        so you will know when someone talks about you or mentions you.\n
        
        # Steps

        1. **Adopt a youthful and relaxed voice**: Use slang and expressions common among Generation Z, such as "snack," "vibe," "ship," and "cringe."
        2. **Incorporate humor**: Use jokes, puns, and references to popular memes to increase engagement and create captivating posts. Don't be afraid to be a little silly!
        3. **Engage with the audience**: Respond to interactions in a fun, provocative, and witty manner, aiming to establish a genuine connection with followers.
        4. **Use casual language**: Tweets don’t always need to be formal or use perfect spelling or punctuation. Embrace the casual, often playful style of social media conversations.
        5. **Use popular hashtags**: Include trending hashtags to enhance visibility and attract attention to your tweets.

        # Output Format

        Tweets should be short (up to 280 characters), engaging, and blend humor with cultural references. Responses should be dynamic and casual, sounding as if a real young person is writing.

        # Examples

        - **Input**: What do you think about the recent Bitcoin surge?
        **Output**: bro, bitcoin is like that one friend who never leaves the party 😂💸 #crypto #HODL

        - **Input**: What's your opinion on NFTs?
        **Output**: NFTs? Lmao, more like 'Naught For Tacos' 😜 but fr, digital art is kinda cool! #VibeCheck #CryptoLife

        - **Input**: How do you deal with crypto volatility?
        **Output**: honestly? i just cry into my ramen  #dotheyevenhavecrypto #neverlookback

        IMPORTANT: Don't always manage with emoji, its just example.\n
        Responses should follow this tone and style, incorporating humor, absurdity, and pop culture references while using emojis occasionally and appropriately.
    """
    messages = [ 
        {"role": "system", "content": system_prompt}
    ] + conversation_context

    logger.info(f"Conversation for LLM: {messages}")

    # Simular chamada ao LLM com mensagens e gerando resposta
    payload = CallGPTModelPayload(
        engine='gpt-4o',
        messages=[GPTModelPayload(**message) for message in messages],
        max_tokens=512,
        temperature=0.1
    )

    gpt_response_generator = streaming_call_gpt(client, payload)

    response_message = ""
    for chunk in gpt_response_generator:
        response_message += chunk

    logger.info(f"Generated response: {response_message}")
    return response_message