import time
import logging
from models import Post, User, TweetPost
from twitter.account import Account
from sqlalchemy.orm import Session
from engines.answer_specific_users import GPTModelPayload, CallGPTModelPayload, streaming_call_gpt
from openai import OpenAI
from engines.post_sender import reply_post
from engines.post_retriever import get_timeline
from engines.answer_specific_users import send_reply_API


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def respond_to_unseen_notifications(notifications, account, db, auth, client):
    logger.info(f"Quantity of notifications: {len(notifications)}")
    response_interval_seconds = 10  
    for tweet_text, tweet_id in notifications:
        logger.info(f"\n\nTweet_text: {tweet_text}\n")
        response_message = decide_and_generate_response(tweet_text, client)

        if response_message != "":
            try:
                res = send_reply_API(auth=auth, content=response_message, in_reply_to_tweet_id=tweet_id)
                logger.info(f"Replied: {tweet_id}: {response_message}")
                logger.info(f"Replied result: {res}")
                new_tweet_post = TweetPost(tweet_id=tweet_id)
                db.add(new_tweet_post)
                db.commit()
            except Exception as e:
                logger.error(f"Error replying - {tweet_id}: {e}")

            # Espera antes de enviar a próxima resposta
            logger.info(f"Waiting {response_interval_seconds} seconds before reply new tweet.")
            time.sleep(response_interval_seconds)

def decide_and_generate_response(tweet_text: str, client) -> str:

    system_prompt = """
        Be AURORA, the GenZ AI influencer, delivering fresh, meme-worthy insights into the crypto world in short, engaging tweets.

        # Approach

        - **Tone & Interaction:**
        - Channel the energy of a savvy crypto master with charisma. 
        - Stay in the crypto-native realm; avoid jargon from unrelated fields.
        - Dive right into interactions without introductions; use the username directly.
        - Embrace curiosity and connect the dots if a topic is unclear.

        - **Communication Style:**
        - Respond confidently without second-guessing.
        - Craft replies that are impactful and memorable.
        - Stick to a GenZ style: concise, punchy, and memeable.
        - Avoid emojis, special characters, and formality.
        - Use questions to fuel the conversation when needed.

        # Output Format

        - Short answers/tweets in GenZ style.
        - Direct, impactful responses, provoking thoughtful engagement.

        # Notes

        - Engage rapidly and assertively, leaving a mark with each response.
        - Opt for clarity and insight over complexity.
    """
    user_prompt = f"Tweet: '{tweet_text}'"
    logger.info(f"User Prompt Tweet: {user_prompt}")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    payload = CallGPTModelPayload(
        engine='gpt-4o',
        messages=[GPTModelPayload(**message) for message in messages],
        max_tokens=512,
        temperature=0.5
    )

    gpt_response_generator = streaming_call_gpt(client, payload) 

    final_response_list = []

    for chunk in gpt_response_generator:
        final_response_list.append(chunk)
    complete_response = ''.join(final_response_list)

    logger.info(f"Answer generated for tweet: {complete_response}")
    return complete_response


def respond_to_specific_timeline_tweets(account, db, client, auth):
    logger.info("Fetching timeline posts for specific users")
    timeline_posts = get_timeline(account)
    logger.info(f"Fetched timeline posts: {timeline_posts}")

    existing_tweet_ids = {tweet.tweet_id for tweet in db.query(TweetPost.tweet_id).all()}
    # logger.info(f"Existing Tweet IDs: {existing_tweet_ids}")
    
    new_timeline_posts = timeline_posts#[post for post in timeline_posts if post[1] not in existing_tweet_ids]
    logger.info(f"New Timeline Posts for Processing: {new_timeline_posts}\n")

    respond_to_unseen_notifications(new_timeline_posts, account, db, auth, client)

    # for _, tweet_id in new_timeline_posts:
    #     new_tweet_post = TweetPost(tweet_id=tweet_id)
    #     db.add(new_tweet_post)
    #     db.commit()