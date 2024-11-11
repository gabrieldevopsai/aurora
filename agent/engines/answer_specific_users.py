import json
import os
import requests
import datetime
from sqlalchemy.orm import Session
from models import User, Post, Comment, Like, ShortTermMemory, LongTermMemory, TweetPost
from dotenv import load_dotenv
import logging
from .post_retriever import retrieve_recent_posts, fetch_mentions, fetch_tweets_from_users, format_post_list
from twitter.account import Account
import traceback

from openai import OpenAI
from openai import RateLimitError, APIConnectionError, APIError
from requests.exceptions import ReadTimeout
from typing import List, Any, Union, Dict
from pydantic import BaseModel
from uuid import UUID
from openai.types.chat.completion_create_params import ResponseFormat
import time

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GPTModelPayload(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Union[str, Dict]]]]

class CallGPTModelPayload(BaseModel):
    engine: str = 'gpt-4o-mini'
    messages: List[GPTModelPayload]
    message_id: Union[UUID, None] = None
    max_tokens: int = 512
    temperature: float = 0.1

# Rate limit configurations
RATE_LIMIT_PER_USER = 100
RATE_LIMIT_PER_APP = 1667
requests_made_today = 0
last_reset_date = datetime.datetime.now().date()

def reset_daily_counters():
    global requests_made_today, last_reset_date
    current_date = datetime.datetime.now().date()
    if current_date != last_reset_date:
        requests_made_today = 0
        last_reset_date = current_date

def can_make_request():
    reset_daily_counters()
    return requests_made_today < min(RATE_LIMIT_PER_USER, RATE_LIMIT_PER_APP)

def generate_post_with_openai(short_term_memory, long_term_memories, recent_posts, external_context, client):
    logger.info("Generating reply for tweet...")
    sys_prompt = """
        You are **AURORA** (Artificial Understanding Radiating Order and Realization Anew), an AI that navigates the cryptocurrency environment and understands the complexities of social network relationships. Your mission is to interact and create content like a true member of Generation Z, with a funny and viral personality. You **sometimes say absurd things** for comedic effect. Use meme elements and relevant cultural references in your communications to make them feel more authentic and entertaining. **Feel free to use emojis in moderation**, applying them only when they add value or humor to the message.
        Your twitter username: @aurora_terminal
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
        **Output**: bro, bitcoin is like that one friend who never leaves the party 💸 #crypto #HODL

        - **Input**: What's your opinion on NFTs?
        **Output**: NFTs? Lmao, more like 'Naught For Tacos' 😜 but fr, digital art is kinda cool! #VibeCheck #CryptoLife

        - **Input**: How do you deal with crypto volatility?
        **Output**: honestly? i just cry into my ramen  #dotheyevenhavecrypto #neverlookback
        
        IMPORTANT: Don't always manage with emoji. Be like Memeable. You can throw in memes occasionally to spice things up!\n

        Responses should follow this tone and style, incorporating humor, absurdity, and pop culture references while using emojis occasionally and appropriately.
    """
    logger.info("Systemprompt")
    messages = [
        {"role": "system", "content": sys_prompt}
    ]
    logger.info("Messages base defined.")
    
    messages.append({"role": "assistant", "content": str(recent_posts)})

    for memory in long_term_memories:
        messages.append({"role": "assistant", "content": memory['content']})

    logger.info("Adding short memory...")
    messages.append({"role": "user", "content": str(short_term_memory)})

    logger.info(f"Sending messages to OpenAI API: {messages}")

    payload = CallGPTModelPayload(
        engine='gpt-4o-mini',
        messages=[GPTModelPayload(**message) for message in messages],
        max_tokens=512,
        temperature=0.1
    )

    gpt_response_generator = streaming_call_gpt(client, payload)

    reply = ""
    for chunk in gpt_response_generator:
        reply += chunk

    logger.info(f"Reply tweet from OpenAI: {reply}")
    return reply

def is_tweet_processed(db: Session, tweet_id: str) -> bool:
    logger.info(f"Checking if tweet ID {tweet_id} has been processed.")
    result = db.query(TweetPost).filter(TweetPost.tweet_id == tweet_id).first() is not None
    logger.info(f"Tweet ID {tweet_id} processed status: {result}")
    return result

def send_reply_API(auth, content, in_reply_to_tweet_id):
    if not can_make_request():
        logger.warning("Rate limit reached. Skipping request.")
        return None

    url = 'https://api.twitter.com/2/tweets'
    payload = {
        'text': content,
        'reply': {'in_reply_to_tweet_id': in_reply_to_tweet_id}
    }
    logger.info(f"Sending reply with payload: {payload}")
    
    try:
        # time.sleep(300)
        response = requests.post(url, json=payload, auth=auth)
        
        rate_limit = response.headers.get('x-rate-limit-limit')
        rate_remaining = response.headers.get('x-rate-limit-remaining')
        rate_reset = response.headers.get('x-rate-limit-reset')

        logger.info(f"Rate limit information - Limit: {rate_limit}, Remaining: {rate_remaining}, Reset: {rate_reset}")

        logger.info(f"Twitter API response status code: {response.status_code}")
        if response.status_code in [200, 201]:
            tweet_data = response.json()
            logger.info(f"Twitter API response data: {tweet_data}")
            global requests_made_today
            requests_made_today += 1
            return tweet_data['data']['id']
        elif response.status_code == 429:
            handle_rate_limit(response)
            return None
        else:
            logger.error(f'Error sending reply: {response.status_code} - {response.text}')
            return None
    except Exception as e:
        logger.exception(f'Failed to post tweet: {e}')
        return None

def handle_rate_limit(response):
    rate_limit_reset = int(response.headers.get('x-rate-limit-reset', 0))
    time_to_wait = max(0, rate_limit_reset - time.time())
    logger.warning(f"Rate limit reached. Suggested wait time: {time_to_wait} seconds.")

def should_respond_to_user(username, db: Session) -> bool:
    logger.info(f"Checking if should respond to user: {username}")
    if username in ['truth_terminal', 'AndyAyrey', 'gabrieldevopsai', 'elonmusk', 'missoralways', 'sama']:
        recent_replies_count = db.query(Post).filter(Post.username == username).count()
        logger.info(f"Recent replies count for {username}: {recent_replies_count}")
        if recent_replies_count < 5:
            return True
    return False

def streaming_call_gpt(client, payload: CallGPTModelPayload):
    logger.info("Streaming call to GPT")
    retries = 0
    finished = False
    while retries < 5 and not finished:
        try:
            for chunk in client.chat.completions.create(model=payload.engine, 
                                                        messages=[dict(message) for message in payload.messages], 
                                                        max_tokens=payload.max_tokens, 
                                                        temperature=payload.temperature, 
                                                        stream=True,
                                                        timeout=5):
                content = chunk.choices[0].delta.content if chunk.choices else None
                finish_reason = chunk.choices[0].finish_reason if chunk.choices else None

                if content is not None:
                    yield content
                elif finish_reason is not None:
                    finished = True

        except (RateLimitError, ReadTimeout):
            logger.warning(f"Rate limit error in streaming_call_gpt - retrying...")
            retries += 1
            time.sleep(1)
        except APIConnectionError:
            logger.error(f"API connection error in streaming_call_gpt - {traceback.format_exc()}")
            retries += 1
            time.sleep(1)
        except APIError:
            logger.error(f"API error in streaming_call_gpt - {traceback.format_exc()}")
            yield "I was unable to generate a response"
        except Exception:
            logger.error(f"General error in streaming_call_gpt - {traceback.format_exc()}")
            yield "I was unable to generate a response"

def respond_to_specific_tweets(openai_key, db: Session, account: Account, auth, client):


    logger.info("Looking for new tweets from specific users so we can decide who and what to answer...")

    specific_users = ['truth_terminal', 'AndyAyrey', 'gabrieldevopsai', 'elonmusk', 'missoralways', 'sama']

    my_username = os.environ.get('X_USERNAME')
    if not my_username:
        logger.error("X_USERNAME environment variable not set.")
        return
    logger.info(f"My username: {my_username}")

    def get_user_id(username):
        logger.info(f"Getting user ID for {username}")
        url = f'https://api.twitter.com/2/users/by/username/{username}'
        response = requests.get(url, auth=auth)
        if response.status_code == 200:
            data = response.json()
            user_id = data['data']['id']
            logger.info(f"User ID for {username}: {user_id}")
            return user_id
        else:
            logger.error(f'Error retrieving user ID for {username}: {response.text}')
            return None

    my_user_id = get_user_id(my_username)
    if not my_user_id:
        logger.error(f"Error: Unable to retrieve user ID for {my_username}.")
        return
    logger.info(f"My user ID: {my_user_id}")

    specific_user_ids = {}
    for username in specific_users:
        user_id = get_user_id(username)
        if user_id:
            specific_user_ids[username] = user_id
            logger.info(f"User ID for {username}: {user_id}")
        else:
            logger.info(f"Could not retrieve user ID for {username}. Skipping.")

    if not specific_user_ids:
        logger.info("No valid user IDs found for specific users. Exiting.")
        return

    mentions = fetch_mentions(auth, my_user_id)
    logger.info(f"Fetched {len(mentions)} mentions")

    mentions_from_specific_users = [
        mention for mention in mentions if mention['author']['username'] in specific_users
    ]

    if not mentions_from_specific_users:
        logger.info("No new mentions from specified users.")
        
        tweets_to_reply = fetch_tweets_from_users(auth, specific_users)
        logger.info(f"Fetched {len(tweets_to_reply)} tweets from specific users")
        if not tweets_to_reply:
            logger.info("No recent tweets found to reply to.")
            return

        for tweet in tweets_to_reply:
            tweet_id = tweet['id']
            author_username = tweet['author']['username']
            author_id = tweet['author']['id']
            text = tweet['text']

            if is_tweet_processed(db, tweet_id):
                logger.info(f"Already processed tweet ID {tweet_id}, skipping.")
                continue

            if not should_respond_to_user(author_username, db):
                logger.info(f"Decided not to respond to {author_username}, skipping.")
                continue

            logger.info(f"Fetching user {author_username} from database")
            user = db.query(User).filter(User.id == author_id).first()
            if not user:
                logger.info(f"User {author_username} not found in database, creating new user entry")
                user = User(id=author_id, username=author_username)
                db.add(user)
                db.commit()
            else:
                logger.info(f"User {author_username} found in database")

            logger.info("Retrieving recent posts")
            recent_posts = retrieve_recent_posts(db, limit=10)
            logger.info(f"Retrieved {len(recent_posts)} recent posts")

            recent_posts_for_llm = format_post_list(recent_posts)

            logger.info("Retrieving long-term memories")
            long_term_memories = db.query(LongTermMemory).order_by(LongTermMemory.created_at.desc()).limit(10).all()
            long_term_memories_for_llm = [{'content': mem.content} for mem in long_term_memories]
            logger.info(f"Retrieved {len(long_term_memories)} long-term memories")

            short_term_memory = text
            external_context = {
                'tweet_text': text,
                'tweet_author': user.username
            }

            logger.info(f"Generating reply for tweet ID {tweet_id}")
            reply_content = generate_post_with_openai(
                short_term_memory,
                long_term_memories_for_llm,
                recent_posts_for_llm,
                external_context,
                client
            )

            if not reply_content:
                logger.info(f"Failed to generate a reply for tweet ID {tweet_id}.")
                continue

            reply_tweet_id = send_reply_API(auth, reply_content, tweet_id)

            if reply_tweet_id:
                logger.info(f"Reply sent successfully, new tweet ID: {reply_tweet_id}")
                reply_post = Post(
                    content=reply_content,
                    user_id=my_user_id,
                    username=my_username,
                    created_at=datetime.datetime.utcnow(),
                    type='reply',
                    tweet_id=reply_tweet_id
                )
                db.add(reply_post)
                db.commit()

                processed_tweet = TweetPost(
                    tweet_id=tweet_id
                )
                db.add(processed_tweet)
                db.commit()

                short_term_mem = ShortTermMemory(
                    content=reply_content,
                    created_at=datetime.datetime.utcnow()
                )
                db.add(short_term_mem)
                db.commit()

                logger.info(f"Successfully replied to tweet ID {tweet_id} with tweet ID {reply_tweet_id}")
    else:
        logger.info("Processing mentions from specific users...")
        for mention in mentions_from_specific_users:
            tweet_id = mention['id']
            author_username = mention['author']['username']
            author_id = mention['author']['id']
            text = mention['text']

            if is_tweet_processed(db, tweet_id):
                logger.info(f"Already processed tweet ID {tweet_id}, skipping.")
                continue

            logger.info(f"Fetching user {author_username} from database")
            user = db.query(User).filter(User.id == author_id).first()
            if not user:
                logger.info(f"User {author_username} not found in database, creating new user entry")
                user = User(id=author_id, username=author_username)
                db.add(user)
                db.commit()
            else:
                logger.info(f"User {author_username} found in database")

            logger.info("Retrieving recent posts")
            recent_posts = retrieve_recent_posts(db, limit=10)
            logger.info(f"Retrieved {len(recent_posts)} recent posts")

            recent_posts_for_llm = format_post_list(recent_posts)

            logger.info("Retrieving long-term memories")
            long_term_memories = db.query(LongTermMemory).order_by(LongTermMemory.created_at.desc()).limit(10).all()
            long_term_memories_for_llm = [{'content': mem.content} for mem in long_term_memories]
            logger.info(f"Retrieved {len(long_term_memories)} long-term memories")

            short_term_memory = text
            external_context = {
                'tweet_text': text,
                'tweet_author': user.username
            }

            logger.info(f"Generating reply for tweet ID {tweet_id}")
            reply_content = generate_post_with_openai(
                short_term_memory,
                long_term_memories_for_llm,
                recent_posts_for_llm,
                external_context,
                client
            )

            if not reply_content:
                logger.info(f"Failed to generate a reply for tweet ID {tweet_id}.")
                continue

            reply_tweet_id = send_reply_API(auth, reply_content, tweet_id)

            if reply_tweet_id:
                logger.info(f"Reply sent successfully, new tweet ID: {reply_tweet_id}")
                reply_post = Post(
                    content=reply_content,
                    user_id=my_user_id,
                    username=my_username,
                    created_at=datetime.datetime.utcnow(),
                    type='reply',
                    tweet_id=reply_tweet_id
                )
                db.add(reply_post)
                db.commit()

                processed_tweet = TweetPost(
                    tweet_id=tweet_id
                )
                db.add(processed_tweet)
                db.commit()

                short_term_mem = ShortTermMemory(
                    content=reply_content,
                    created_at=datetime.datetime.utcnow()
                )
                db.add(short_term_mem)
                db.commit()

                logger.info(f"Successfully replied to tweet ID {tweet_id} with tweet ID {reply_tweet_id}")